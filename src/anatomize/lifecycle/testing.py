"""Static test intent and separately sourced runtime-context evidence."""

from __future__ import annotations

import ast
import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Literal

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from pydantic import Field, model_validator

from anatomize._artifacts import BoundedJsonError, JsonLimits, content_id, parse_bounded_json_object, sha256_digest
from anatomize._errors import AnatomizeError
from anatomize.evidence import EvidenceModel, EvidenceStrength, RepositoryEvidence, validate_repository_path
from anatomize.providers.models import ProviderEnvelope

TEST_INTENT_ARTIFACT_TYPE: Literal["anatomize.test-intent"] = "anatomize.test-intent"
TEST_INTENT_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
TEST_RUNTIME_ARTIFACT_TYPE: Literal["anatomize.test-runtime"] = "anatomize.test-runtime"
TEST_RUNTIME_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class TestEvidenceError(AnatomizeError):
    """Stable failure while extracting or importing test evidence."""


class TestEvidenceKind(str, Enum):
    STATIC_EXERCISE = "static_exercise"
    RUNTIME_EXECUTION = "runtime_execution"
    LINE_COVERAGE = "line_coverage"
    BRANCH_COVERAGE = "branch_coverage"
    MUTATION_DETECTION = "mutation_detection"
    FAILURE = "failure"
    SKIP = "skip"
    ABSENCE = "absence"


class TestLocator(EvidenceModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_locator(self) -> TestLocator:
        validate_repository_path(self.path)
        if self.end_line < self.start_line:
            raise ValueError("test locator end must not precede start")
        return self


class TestIntent(EvidenceModel):
    intent_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    framework: str
    suite: str | None = None
    name: str
    locator: TestLocator
    fixtures: list[str] = Field(default_factory=list)
    parameterizations: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    expected_exceptions: list[str] = Field(default_factory=list)
    snapshots: list[str] = Field(default_factory=list)
    marks: list[str] = Field(default_factory=list)
    setup: list[str] = Field(default_factory=list)
    generated_cases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_intent(self) -> TestIntent:
        for name in (
            "fixtures",
            "parameterizations",
            "calls",
            "targets",
            "assertions",
            "expected_exceptions",
            "snapshots",
            "marks",
            "setup",
            "generated_cases",
        ):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"test intent {name} must be unique")
        if self.intent_id != _intent_id(self):
            raise ValueError("test intent identifier does not match its source identity")
        return self


class TestIntentArtifact(EvidenceModel):
    artifact_type: Literal["anatomize.test-intent"] = TEST_INTENT_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = TEST_INTENT_SCHEMA_VERSION
    repository_id: str
    source_state_id: str
    intents: list[TestIntent] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RuntimeTestObservation(EvidenceModel):
    observation_id: str
    kind: TestEvidenceKind
    test_name: str | None = None
    subject_identity: str | None = None
    locator: TestLocator | None = None
    outcome: str
    metrics: dict[str, str | int | float | bool] = Field(default_factory=dict)
    failure_provenance: str | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> RuntimeTestObservation:
        if self.observation_id != content_id(
            "test-runtime-observation",
            self.model_dump(mode="json", exclude={"observation_id"}),
        ):
            raise ValueError("runtime test observation identifier does not match its content")
        return self


