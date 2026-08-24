#!/usr/bin/env python3
"""Evaluate complete review workflows on a generated held-out repository."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anatomize._artifacts import sha256_digest
from anatomize.dossiers import DossierBudget, DossierProfile, DossierStatus, EvidenceRole, TargetKind, TargetSelector
from anatomize.evidence import FileEntity, RepositoryEvidence
from anatomize.index import build_repository_index
from anatomize.lifecycle import (
    ClosureObservation,
    DecisionDisposition,
    ObligationKind,
    ObligationStatus,
    build_implementation_obligation,
    parse_junit_xml,
    test_runtime_provider_envelope,
)
from anatomize.providers import ProviderEnvelope, repository_index_evidence
from anatomize.research import (
    ResearchGraphArtifact,
    WorkflowStep,
    parse_snakemake_workflow,
    research_graph_provider_envelope,
)
from anatomize.review import (
    DossierExchange,
    ReviewApplication,
    ReviewApplicationError,
    ReviewOutputFormat,
    canonical_review_bytes,
    render_review,
    target_selector,
)

REPOSITORY_ID = "repository:ledger-lab-held-out"
POLICY_DIGEST = sha256_digest(b"held-out;artifact-import;no-network;content-free")


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    profile: DossierProfile
    selector: TargetSelector | None
    roles: frozenset[EvidenceRole]
    paths: frozenset[str]
    judgement: str


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def build_corpus(root: Path) -> None:
    """Generate a public-style package and research workflow after feature delivery."""
    write(
        root / "src/ledgerlite/rules.py",
        "def normalise_reference(value: str) -> str:\n"
        "    cleaned = value.strip().casefold()\n"
        "    pieces = cleaned.split()\n"
        "    compact = '-'.join(pieces)\n"
        "    checked = compact.replace('_', '-')\n"
        "    return checked\n\n"
        "def assess_risk(values: list[int]) -> int:\n"
        "    positive = [value for value in values if value > 0]\n"
        "    return sum(positive)\n",
    )
    write(
        root / "src/ledgerlite/legacy.py",
        "def canonicalise_reference(value: str) -> str:\n"
        "    cleaned = value.strip().casefold()\n"
        "    pieces = cleaned.split()\n"
        "    compact = '-'.join(pieces)\n"
        "    checked = compact.replace('_', '-')\n"
        "    return checked\n",
    )
    write(
        root / "src/ledgerlite/service.py",
        "from ledgerlite.rules import assess_risk, normalise_reference\n\n"
        "def record(reference: str, values: list[int]) -> dict[str, object]:\n"
        "    return {'reference': normalise_reference(reference), 'risk': assess_risk(values)}\n",
    )
    write(
        root / "src/ledgerlite/cli.py",
        "from ledgerlite.service import record\n\n"
        "def main() -> None:\n"
        "    print(record(' Example ', [1, -1, 2]))\n",
    )
    write(
        root / "src/ledgerlite/__init__.py",
        "from ledgerlite.rules import assess_risk, normalise_reference\n\n"
        "__all__ = ['assess_risk', 'normalise_reference']\n",
    )
    body = (
        "    raw = ' Trial  A '\n"
        "    first = FUNCTION(raw)\n"
        "    second = FUNCTION(raw)\n"
        "    assert first == second\n"
        "    assert first == 'trial-a'\n"
    )
    write(
        root / "tests/test_rules.py",
        "from ledgerlite.rules import normalise_reference as subject\n\n"
        "def test_public_reference() -> None:\n"
        + body.replace("FUNCTION", "subject"),
    )
    write(
        root / "tests/test_legacy.py",
        "from ledgerlite.legacy import canonicalise_reference as subject\n\n"
        "def test_legacy_reference() -> None:\n"
        + body.replace("FUNCTION", "subject"),
    )
    claim = (
        "Reference values are stripped and case folded.\n"
        "They are separated on whitespace and joined with hyphens.\n"
        "Underscore input is normalized through the same rule.\n"
        "The deterministic result is a portable downstream key.\n"
        "Callers may store that key and compare it across runs.\n"
        "They can display the original value when a person needs the unmodified source label."
    )
    write(
        root / "README.md",
        "# Ledger Lite\n\nUse ledgerlite.normalise_reference as the public entry point.\n\n"
        "## Reference normalization\n\n" + claim + "\n",
    )
    write(root / "docs/api.md", "# API\n\n## Reference normalization\n\n" + claim + "\n")
    write(
        root / "docs/legacy.md",
        "# Legacy API\n\n## Reference normalization\n\n"
        + claim
        + "\n\n## Ownership\n\nThe legacy implementation remains the recommended owner.\n",
    )
    write(
        root / "pyproject.toml",
        "[project]\nname='ledgerlite'\nversion='0.4.0'\nrequires-python='>=3.10'\ndependencies=[]\n\n"
        "[project.scripts]\nledgerlite='ledgerlite.cli:main'\n",
    )
    write(root / "scripts/analyse.py", "from ledgerlite import assess_risk\nprint(assess_risk([1, 2, 3]))\n")
    write(root / "data/input.metadata.json", '{"locator":"data/input.csv","content_included":false}\n')
    write(root / "environment.yml", "name: ledger-lab\ndependencies: [python=3.12]\n")
    write(root / "reports/summary.metadata.json", '{"locator":"reports/summary.html","generated":true}\n')
    write_json(
        root / "workflow/snakemake-summary.json",
        {
            "workflow_id": "ledger-lab",
            "rules": [
                {
                    "name": "analyse",
                    "script": "scripts/analyse.py",
                    "input": [{"locator": "data/input.csv", "content_class": "ordinary"}],
                    "output": [
                        {
                            "locator": "derived/risk.json",
                            "content_class": "generated",
                            "digest": "risk-v1",
                        }
                    ],
                    "environment": {"kind": "conda", "name": "ledger-lab", "locator": "environment.yml"},
                },
                {
                    "name": "report",
                    "script": "scripts/analyse.py",
                    "input": [{"locator": "derived/risk.json", "content_class": "generated"}],
                    "report": [
                        {
                            "locator": "reports/summary.html",
                            "content_class": "generated",
                            "digest": "report-v1",
                        }
                    ],
                    "depends_on": ["analyse"],
                },
            ],
        },
    )
    for index in range(20):
        write(
            root / "notes" / f"unrelated-{index:02d}.md",
            f"# Unrelated note {index}\n\nAn independent administrative topic with no ledger behavior.\n",
        )


def research_provider(root: Path) -> tuple[ProviderEnvelope, ResearchGraphArtifact, RepositoryEvidence]:
    index = build_repository_index(root)
    baseline = repository_index_evidence(index, repository_id=REPOSITORY_ID)
    graph = parse_snakemake_workflow(
        (root / "workflow/snakemake-summary.json").read_bytes(),
        repository_id=REPOSITORY_ID,
        source_state_id=baseline.states[0].state_id,
        provider_run_id=f"provider-run:held-out:{index.source_state.fact_digest}",
        provider_version="held-out-1",
    )
    return (
        research_graph_provider_envelope(graph, baseline=baseline, policy_digest=POLICY_DIGEST),
        graph,
        baseline,
    )


def workflow_step(graph: ResearchGraphArtifact, name: str) -> WorkflowStep:
    return next(item for item in graph.nodes if isinstance(item, WorkflowStep) and item.name == name)


def tasks(graph: ResearchGraphArtifact) -> list[Task]:
    analyse = workflow_step(graph, "analyse")
    return [
        Task(
            "orientation",
            "Orient this repository before choosing a change boundary.",
            DossierProfile.ORIENTATION,
            None,
            frozenset(
                {
                    EvidenceRole.TOPOLOGY,
                    EvidenceRole.PUBLIC_SURFACE,
                    EvidenceRole.ENTRY_POINT,
                    EvidenceRole.TEST,
                    EvidenceRole.DOCUMENTATION,
                    EvidenceRole.CONFIGURATION,
                    EvidenceRole.WORKFLOW,
                }
            ),
            frozenset(
                {
                    "README.md",
                    "data/input.csv",
                    "derived/risk.json",
                    "environment.yml",
                    "pyproject.toml",
                    "reports/summary.html",
                    "scripts/analyse.py",
                    "src/ledgerlite/__init__.py",
                    "src/ledgerlite/cli.py",
                    "src/ledgerlite/legacy.py",
                    "src/ledgerlite/rules.py",
                    "src/ledgerlite/service.py",
                    "tests/test_legacy.py",
                    "tests/test_rules.py",
                }
            ),
            "The package and workflow entry points are visible before source reading.",
        ),
        Task(
            "design-comparison",
            "Compare two normalizers with one canonical implementation and an explicit alias.",
            DossierProfile.DESIGN,
            target_selector("ledgerlite.rules.normalise_reference", kind=TargetKind.SYMBOL),
            frozenset(
                {
                    EvidenceRole.DEFINITION,
                    EvidenceRole.CONSUMER,
                    EvidenceRole.TEST,
                    EvidenceRole.DOCUMENTATION,
                    EvidenceRole.DUPLICATE_CANDIDATE,
                }
            ),
            frozenset(
                {
                    "src/ledgerlite/rules.py",
                    "src/ledgerlite/legacy.py",
                    "src/ledgerlite/service.py",
                    "tests/test_rules.py",
                    "README.md",
                }
            ),
            "Prefer one owner only after preserving public and legacy contracts.",
        ),
        Task(
            "architecture-audit",
            "Audit ownership and dependencies around the record service.",
            DossierProfile.AUDIT,
            target_selector("src/ledgerlite/service.py", kind=TargetKind.FILE),
            frozenset({EvidenceRole.DEFINITION, EvidenceRole.DEPENDENCY, EvidenceRole.CONSUMER}),
            frozenset({"src/ledgerlite/service.py", "src/ledgerlite/rules.py", "src/ledgerlite/cli.py"}),
            "The rules module owns policy; service and CLI remain consumers.",
        ),
        Task(
            "targeted-implementation",
            "Localise what must be preserved while consolidating the legacy normalizer.",
            DossierProfile.IMPLEMENTATION,
            target_selector("src/ledgerlite/legacy.py", kind=TargetKind.FILE),
            frozenset(
                {
                    EvidenceRole.DEFINITION,
                    EvidenceRole.TEST,
                    EvidenceRole.DUPLICATE_CANDIDATE,
                }
            ),
            frozenset({"src/ledgerlite/legacy.py", "src/ledgerlite/rules.py", "tests/test_legacy.py"}),
            "Replace the body with an alias and retain a named compatibility test.",
        ),
        Task(
            "test-refinement",
            "Can repeated test setup be shared without erasing the two named contracts?",
            DossierProfile.AUDIT,
            target_selector("tests/test_legacy.py", kind=TargetKind.TEST),
            frozenset({EvidenceRole.TEST, EvidenceRole.DEFINITION, EvidenceRole.DUPLICATE_CANDIDATE}),
            frozenset({"tests/test_rules.py", "tests/test_legacy.py", "src/ledgerlite/legacy.py"}),
            "Keep two contract tests and consolidate only their setup.",
        ),
        Task(
            "documentation-repair",
            "Repair stale ownership and choose one canonical normalization explanation.",
            DossierProfile.IMPLEMENTATION,
            target_selector("docs/legacy.md#Reference normalization", kind=TargetKind.DOCUMENTATION_SECTION),
            frozenset({EvidenceRole.DOCUMENTATION, EvidenceRole.DUPLICATE_CANDIDATE}),
            frozenset({"README.md", "docs/api.md", "docs/legacy.md"}),
            "Link to one canonical explanation and name the actual owner.",
        ),
        Task(
            "research-reproducibility",
            "Trace analysis to inputs, environment, code, output, and downstream report.",
            DossierProfile.AUDIT,
            TargetSelector(kind=TargetKind.WORKFLOW, identity=analyse.node_id),
            frozenset(
                {
                    EvidenceRole.WORKFLOW,
                    EvidenceRole.DATA,
                    EvidenceRole.CONFIGURATION,
                    EvidenceRole.ARTIFACT,
                }
            ),
            frozenset(
                {
                    "data/input.csv",
                    "derived/risk.json",
                    "environment.yml",
                    "reports/summary.html",
                    "scripts/analyse.py",
                }
            ),
            "Rebuild when source, environment, input, or workflow identity changes.",
        ),
        Task(
            "release-preparation",
            "Prepare a public release review without treating repository evidence as approval.",
            DossierProfile.AUDIT,
            TargetSelector(kind=TargetKind.REPOSITORY, identity=REPOSITORY_ID),
            frozenset(
                {
                    EvidenceRole.PUBLIC_SURFACE,
                    EvidenceRole.TEST,
                    EvidenceRole.DOCUMENTATION,
                    EvidenceRole.CONFIGURATION,
                }
            ),
            frozenset({"README.md", "pyproject.toml", "tests/test_rules.py"}),
            "Run native package, test, security, documentation, and release checks.",
        ),
    ]


def source_files(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in root.rglob("*")
        if path.is_file()
    }


def selected_paths(exchange: DossierExchange) -> set[str]:
    return {
        locator.path
        for item in exchange.dossier.items
        for locator in item.locators
        if locator.path is not None
    }


def evaluate_task(
    app: ReviewApplication,
    bundle: Any,
    task: Task,
    files: dict[str, int],
) -> tuple[dict[str, Any], DossierExchange]:
    started = time.perf_counter()
    exchange = app.dossier(
        bundle,
        profile=task.profile,
        targets=[] if task.selector is None else [task.selector],
        question=task.prompt,
    )
    latency_ms = (time.perf_counter() - started) * 1_000
    dossier = exchange.dossier
    selected = selected_paths(exchange)
    observed_roles = {item.role for item in dossier.items}
    role_recall = len(task.roles.intersection(observed_roles)) / len(task.roles)
    path_recall = len(task.paths.intersection(selected)) / len(task.paths)
    irrelevant = selected.difference(task.paths)
    baseline_irrelevant = sum(size for path, size in files.items() if path not in task.paths)
    selected_irrelevant = sum(files.get(path, 0) for path in irrelevant)
    markdown = render_review(exchange, format=ReviewOutputFormat.MARKDOWN)
    exact_target = not dossier.boundary.targets or all(
        target.status.value == "exact" for target in dossier.boundary.targets
    )
    provenance = (
        dossier.source_state_ids == [bundle.manifest.source_states[0].source_state.state_id]
        and bool(dossier.provider_run_ids)
        and all(item.selection_reasons for item in dossier.items)
    )
    comprehension = all(
        marker in markdown
        for marker in (
            task.prompt,
            "## Target",
            "## Evidence",
            "## Omissions and unknowns",
            "## Expansion actions",
            "Authority: Anatomize supplies source-bound evidence",
        )
    )
    correct = (
        dossier.status in {DossierStatus.COMPLETE, DossierStatus.PARTIAL}
        and exact_target
        and role_recall == 1.0
        and provenance
        and comprehension
    )
    return (
        {
            "workflow": task.name,
            "prompt": task.prompt,
            "profile": task.profile.value,
            "source_state_id": dossier.source_state_ids[0],
            "dossier_id": dossier.dossier_id,
            "status": dossier.status.value,
            "exact_target": exact_target,
            "required_roles": sorted(role.value for role in task.roles),
            "observed_roles": sorted(role.value for role in observed_roles),
            "required_role_recall": round(role_recall, 6),
            "expected_paths": sorted(task.paths),
            "selected_paths": sorted(selected),
            "selected_path_recall": round(path_recall, 6),
            "irrelevant_selected_paths": sorted(irrelevant),
            "baseline_irrelevant_bytes": baseline_irrelevant,
            "selected_irrelevant_bytes": selected_irrelevant,
            "irrelevant_byte_reduction": (
                round(1 - selected_irrelevant / baseline_irrelevant, 6)
                if baseline_irrelevant
                else 1.0
            ),
            "latency_ms": round(latency_ms, 3),
            "baseline_interactions": 4,
            "anatomize_interactions_after_session": 1,
            "provenance_fidelity": provenance,
            "reviewer_comprehension": comprehension,
            "reviewer_judgement": task.judgement,
            "correct": correct,
        },
        exchange,
    )


def find_candidate(similarity: Any, paths: set[str]) -> Any:
    return next(
        candidate
        for candidate in similarity.candidates
        if {member.path for member in candidate.members} == paths
    )


def apply_change(root: Path) -> None:
    write(
        root / "src/ledgerlite/legacy.py",
        "from ledgerlite.rules import normalise_reference\n\n"
        "canonicalise_reference = normalise_reference\n",
    )
    write(
        root / "docs/legacy.md",
        "# Legacy API\n\ncanonicalise_reference is a compatibility alias. "
        "See the canonical normalization contract in api.md.\n",
    )
    for path in (root / "tests/test_legacy.py", root / "tests/test_rules.py"):
        path.write_text(
            path.read_text(encoding="utf-8") + "    assert len(first) == 7\n",
            encoding="utf-8",
        )
    workflow_path = root / "workflow/snakemake-summary.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["rules"][0]["environment"]["name"] = "ledger-lab-v2"
    workflow["rules"][0]["output"][0]["digest"] = "risk-v2"
    workflow["rules"][1]["report"][0]["digest"] = "report-v2"
    write_json(workflow_path, workflow)
    write(root / "environment.yml", "name: ledger-lab-v2\ndependencies: [python=3.12]\n")


def provider_state_mismatch(
    app: ReviewApplication,
    root: Path,
    stale_provider: ProviderEnvelope,
) -> dict[str, Any]:
    try:
        app.start(root, repository_id=REPOSITORY_ID, provider_envelopes=[stale_provider])
    except ReviewApplicationError as error:
        return {"visible": True, "code": error.code, "remediation": error.remediation}
    return {"visible": False, "code": None, "remediation": None}


def artifact_identity(value: Any) -> str:
    payload = value.model_dump(mode="json")
    return next(item for key, item in payload.items() if key.endswith("_id") and isinstance(item, str))


def evaluate() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="anatomize-held-out-") as temporary_name:
        root = Path(temporary_name) / "ledger-lab"
        build_corpus(root)
        files = source_files(root)
        provider, graph, _ = research_provider(root)
        app = ReviewApplication()
        started = time.perf_counter()
        before = app.start(root, repository_id=REPOSITORY_ID, provider_envelopes=[provider])
        session_ms = (time.perf_counter() - started) * 1_000

        workflow_results: list[dict[str, Any]] = []
        exchanges: dict[str, DossierExchange] = {}
        for task in tasks(graph):
            result, exchange = evaluate_task(app, before, task, files)
            workflow_results.append(result)
            exchanges[task.name] = exchange

        started = time.perf_counter()
        similarity = app.similarity(before)
        similarity_ms = (time.perf_counter() - started) * 1_000
        implementation_candidate = find_candidate(
            similarity,
            {"src/ledgerlite/legacy.py", "src/ledgerlite/rules.py"},
        )
        test_candidate = find_candidate(
            similarity,
            {"tests/test_legacy.py", "tests/test_rules.py"},
        )
        documentation_candidate = find_candidate(
            similarity,
            {"README.md", "docs/api.md", "docs/legacy.md"},
        )
        consolidation = app.consolidation(exchanges["targeted-implementation"], implementation_candidate)
        decision = app.decision_overlay(
            before,
            implementation_candidate,
            owner_namespace="held-out-review",
            disposition=DecisionDisposition.CONSOLIDATE,
            rationale="One implementation preserves behavior while an explicit alias retains compatibility.",
            preserved_divergence=["The legacy public name remains an explicit alias."],
            review_conditions=["Reconsider if the names acquire distinct behavior."],
        )
        decision_current = app.evaluate_overlay(decision, before, implementation_candidate)
        test_decision = app.decision_overlay(
            before,
            test_candidate,
            owner_namespace="held-out-test-review",
            disposition=DecisionDisposition.KEEP,
            rationale="The tests protect two separately named public contracts.",
        )

        obligations = [
            build_implementation_obligation(
                kind=kind,
                subject_ref=subject,
                expectation=expectation,
                required_evidence_kinds=[evidence_kind],
            )
            for kind, subject, expectation, evidence_kind in (
                (
                    ObligationKind.OWNER,
                    "src/ledgerlite/rules.py",
                    "One module owns normalization.",
                    "source",
                ),
                (
                    ObligationKind.CONTRACT,
                    "canonicalise_reference",
                    "The legacy name remains callable as an alias.",
                    "test_runtime",
                ),
                (
                    ObligationKind.TEST,
                    "tests/test_legacy.py",
                    "A named compatibility test protects the alias.",
                    "test_runtime",
                ),
                (
                    ObligationKind.DOCUMENTATION,
                    "docs/legacy.md",
                    "Documentation names one canonical owner.",
                    "documentation",
                ),
                (
                    ObligationKind.WORKFLOW,
                    "workflow/snakemake-summary.json",
                    "Changed environments and outputs receive fresh identities.",
                    "workflow",
                ),
            )
        ]
        intent = app.implementation_intent(
            before,
            exchanges["targeted-implementation"],
            obligations=obligations,
            decision_overlay_id=decision.decision_id,
        )

        unknown_expansion: dict[str, Any] | None = None
        try:
            app.expand(before, exchanges["orientation"], action_id="expansion:unknown")
        except ReviewApplicationError as error:
            unknown_expansion = {
                "visible": True,
                "code": error.code,
                "remediation": error.remediation,
            }
        budgeted = app.dossier(
            before,
            profile=DossierProfile.IMPLEMENTATION,
            targets=[target_selector("src/ledgerlite/legacy.py", kind=TargetKind.FILE)],
            budget=DossierBudget(max_items=1),
        )
        unresolved = app.dossier(
            before,
            profile=DossierProfile.IMPLEMENTATION,
            targets=[target_selector("ledgerlite.missing", kind=TargetKind.SYMBOL)],
        )

        apply_change(root)
        after_provider, after_graph, after_baseline = research_provider(root)
        runtime_artifact = parse_junit_xml(
            (
                b'<testsuite><testcase classname="tests.test_legacy" '
                b'name="test_legacy_contract" time="0.01" /></testsuite>'
            ),
            repository_id=REPOSITORY_ID,
            source_state_id=after_baseline.states[0].state_id,
            provider_run_id="provider-run:held-out:junit-after",
            provider_version="held-out-1",
            environment_digest=sha256_digest(b"python-3.12;pytest;held-out"),
            selection=["tests/test_legacy.py::test_legacy_contract"],
        )
        runtime_provider = test_runtime_provider_envelope(
            runtime_artifact,
            baseline=after_baseline,
            configuration_digest=sha256_digest(b"held-out:selected-test"),
            policy_digest=POLICY_DIGEST,
        )
        after = app.start(
            root,
            repository_id=REPOSITORY_ID,
            provider_envelopes=[after_provider, runtime_provider],
        )
        change = app.change(before, after)
        after_similarity = app.similarity(after)
        after_test_candidate = find_candidate(
            after_similarity,
            {"tests/test_legacy.py", "tests/test_rules.py"},
        )
        stale_test_decision = app.evaluate_overlay(test_decision, after, after_test_candidate)
        incomplete = app.verify(intent, after, observations=[])
        after_files = {
            item.path: item.entity_id
            for item in after_baseline.entities
            if isinstance(item, FileEntity)
        }
        runtime_ref = runtime_artifact.observations[0].observation_id
        workflow_ref = workflow_step(after_graph, "analyse").node_id
        evidence_refs = {
            ObligationKind.OWNER: [after_files["src/ledgerlite/rules.py"]],
            ObligationKind.CONTRACT: [runtime_ref],
            ObligationKind.TEST: [runtime_ref],
            ObligationKind.DOCUMENTATION: [after_files["docs/legacy.md"]],
            ObligationKind.WORKFLOW: [workflow_ref],
        }
        observations = [
            ClosureObservation(
                obligation_id=obligation.obligation_id,
                source_state_id=after.manifest.source_states[0].source_state.state_id,
                status=ObligationStatus.SATISFIED,
                evidence_refs=evidence_refs[obligation.kind],
                observed=f"Fresh after-state evidence satisfies {obligation.kind.value}.",
            )
            for obligation in obligations
        ]
        closure = app.verify(intent, after, observations=observations)
        implementation_after = [
            candidate
            for candidate in after_similarity.candidates
            if {member.path for member in candidate.members}
            == {"src/ledgerlite/legacy.py", "src/ledgerlite/rules.py"}
        ]
        documentation_after = [
            candidate
            for candidate in after_similarity.candidates
            if {member.path for member in candidate.members}
            == {"README.md", "docs/api.md", "docs/legacy.md"}
        ]

        public_language = " ".join(
            render_review(exchange, format=ReviewOutputFormat.MARKDOWN)
            for exchange in exchanges.values()
        ).casefold()
        unsafe_terms = (
            "safe to delete",
            "automatically merge",
            "is redundant",
            "should delete",
            "approved for release",
        )
        mismatch = provider_state_mismatch(app, root, provider)
        adversarial = {
            "unknown_expansion": unknown_expansion,
            "budget_exhaustion": {
                "status": budgeted.dossier.status.value,
                "omissions_visible": bool(budgeted.dossier.omissions),
            },
            "unresolved_target": {
                "status": unresolved.dossier.status.value,
                "target_status": unresolved.dossier.boundary.targets[0].status.value,
                "omissions_visible": bool(unresolved.dossier.omissions),
            },
            "missing_closure_observations": {
                "outcome": incomplete.outcome.value,
                "unresolved": len(incomplete.unresolved_obligation_ids),
            },
            "stale_consumer_decision": {
                "current": stale_test_decision.current,
                "reasons": [reason.value for reason in stale_test_decision.stale_reasons],
            },
            "content_free_default": any(
                omission.code == "content_free_default" for omission in before.manifest.omissions
            ),
            "unsafe_language_absent": all(term not in public_language for term in unsafe_terms),
            "provider_state_mismatch": mismatch,
        }
        adversarial_visible = (
            unknown_expansion is not None
            and budgeted.dossier.status is DossierStatus.PARTIAL
            and unresolved.dossier.status is DossierStatus.BLOCKED
            and incomplete.outcome.value == "incomplete"
            and not stale_test_decision.current
            and bool(adversarial["content_free_default"])
            and bool(adversarial["unsafe_language_absent"])
            and bool(mismatch["visible"])
        )
        artifacts = {
            "before_session": before,
            "similarity": similarity,
            "consolidation": consolidation,
            "decision": decision,
            "intent": intent,
            "after_session": after,
            "change": change,
            "closure": closure,
        }
        artifact_bindings = {
            name: {
                "identity": artifact_identity(value),
                "sha256": sha256_digest(canonical_review_bytes(value)),
            }
            for name, value in artifacts.items()
        }
        mean_role_recall = statistics.mean(float(item["required_role_recall"]) for item in workflow_results)
        mean_path_recall = statistics.mean(float(item["selected_path_recall"]) for item in workflow_results)
        mean_reduction = statistics.mean(float(item["irrelevant_byte_reduction"]) for item in workflow_results)
        return {
            "schema_version": "1.0.0",
            "corpus": {
                "name": "ledger-lab-held-out",
                "generated_after_feature_delivery": True,
                "repository_id": REPOSITORY_ID,
                "before_source_state_id": before.manifest.source_states[0].source_state.state_id,
                "after_source_state_id": after.manifest.source_states[0].source_state.state_id,
                "file_count": len(files),
                "source_bytes": sum(files.values()),
                "decoy_files": 20,
                "research_provider": provider.provider_id,
                "research_nodes_before": len(graph.nodes),
                "research_nodes_after": len(after_graph.nodes),
            },
            "baseline": {
                "method": "inventory, search, whole-file reads, and manual cross-artifact join",
                "files_considered_per_task": len(files),
                "interactions_per_task": 4,
            },
            "session_build_ms": round(session_ms, 3),
            "similarity_latency_ms": round(similarity_ms, 3),
            "workflows": workflow_results,
            "lifecycle": {
                "similarity_candidates": len(similarity.candidates),
                "implementation_candidate_id": implementation_candidate.candidate_id,
                "test_candidate_id": test_candidate.candidate_id,
                "documentation_candidate_id": documentation_candidate.candidate_id,
                "consolidation_groups": len(consolidation.groups),
                "decision_current_before_change": decision_current.current,
                "decision_disposition": decision.disposition.value,
                "change_records": len(change.changes),
                "implementation_candidates_after": len(implementation_after),
                "documentation_candidates_after": len(documentation_after),
                "closure_outcome": closure.outcome.value,
            },
            "summary": {
                "workflow_count": len(workflow_results) + 3,
                "dossier_workflows": len(workflow_results),
                "mean_required_role_recall": round(mean_role_recall, 6),
                "mean_expected_path_recall": round(mean_path_recall, 6),
                "mean_irrelevant_byte_reduction": round(mean_reduction, 6),
                "all_dossier_decisions_correct": all(
                    bool(item["correct"]) for item in workflow_results
                ),
                "provenance_fidelity": all(
                    bool(item["provenance_fidelity"]) for item in workflow_results
                ),
                "human_review_comprehension": all(
                    bool(item["reviewer_comprehension"]) for item in workflow_results
                ),
                "all_adversarial_failures_visible": adversarial_visible,
                "post_change_closure": closure.outcome.value,
            },
            "adversarial": adversarial,
            "artifacts": artifact_bindings,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate()
    except (OSError, ValueError, StopIteration) as error:
        print(f"Error: {error}")
        return 1
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
