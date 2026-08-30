from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.dossiers import DossierContext, DossierProfile, DossierStatus, TargetKind
from anatomize.evidence import (
    CompletenessRecord,
    CompletenessStatus,
    EvidenceStrength,
    ProviderRunStatus,
    RepositoryEntity,
    SymbolEntity,
)
from anatomize.evidence import (
    TestEntity as EvidenceTestEntity,
)
from anatomize.index import build_repository_index
from anatomize.lifecycle import (
    CandidateGranularity,
    CandidateRegion,
    ClosureObservation,
    ConsolidationQuestion,
    DecisionDisposition,
    ObligationKind,
    ObligationStatus,
    SimilarityCandidate,
    SimilarityMethod,
    build_implementation_obligation,
    build_similarity_candidate,
)
from anatomize.providers import (
    AuthorityLevel,
    InvocationAuthority,
    InvocationMode,
    NetworkPolicy,
    ProviderEvidenceBatch,
    ProviderScope,
    ProviderToolIdentity,
    build_provider_envelope,
    canonical_provider_bytes,
    repository_index_provider_envelope,
)
from anatomize.review import (
    DossierExchange,
    ReviewApplication,
    ReviewApplicationError,
    ReviewOutputFormat,
    canonical_review_bytes,
    parse_review_artifact,
    render_review,
    target_selector,
)
from anatomize.sessions import parse_session_bundle

pytestmark = pytest.mark.unit


def _repository(root: Path) -> Path:
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "maths.py").write_text(
        "def total(values: list[int]) -> int:\n    return sum(values)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_maths.py").write_text(
        "from src.maths import total\n\ndef test_total() -> None:\n    assert total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Example\n\nUse `total`.\n", encoding="utf-8")
    return root


def _candidate(state_id: str) -> SimilarityCandidate:
    return build_similarity_candidate(
        source_state_id=state_id,
        granularity=CandidateGranularity.DEFINITION,
        method=SimilarityMethod.NORMALIZED,
        method_version="test:1",
        configuration_digest=sha256_digest(b"similarity-test"),
        members=[
            CandidateRegion(
                source_state_id=state_id,
                path="src/maths.py",
                start_line=1,
                end_line=2,
                entity_id="symbol:total",
                content_digest=sha256_digest(b"left"),
            ),
            CandidateRegion(
                source_state_id=state_id,
                path="tests/test_maths.py",
                start_line=3,
                end_line=4,
                entity_id="symbol:test-total",
                content_digest=sha256_digest(b"right"),
            ),
        ],
        strength=EvidenceStrength.CONSERVATIVE,
        limitations=["Structural similarity does not establish equivalent intent."],
        rationale="A bounded fixture candidate.",
    )