class TestRuntimeArtifact(EvidenceModel):
    artifact_type: Literal["anatomize.test-runtime"] = TEST_RUNTIME_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = TEST_RUNTIME_SCHEMA_VERSION
    repository_id: str
    source_state_id: str
    provider_run_id: str
    provider_id: str
    provider_version: str
    environment_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selection: list[str] = Field(default_factory=list)
    completeness: Literal["complete", "partial", "unavailable", "unknown"]
    observations: list[RuntimeTestObservation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class TestIntentComparison(EvidenceModel):
    left_intent_id: str
    right_intent_id: str
    shared: dict[str, list[str]]
    divergent: dict[str, dict[str, list[str]]]
    authority: Literal["evidence_only"] = "evidence_only"


def extract_python_test_intent(
    source: str,
    *,
    repository_id: str,
    source_state_id: str,
    path: str,
) -> TestIntentArtifact:
    """Extract bounded pytest/unittest intent signals without executing tests."""
    validate_repository_path(path)
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise TestEvidenceError(
            "python_test_syntax_invalid",
            f"Cannot parse test source at line {error.lineno}",
            remediation="Fix syntax or attach a language-specific provider artifact.",
        ) from error
    intents: list[TestIntent] = []
    for suite, function in _test_functions(tree):
        fixtures = sorted(argument.arg for argument in function.args.args if argument.arg not in {"self", "cls"})
        decorators = [_call_name(item) for item in function.decorator_list]
        calls = sorted({_call_name(item) for item in ast.walk(function) if isinstance(item, ast.Call)})
        assertions = sorted(
            {_source_segment(source, item) for item in ast.walk(function) if isinstance(item, ast.Assert)}
        )
        exceptions = sorted(
            {
                _call_name(item.context_expr.args[0])
                for node in ast.walk(function)
                if isinstance(node, ast.With)
                for item in node.items
                if isinstance(item.context_expr, ast.Call)
                and _call_name(item.context_expr).endswith("raises")
                and item.context_expr.args
            }
        )
        parameterizations = sorted(value for value in decorators if "parametrize" in value)
        marks = sorted(value for value in decorators if "mark" in value and "parametrize" not in value)
        targets = sorted(value for value in calls if not value.startswith(("pytest.", "unittest.")))
        snapshots = sorted(value for value in calls if "snapshot" in value.casefold())
        setup = sorted(value for value in calls if value.casefold().startswith(("setup", "teardown")))
        generated = parameterizations
        values = {
            "source_state_id": source_state_id,
            "framework": "pytest" if any(value.startswith("pytest") for value in [*decorators, *calls]) else "python",
            "suite": suite,
            "name": function.name,
            "locator": TestLocator(
                path=path,
                start_line=function.lineno,
                end_line=function.end_lineno or function.lineno,
            ),
            "fixtures": fixtures,
            "parameterizations": parameterizations,
            "calls": calls,
            "targets": targets,
            "assertions": assertions,
            "expected_exceptions": exceptions,
            "snapshots": snapshots,
            "marks": marks,
            "setup": setup,
            "generated_cases": generated,
        }
        intents.append(_build_test_intent(**values))
    return TestIntentArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        intents=sorted(intents, key=lambda item: item.intent_id),
        limitations=[
            "Static calls and assertions describe syntax, not successful execution or complete behavioral coverage."
        ],
    )


def extract_r_test_intent(
    source: str,
    *,
    repository_id: str,
    source_state_id: str,
    path: str,
) -> TestIntentArtifact:
    """Extract conservative testthat intent from source without evaluating R."""
    validate_repository_path(path)
    intents: list[TestIntent] = []
    lines = source.splitlines()
    for start, end, name, body in _r_test_blocks(lines):
        calls = sorted(set(re.findall(r"\b([A-Za-z.][\w.]*)\s*\(", body)))
        assertions = sorted(value for value in calls if value.startswith("expect_"))
        expected_exceptions = sorted(
            value for value in assertions if value in {"expect_error", "expect_warning", "expect_message"}
        )
        fixtures = sorted(value for value in calls if value.startswith(("fixture_", "local_")))
        parameterizations = ["for"] if re.search(r"\bfor\s*\(", body) else []
        snapshots = sorted(value for value in calls if "snapshot" in value.casefold())
        setup = sorted(value for value in calls if value.startswith(("setup", "teardown", "withr.")))
        targets = sorted(
            value
            for value in calls
            if value
            not in {
                "test_that",
                *assertions,
                *fixtures,
                *setup,
            }
        )
        values = {
            "source_state_id": source_state_id,
            "framework": "testthat",
            "suite": None,
            "name": name,
            "locator": TestLocator(path=path, start_line=start, end_line=end),
            "fixtures": fixtures,
            "parameterizations": parameterizations,
            "calls": calls,
            "targets": targets,
            "assertions": assertions,
            "expected_exceptions": expected_exceptions,
            "snapshots": snapshots,
            "marks": [],
            "setup": setup,
            "generated_cases": parameterizations,
        }
        intents.append(_build_test_intent(**values))
    return TestIntentArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        intents=sorted(intents, key=lambda item: item.intent_id),
        limitations=[
            "Static R calls describe syntax; dynamic dispatch and runtime behavior require provider evidence."
        ],
    )


