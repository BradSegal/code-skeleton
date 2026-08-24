from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.evidence import RuntimeObservation
from anatomize.index import build_repository_index
from anatomize.lifecycle import (
    TestEvidenceError as LifecycleTestEvidenceError,
)
from anatomize.lifecycle import (
    TestEvidenceKind as LifecycleTestEvidenceKind,
)
from anatomize.lifecycle import (
    compare_test_intent,
    extract_python_test_intent,
    extract_r_test_intent,
    parse_coverage_json,
    parse_junit_xml,
    parse_mutation_json,
)
from anatomize.lifecycle import (
    test_runtime_provider_envelope as build_test_provider_envelope,
)
from anatomize.providers import repository_index_evidence
from tests.unit.test_evidence_models import _known_truth_evidence


def _digest(value: str) -> str:
    return sha256_digest(value.encode())


def test_python_intent_retains_structural_signals_and_exact_locators() -> None:
    source = '''@pytest.mark.slow
@pytest.mark.parametrize("value", [1, 2])
def test_answer(value, fixture_db, snapshot):
    setup_case()
    with pytest.raises(ValueError):
        pkg.answer(value)
    snapshot.assert_match(value)
    assert value > 0
'''
    intent = extract_python_test_intent(
        source,
        repository_id="repository:fixture",
        source_state_id="state:after",
        path="tests/test_core.py",
    ).intents[0]

    assert intent.framework == "pytest"
    assert intent.locator.start_line == 3 and intent.locator.end_line == 8
    assert intent.fixtures == ["fixture_db", "snapshot", "value"]
    assert intent.parameterizations == ["pytest.mark.parametrize"]
    assert intent.expected_exceptions == ["ValueError"]
    assert "pkg.answer" in intent.targets
    assert intent.assertions == ["assert value > 0"]
    assert intent.snapshots == ["snapshot.assert_match"]
    assert intent.setup == ["setup_case"]
    assert intent.generated_cases == ["pytest.mark.parametrize"]


def test_r_testthat_intent_is_distinct_and_bounded() -> None:
    source = '''test_that("answer varies by input", {
  local_options(list(warn = 2))
  for (value in c(1, 2)) {
    expect_equal(answer(value), value)
    expect_snapshot(print(answer(value)))
    expect_error(answer(NULL))
  }
})
'''
    intent = extract_r_test_intent(
        source,
        repository_id="repository:fixture",
        source_state_id="state:after",
        path="tests/testthat/test-answer.R",
    ).intents[0]

    assert intent.framework == "testthat"
    assert intent.locator.start_line == 1 and intent.locator.end_line == 8
    assert intent.fixtures == ["local_options"]
    assert intent.parameterizations == ["for"]
    assert intent.expected_exceptions == ["expect_error"]
    assert "answer" in intent.targets
    assert "expect_snapshot" in intent.snapshots


def test_junit_preserves_runtime_failure_skip_environment_and_selection() -> None:
    raw = b'''<testsuite><testcase classname="tests.test_core" name="test_answer" time="0.2" />
<testcase name="test_failure"><failure type="AssertionError">bounded trace</failure></testcase>
<testcase name="test_optional"><skipped /></testcase></testsuite>'''
    artifact = parse_junit_xml(
        raw,
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:junit",
        provider_version="1",
        environment_digest=_digest("python-3.12-linux"),
        selection=["tests/test_core.py"],
    )

    assert [item.kind for item in artifact.observations] == [
        LifecycleTestEvidenceKind.RUNTIME_EXECUTION,
        LifecycleTestEvidenceKind.FAILURE,
        LifecycleTestEvidenceKind.SKIP,
    ]
    assert artifact.observations[1].failure_provenance == "AssertionError"
    assert artifact.selection == ["tests/test_core.py"]
    with pytest.raises(LifecycleTestEvidenceError, match="DTD"):
        parse_junit_xml(
            b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><testsuite/>',
            repository_id="repository:fixture",
            source_state_id="state:after",
            provider_run_id="run:unsafe",
            provider_version="1",
            environment_digest=_digest("environment"),
            selection=[],
        )