def test_session_and_dossier_are_deterministic_content_free_and_round_trip(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    application = ReviewApplication()

    first = application.start(root, repository_id="repository:review-test")
    second = application.start(root, repository_id="repository:review-test")

    assert first == second
    assert first.manifest.session_id == second.manifest.session_id
    assert all(item.role.value != "source_slice" for item in first.manifest.artifacts)
    assert {item.provider_id for item in first.manifest.providers} == {
        "anatomize.repository-index",
        "anatomize.source-inventory",
    }
    assert parse_session_bundle(canonical_review_bytes(first)) == first

    exchange = application.dossier(first, profile=DossierProfile.ORIENTATION)
    raw = canonical_review_bytes(exchange)
    parsed = parse_review_artifact(raw, expected_type="anatomize.dossier-exchange")
    assert isinstance(parsed, DossierExchange)
    assert parsed == exchange
    assert exchange.dossier.status is DossierStatus.COMPLETE
    assert exchange.dossier.expansions


def test_lightweight_source_state_matches_session_and_changes_with_source(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    application = ReviewApplication()

    current = application.source_state(root, repository_id="repository:review-test")
    session = application.start(root, repository_id="repository:review-test")

    assert current == session.manifest.source_states[0].source_state

    (root / "src" / "maths.py").write_text("def total(values: list[int]) -> int:\n    return 0\n", encoding="utf-8")
    assert application.source_state(root, repository_id="repository:review-test").state_id != current.state_id


def test_source_state_and_builtin_inventory_cover_research_files(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    (root / "R").mkdir()
    (root / "tests" / "testthat").mkdir()
    (root / "data").mkdir()
    (root / "R" / "estimate.R").write_text(
        "#' @export\nestimate <- function(x) sum(x)\n",
        encoding="utf-8",
    )
    (root / "tests" / "testthat" / "test-estimate.R").write_text(
        'test_that("estimate sums", { expect_equal(estimate(c(1, 2)), 3) })\n',
        encoding="utf-8",
    )
    (root / "data" / "public.csv").write_text("x\n1\n", encoding="utf-8")
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"language": "python"}},
        "cells": [
            {
                "id": "analysis",
                "cell_type": "code",
                "metadata": {"tags": ["parameters"]},
                "source": "result = total([1, 2])",
                "execution_count": None,
                "outputs": [],
            }
        ],
    }
    (root / "analysis.ipynb").write_text(json.dumps(notebook), encoding="utf-8")

    before = build_repository_index(root)
    (root / "R" / "estimate.R").write_text("estimate <- function(x) mean(x)\n", encoding="utf-8")
    after = build_repository_index(root)
    assert before.source_state.fact_digest != after.source_state.fact_digest
    assert {item.path for item in after.files}.issuperset(
        {"R/estimate.R", "analysis.ipynb", "data/public.csv"}
    )

    bundle = ReviewApplication().start(root, repository_id="repository:research-source")
    evidence = DossierContext.from_bundle(bundle).evidence[0]
    assert any(
        isinstance(item, SymbolEntity) and item.language == "r" and item.name == "estimate"
        for item in evidence.entities
    )
    assert any(
        isinstance(item, EvidenceTestEntity) and item.framework == "testthat"
        for item in evidence.entities
    )
    assert any(getattr(item, "range_kind", "") == "notebook_code_cell" for item in evidence.entities)