def parse_junit_xml(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
    environment_digest: str,
    selection: list[str],
    max_bytes: int = 32 * 1024 * 1024,
) -> TestRuntimeArtifact:
    """Parse bounded JUnit XML while rejecting DTD/entity declarations."""
    _check_bytes(raw, max_bytes, "JUnit")
    try:
        root = ET.fromstring(raw)
    except DefusedXmlException as error:
        raise TestEvidenceError(
            "junit_unsafe_xml",
            "JUnit XML contains a forbidden DTD or entity declaration",
            remediation="Regenerate plain JUnit XML without external entities.",
        ) from error
    except ET.ParseError as error:
        raise TestEvidenceError(
            "junit_invalid",
            "JUnit XML is malformed",
            remediation="Regenerate the test result artifact.",
        ) from error
    observations = []
    for case in root.iter("testcase"):
        name = case.get("name") or "unknown-test"
        classname = case.get("classname")
        failure = next(iter(case.findall("failure") + case.findall("error")), None)
        skipped = next(iter(case.findall("skipped")), None)
        kind = TestEvidenceKind.FAILURE if failure is not None else (
            TestEvidenceKind.SKIP if skipped is not None else TestEvidenceKind.RUNTIME_EXECUTION
        )
        outcome = "failed" if failure is not None else ("skipped" if skipped is not None else "passed")
        metrics: dict[str, str | int | float | bool] = {}
        if case.get("time") is not None:
            metrics["duration_seconds"] = float(case.get("time", "0"))
        observations.append(
            _runtime_observation(
                kind=kind,
                test_name=f"{classname}.{name}" if classname else name,
                subject_identity=None,
                locator=None,
                outcome=outcome,
                metrics=metrics,
                failure_provenance=(
                    failure.get("type") or failure.text or "reported failure"
                    if failure is not None
                    else None
                ),
            )
        )
    return TestRuntimeArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="junit",
        provider_version=provider_version,
        environment_digest=environment_digest,
        selection=selection,
        completeness="complete",
        observations=observations,
        limitations=["A passing JUnit case does not establish unselected tests, coverage, or mutation detection."],
        source_artifact_digest=sha256_digest(raw),
    )


def parse_coverage_json(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
    environment_digest: str,
    selection: list[str],
    max_bytes: int = 64 * 1024 * 1024,
) -> TestRuntimeArtifact:
    """Parse coverage.py-style JSON as line and branch context, never proof of redundancy."""
    payload = _bounded_json(raw, max_bytes, "coverage")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise TestEvidenceError(
            "coverage_shape_invalid",
            "Coverage JSON requires a files object",
            remediation="Generate supported coverage JSON.",
        )
    observations = []
    for path, value in sorted(files.items()):
        if not isinstance(path, str) or not isinstance(value, dict):
            continue
        validate_repository_path(path)
        executed = value.get("executed_lines", [])
        missing = value.get("missing_lines", [])
        observations.append(
            _runtime_observation(
                kind=TestEvidenceKind.LINE_COVERAGE,
                test_name=None,
                subject_identity=path,
                locator=None,
                outcome="observed",
                metrics={"executed_lines": len(executed), "missing_lines": len(missing)},
                failure_provenance=None,
            )
        )
        summary = value.get("summary", {})
        if isinstance(summary, dict) and "num_branches" in summary:
            observations.append(
                _runtime_observation(
                    kind=TestEvidenceKind.BRANCH_COVERAGE,
                    test_name=None,
                    subject_identity=path,
                    locator=None,
                    outcome="observed",
                    metrics={
                        "branches": int(summary.get("num_branches", 0)),
                        "covered_branches": int(summary.get("covered_branches", 0)),
                    },
                    failure_provenance=None,
                )
            )
    return TestRuntimeArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="coverage.py",
        provider_version=provider_version,
        environment_digest=environment_digest,
        selection=selection,
        completeness="partial",
        observations=observations,
        limitations=[
            "Coverage records observed execution context; uncovered or covered code is not a redundancy verdict."
        ],
        source_artifact_digest=sha256_digest(raw),
    )