def test_coverage_and_mutation_are_not_collapsed_into_static_or_absence() -> None:
    coverage = parse_coverage_json(
        json.dumps(
            {
                "files": {
                    "src/pkg/core.py": {
                        "executed_lines": [1],
                        "missing_lines": [2],
                        "summary": {"num_branches": 2, "covered_branches": 1},
                    }
                }
            }
        ).encode(),
        provider_run_id="run:coverage",
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_version="1",
        environment_digest=_digest("environment"),
        selection=["tests"],
    )
    mutation = parse_mutation_json(
        json.dumps(
            {
                "mutants": [
                    {"path": "src/pkg/core.py", "line": 1, "operator": "negate", "status": "killed"}
                ]
            }
        ).encode(),
        provider_run_id="run:mutation",
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_version="1",
        environment_digest=_digest("environment"),
        selection=["tests"],
    )

    assert {item.kind for item in coverage.observations} == {
        LifecycleTestEvidenceKind.LINE_COVERAGE,
        LifecycleTestEvidenceKind.BRANCH_COVERAGE,
    }
    assert mutation.observations[0].kind is LifecycleTestEvidenceKind.MUTATION_DETECTION
    assert LifecycleTestEvidenceKind.ABSENCE not in {
        item.kind for item in [*coverage.observations, *mutation.observations]
    }


def test_runtime_import_uses_common_provider_contract_and_keeps_environment() -> None:
    environment = _digest("python-3.12-linux")
    artifact = parse_junit_xml(
        b'<testsuite><testcase name="test_answer" time="0.1" /></testsuite>',
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:junit",
        provider_version="1",
        environment_digest=environment,
        selection=["tests/test_core.py::test_answer"],
    )
    envelope = build_test_provider_envelope(
        artifact,
        baseline=_known_truth_evidence(),
        configuration_digest=_digest("junit-config"),
        policy_digest=_digest("artifact-policy"),
    )

    assert envelope.payload.observations[0].record_type == "runtime_observation"
    assert envelope.payload.observations[0].metrics["environment_digest"] == environment
    assert envelope.payload.observations[0].metrics["selection"] == "tests/test_core.py::test_answer"
    assert envelope.payload.completeness[0].status.value == "complete"


def test_junit_runtime_binds_to_selected_test_file_from_repository_index(tmp_path: Path) -> None:
    test_path = tmp_path / "tests/test_core.py"
    test_path.parent.mkdir()
    test_path.write_text("def test_answer():\n    assert True\n", encoding="utf-8")
    baseline = repository_index_evidence(
        build_repository_index(tmp_path),
        repository_id="repository:fixture",
    )
    state = baseline.states[0]
    artifact = parse_junit_xml(
        b'<testsuite><testcase classname="tests.test_core" name="test_answer" /></testsuite>',
        repository_id=baseline.repository_id,
        source_state_id=state.state_id,
        provider_run_id="run:junit-index",
        provider_version="1",
        environment_digest=_digest("python-3.12-linux"),
        selection=["tests/test_core.py::test_answer"],
    )

    envelope = build_test_provider_envelope(
        artifact,
        baseline=baseline,
        configuration_digest=_digest("junit-config"),
        policy_digest=_digest("artifact-policy"),
    )

    observation = cast(RuntimeObservation, envelope.payload.observations[0])
    assert "file:tests/test_core.py" in observation.subject_entity_ids


def test_duplicate_test_comparison_exposes_difference_without_a_verdict() -> None:
    left = extract_python_test_intent(
        "def test_answer(client):\n    assert answer(client) == 1\n",
        repository_id="repository:fixture",
        source_state_id="state:after",
        path="tests/test_a.py",
    ).intents[0]
    right = extract_python_test_intent(
        "def test_answer(client):\n    assert answer(client) == 2\n",
        repository_id="repository:fixture",
        source_state_id="state:after",
        path="tests/test_b.py",
    ).intents[0]

    comparison = compare_test_intent(left, right)

    assert comparison.shared["calls"] == ["answer"]
    assert comparison.divergent["assertions"] == {
        "left": ["assert answer(client) == 1"],
        "right": ["assert answer(client) == 2"],
    }
    assert "redundant" not in comparison.model_dump_json()