def test_builtin_inventory_degrades_invalid_test_at_path_scope(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    (root / "tests" / "test_invalid.py").write_text("def test_invalid(:\n", encoding="utf-8")

    bundle = ReviewApplication().start(root, repository_id="repository:invalid-test")
    evidence = DossierContext.from_bundle(bundle).evidence
    limitation = next(
        item
        for artifact in evidence
        for item in artifact.limitations
        if item.code == "python_test_parse"
    )
    omission = next(
        item
        for artifact in evidence
        for item in artifact.omissions
        if item.reason == limitation.summary
    )

    assert limitation.summary == "python_test_syntax_invalid: Cannot parse test source at line 1"
    assert omission.scope_type == "path"
    assert omission.scope_id == "tests/test_invalid.py"


def test_explicit_provider_artifact_is_bound_without_discovery_or_execution(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    repository_id = "repository:review-test"
    index = build_repository_index(root)
    baseline = repository_index_provider_envelope(
        index,
        repository_id=repository_id,
        policy_digest=sha256_digest(b"test-policy"),
    )
    state = baseline.source_states[0]
    repository_entity = next(
        item for item in baseline.payload.entities if isinstance(item, RepositoryEntity)
    ).model_copy(update={"provider_run_ids": []})
    run_id = "provider-run:explicit-fixture"
    imported = build_provider_envelope(
        provider_run_id=run_id,
        provider_id="fixture.explicit",
        provider_version="1.0.0",
        tool=ProviderToolIdentity(name="fixture", version="1.0.0"),
        capabilities=["fixture_fact"],
        languages=["python"],
        repository_id=repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=sha256_digest(b"fixture-config"),
        scope=ProviderScope(
            scope_id="scope:explicit-fixture",
            source_state_ids=[state.state_id],
            entity_ids=[repository_entity.entity_id],
            evidence_families=["fixture_fact"],
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.ARTIFACT_IMPORT,
            level=AuthorityLevel.A1_ARTIFACT,
            policy_digest=sha256_digest(b"explicit-import"),
            network_policy=NetworkPolicy.NOT_APPLICABLE,
        ),
        status=ProviderRunStatus.COMPLETE,
        payload=ProviderEvidenceBatch(
            entities=[repository_entity],
            completeness=[
                CompletenessRecord(
                    completeness_id="completeness:explicit-fixture",
                    source_state_id=state.state_id,
                    provider_run_id=run_id,
                    scope_type="repository",
                    scope_id=repository_entity.entity_id,
                    evidence_families=["fixture_fact"],
                    status=CompletenessStatus.COMPLETE,
                )
            ],
        ),
    )

    bundle = ReviewApplication().start(
        root,
        repository_id=repository_id,
        provider_envelopes=[imported],
    )

    assert {item.provider_id for item in bundle.manifest.providers} == {
        "anatomize.repository-index",
        "anatomize.source-inventory",
        "fixture.explicit",
    }
    assert {item.role.value for item in bundle.manifest.artifacts} == {"normalized_evidence"}
    imported_state = next(
        item
        for item in bundle.manifest.source_states[0].provider_states
        if item.provider_id == "fixture.explicit"
    )
    assert imported_state.artifacts[0].digest == sha256_digest(canonical_provider_bytes(imported))


def test_session_projects_baseline_similarity_without_source_reread(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    repeated = (
        "def normalize(values: list[int]) -> list[int]:\n"
        "    selected = [value for value in values if value > 0]\n"
        "    ordered = sorted(selected)\n"
        "    total = sum(ordered)\n"
        "    return [value + total for value in ordered]\n"
    )
    (root / "src" / "left.py").write_text(repeated, encoding="utf-8")
    (root / "src" / "right.py").write_text(repeated.replace("normalize", "prepare"), encoding="utf-8")
    application = ReviewApplication()
    bundle = application.start(root, repository_id="repository:review-test")

    similarity = application.similarity(bundle)

    assert similarity.repository_id == bundle.manifest.repository_id
    assert similarity.source_state_id == bundle.manifest.source_states[0].source_state.state_id
    assert len(similarity.candidates) == 1
    assert {item.path for item in similarity.candidates[0].members} == {"src/left.py", "src/right.py"}
    assert parse_review_artifact(canonical_review_bytes(similarity)) == similarity
    assert application.check_model(similarity).valid


def test_query_expansion_and_accessible_review_artifacts_share_one_result(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    application = ReviewApplication()
    bundle = application.start(root, repository_id="repository:review-test")
    base = application.dossier(bundle, profile=DossierProfile.ORIENTATION)
    exchange = application.dossier(
        bundle,
        profile=DossierProfile.LOCALISATION,
        targets=[target_selector("maths.total", kind=TargetKind.SYMBOL)],
    )
    expanded = application.expand(
        bundle,
        base,
        action_id=base.dossier.expansions[0].action_id,
    )

    assert expanded.dossier.base_dossier_id == base.dossier.dossier_id
    assert expanded.request.expansion is not None
    text = render_review(exchange, format=ReviewOutputFormat.TEXT, width=40)
    markdown = render_review(exchange, format=ReviewOutputFormat.MARKDOWN)
    assert "\x1b" not in text
    assert "src/maths.py:1:0" in text
    assert "## Omissions and unknowns" in markdown
    assert "## Expansion actions" in markdown
    assert "provider run(s)" not in markdown
    assert "canonical repository entity" not in markdown
    assert "specialist" not in markdown.casefold()
    assert exchange.dossier.dossier_id in markdown
    assert exchange.dossier.source_state_ids[0] in markdown
    assert all(not line.endswith(" ") for line in text.splitlines())


def test_change_consolidation_overlay_and_closure_keep_judgement_consumer_owned(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    application = ReviewApplication()
    before = application.start(root, repository_id="repository:review-test")
    before_exchange = application.dossier(
        before,
        profile=DossierProfile.IMPLEMENTATION,
        targets=[target_selector("src/maths.py", kind=TargetKind.FILE)],
    )
    state_id = before.manifest.source_states[0].source_state.state_id
    candidate = _candidate(state_id)

    consolidation = application.consolidation(before_exchange, candidate)
    assert {item.question for item in consolidation.groups} == set(ConsolidationQuestion)
    assert consolidation.disposition_authority == "consumer_overlay_only"
    overlay = application.decision_overlay(
        before,
        candidate,
        owner_namespace="review:test",
        disposition=DecisionDisposition.KEEP,
        rationale="The test and implementation have different responsibilities.",
        preserved_divergence=["production behavior versus verification intent"],
    )
    evaluation = application.evaluate_overlay(overlay, before, candidate)
    assert evaluation.current
    assert parse_review_artifact(canonical_review_bytes(evaluation)) == evaluation
    assert application.check_model(evaluation).valid

    obligation = build_implementation_obligation(
        kind=ObligationKind.TEST,
        subject_ref="src/maths.py",
        expectation="The selected test remains passing after the implementation.",
        required_evidence_kinds=["test_runtime"],
    )
    intent = application.implementation_intent(
        before,
        before_exchange,
        obligations=[obligation],
        decision_overlay_id=overlay.decision_id,
    )
    (root / "src" / "maths.py").write_text(
        "def total(values: list[int]) -> int:\n    return sum(value for value in values)\n",
        encoding="utf-8",
    )
    after = application.start(root, repository_id="repository:review-test")
    change = application.change(before, after)
    assert change.changes
    assert "file:src/maths.py" in change.affected_evidence_refs
    after_state = after.manifest.source_states[0].source_state.state_id
    closure = application.verify(
        intent,
        after,
        observations=[
            ClosureObservation(
                obligation_id=obligation.obligation_id,
                source_state_id=after_state,
                status=ObligationStatus.SATISFIED,
                evidence_refs=["test-runtime:selected-test"],
                observed="The declared selected test passed.",
            )
        ],
    )
    assert closure.outcome.value == "incomplete"
    assert closure.observations[0].status is ObligationStatus.UNRESOLVED
    assert "absent from the after session" in closure.observations[0].limitations[0]


def test_application_overlay_ignores_unrelated_files_but_reopens_changed_members(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    application = ReviewApplication()
    before = application.start(root, repository_id="repository:review-test")
    before_candidate = _candidate(before.manifest.source_states[0].source_state.state_id)
    overlay = application.decision_overlay(
        before,
        before_candidate,
        owner_namespace="review:test",
        disposition=DecisionDisposition.KEEP,
        rationale="The candidate members have distinct responsibilities.",
    )

    (root / "README.md").write_text("# Example\n\nAn unrelated clarification.\n", encoding="utf-8")
    after = application.start(root, repository_id="repository:review-test")
    after_candidate = _candidate(after.manifest.source_states[0].source_state.state_id)

    assert application.evaluate_overlay(overlay, after, after_candidate).current
    changed_member = after_candidate.model_copy(
        update={
            "members": [
                after_candidate.members[0].model_copy(update={"content_digest": sha256_digest(b"changed")}),
                after_candidate.members[1],
            ]
        }
    )
    evaluation = application.evaluate_overlay(overlay, after, changed_member)
    assert not evaluation.current
    assert {reason.value for reason in evaluation.stale_reasons} == {
        "source_state_changed",
        "candidate_or_member_evidence_changed",
        "referenced_evidence_changed",
    }


def test_stable_failures_reject_wrong_state_and_corrupt_artifacts(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    application = ReviewApplication()
    bundle = application.start(root, repository_id="repository:review-test")
    state_id = bundle.manifest.source_states[0].source_state.state_id
    candidate = _candidate(state_id)
    payload = json.loads(canonical_review_bytes(bundle))
    payload["manifest"]["repository_id"] = "repository:tampered"

    with pytest.raises(ReviewApplicationError, match="failed validation") as captured:
        parse_review_artifact(json.dumps(payload).encode("utf-8"))
    assert captured.value.code == "review_artifact_invalid"

    wrong = candidate.model_copy(update={"source_state_id": "state:wrong"})
    with pytest.raises(ReviewApplicationError) as mismatch:
        application.decision_overlay(
            bundle,
            wrong,
            owner_namespace="review:test",
            disposition=DecisionDisposition.INVESTIGATE,
            rationale="Needs review.",
        )
    assert mismatch.value.code == "candidate_state_mismatch"