def parse_mutation_json(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
    environment_digest: str,
    selection: list[str],
    max_bytes: int = 64 * 1024 * 1024,
) -> TestRuntimeArtifact:
    """Parse a small generic mutation-result interchange."""
    payload = _bounded_json(raw, max_bytes, "mutation")
    mutants = payload.get("mutants")
    if not isinstance(mutants, list):
        raise TestEvidenceError(
            "mutation_shape_invalid",
            "Mutation JSON requires a mutants array",
            remediation="Export mutation results with path, line, operator, and status.",
        )
    observations = []
    for item in mutants:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        path = item["path"]
        validate_repository_path(path)
        line = int(item.get("line", 1))
        status = str(item.get("status", "unknown"))
        observations.append(
            _runtime_observation(
                kind=TestEvidenceKind.MUTATION_DETECTION,
                test_name=None,
                subject_identity=path,
                locator=TestLocator(path=path, start_line=line, end_line=line),
                outcome=status,
                metrics={"operator": str(item.get("operator", "unknown"))},
                failure_provenance=None,
            )
        )
    return TestRuntimeArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="mutation-json",
        provider_version=provider_version,
        environment_digest=environment_digest,
        selection=selection,
        completeness="partial",
        observations=observations,
        limitations=["Surviving or killed mutants are observations under the selected mutation configuration."],
        source_artifact_digest=sha256_digest(raw),
    )


def compare_test_intent(left: TestIntent, right: TestIntent) -> TestIntentComparison:
    fields = (
        "fixtures",
        "parameterizations",
        "calls",
        "targets",
        "assertions",
        "expected_exceptions",
        "snapshots",
        "marks",
        "setup",
        "generated_cases",
    )
    shared: dict[str, list[str]] = {}
    divergent: dict[str, dict[str, list[str]]] = {}
    for name in fields:
        left_values = set(getattr(left, name))
        right_values = set(getattr(right, name))
        common = sorted(left_values & right_values)
        if common:
            shared[name] = common
        if left_values != right_values:
            divergent[name] = {"left": sorted(left_values - right_values), "right": sorted(right_values - left_values)}
    return TestIntentComparison(
        left_intent_id=left.intent_id,
        right_intent_id=right.intent_id,
        shared=shared,
        divergent=divergent,
    )


def test_runtime_provider_envelope(
    artifact: TestRuntimeArtifact,
    *,
    baseline: RepositoryEvidence,
    configuration_digest: str,
    policy_digest: str,
) -> ProviderEnvelope:
    """Map runtime/coverage/mutation observations through the common provider contract."""
    from anatomize.evidence import (
        CompletenessRecord,
        CompletenessStatus,
        FileEntity,
        ObservationStance,
        ProviderRunStatus,
        RuntimeEntity,
        RuntimeObservation,
        TestEntity,
    )
    from anatomize.providers import (
        AuthorityLevel,
        InvocationAuthority,
        InvocationMode,
        ProviderBatchBuilder,
        ProviderScope,
        ProviderToolIdentity,
        build_provider_envelope,
    )

    state = next(item for item in baseline.states if item.state_id == artifact.source_state_id)
    builder = ProviderBatchBuilder(baseline)
    repository = builder.repository_entity(state)
    completeness_id = content_id(
        "completeness:test-runtime",
        {"run": artifact.provider_run_id, "state": artifact.source_state_id},
    )
    for observation in artifact.observations:
        subjects = [
            item
            for item in baseline.entities
            if item.source_state_id == state.state_id
            and (
                isinstance(item, TestEntity)
                and observation.test_name is not None
                and observation.test_name.endswith(item.test_name)
                or isinstance(item, FileEntity)
                and _runtime_observation_matches_file(observation, item, artifact.selection)
            )
        ]
        if not subjects:
            continue
        for subject in subjects:
            builder.include_baseline_entity(subject)
        runtime_id = content_id("entity:test-runtime", {"observation": observation.observation_id})
        runtime = RuntimeEntity(
            entity_id=runtime_id,
            source_state_id=state.state_id,
            display_name=f"{observation.kind.value}: {observation.test_name or observation.subject_identity}",
            runtime_kind=observation.kind.value,
            status=observation.outcome,
            run_identity=artifact.provider_run_id,
        )
        builder.entities[runtime.entity_id] = runtime
        normalized = RuntimeObservation(
            observation_id=observation.observation_id,
            source_state_id=state.state_id,
            provider_run_id=artifact.provider_run_id,
            method=artifact.provider_id,
            method_version=artifact.provider_version,
            strength=EvidenceStrength.EXACT,
            stance=ObservationStance.QUALIFIES,
            completeness_id=completeness_id,
            rationale=(
                "Runtime evidence is retained separately from static exercise, coverage, mutation, and absence."
            ),
            runtime_entity_id=runtime.entity_id,
            subject_entity_ids=sorted(item.entity_id for item in subjects),
            outcome=observation.outcome,
            metrics={
                **observation.metrics,
                "environment_digest": artifact.environment_digest,
                "selection": ",".join(artifact.selection),
                **(
                    {"failure_provenance": observation.failure_provenance}
                    if observation.failure_provenance is not None
                    else {}
                ),
            },
        )
        builder.observations[normalized.observation_id] = normalized
    completeness = CompletenessRecord(
        completeness_id=completeness_id,
        source_state_id=state.state_id,
        provider_run_id=artifact.provider_run_id,
        scope_type="repository",
        scope_id=repository.entity_id,
        evidence_families=sorted({item.kind.value for item in artifact.observations}) or ["runtime_execution"],
        status=CompletenessStatus(artifact.completeness),
    )
    payload = builder.build_payload([completeness])
    return build_provider_envelope(
        provider_run_id=artifact.provider_run_id,
        provider_id=artifact.provider_id,
        provider_version=artifact.provider_version,
        tool=ProviderToolIdentity(name=artifact.provider_id, version=artifact.provider_version),
        capabilities=completeness.evidence_families,
        languages=[],
        repository_id=artifact.repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=configuration_digest,
        scope=ProviderScope(
            scope_id=content_id("provider-scope:test-runtime", {"run": artifact.provider_run_id}),
            source_state_ids=[state.state_id],
            entity_ids=sorted(builder.entities),
            evidence_families=completeness.evidence_families,
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.ARTIFACT_IMPORT,
            level=AuthorityLevel.A1_ARTIFACT,
            policy_digest=policy_digest,
        ),
        status=(
            ProviderRunStatus.COMPLETE
            if artifact.completeness == "complete"
            else ProviderRunStatus.UNAVAILABLE
            if artifact.completeness == "unavailable"
            else ProviderRunStatus.PARTIAL
        ),
        payload=payload,
    )


def _runtime_observation_matches_file(
    observation: RuntimeTestObservation,
    file: object,
    selection: list[str],
) -> bool:
    """Bind runtime results to exact source paths or selected test files.

    JUnit commonly reports only a dotted test name.  The independently recorded
    selection is therefore the authoritative fallback for repository indexes
    that expose test files rather than synthetic ``TestEntity`` records.
    """
    from anatomize.evidence import FileEntity

    if not isinstance(file, FileEntity):
        return False
    if observation.subject_identity == file.path or (
        observation.locator is not None and observation.locator.path == file.path
    ):
        return True
    if "test" not in file.roles:
        return False
    if any(item.split("::", maxsplit=1)[0] == file.path for item in selection):
        return True
    dotted_path = str(PurePosixPath(file.path).with_suffix("")).replace("/", ".")
    return observation.test_name is not None and (
        observation.test_name == dotted_path or observation.test_name.startswith(f"{dotted_path}.")
    )


def _test_functions(tree: ast.AST) -> list[tuple[str | None, ast.FunctionDef | ast.AsyncFunctionDef]]:
    result = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            parent_suite = next(
                (
                    parent.name
                    for parent in ast.walk(tree)
                    if isinstance(parent, ast.ClassDef) and node in parent.body
                ),
                None,
            )
            result.append((parent_suite, node))
    return sorted(result, key=lambda item: (item[1].lineno, item[1].name))


def _call_name(value: ast.AST) -> str:
    target = value.func if isinstance(value, ast.Call) else value
    parts = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts)) or type(value).__name__


def _source_segment(source: str, node: ast.AST) -> str:
    return (ast.get_source_segment(source, node) or type(node).__name__)[:2_048]


def _intent_id(intent: TestIntent) -> str:
    return content_id(
        "test-intent",
        {
            "source_state_id": intent.source_state_id,
            "framework": intent.framework,
            "suite": intent.suite,
            "name": intent.name,
            "locator": intent.locator.model_dump(mode="json"),
        },
    )


def _build_test_intent(**values: Any) -> TestIntent:
    provisional = TestIntent.model_construct(intent_id="pending", **values)
    return TestIntent(intent_id=_intent_id(provisional), **values)


def _runtime_observation(**values: Any) -> RuntimeTestObservation:
    provisional = RuntimeTestObservation.model_construct(observation_id="pending", **values)
    observation_id = content_id(
        "test-runtime-observation",
        provisional.model_dump(mode="json", exclude={"observation_id"}),
    )
    return RuntimeTestObservation(observation_id=observation_id, **values)


def _r_test_blocks(lines: list[str]) -> list[tuple[int, int, str, str]]:
    result: list[tuple[int, int, str, str]] = []
    pattern = re.compile(r"\btest_that\s*\(\s*(['\"])(.*?)\1\s*,\s*\{")
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if match is None:
            continue
        depth = line[match.start() :].count("{") - line[match.start() :].count("}")
        end = index
        while depth > 0 and end + 1 < len(lines):
            end += 1
            depth += lines[end].count("{") - lines[end].count("}")
        result.append((index + 1, end + 1, match.group(2), "\n".join(lines[index : end + 1])))
    return result


def _check_bytes(raw: bytes, maximum: int, label: str) -> None:
    if len(raw) > maximum:
        raise TestEvidenceError(
            f"{label.casefold()}_too_large",
            f"{label} artifact exceeds {maximum} bytes",
            remediation="Narrow the exported artifact or raise an explicit trusted limit.",
        )


def _bounded_json(raw: bytes, maximum: int, label: str) -> dict[str, Any]:
    try:
        return parse_bounded_json_object(
            raw,
            limits=JsonLimits(max_bytes=maximum, max_depth=64, max_values=2_000_000, max_string_bytes=maximum),
        )
    except BoundedJsonError as error:
        raise TestEvidenceError(
            f"{label}_{error.code}",
            f"{label.title()} artifact {error}",
            remediation=f"Regenerate bounded {label} JSON.",
        ) from error
