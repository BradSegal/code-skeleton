from __future__ import annotations

from pathlib import Path

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.evidence import EvidenceStrength
from anatomize.index import build_repository_index
from anatomize.lifecycle import (
    CandidateGranularity,
    CandidateRegion,
    ClosureObservation,
    ClosureOutcome,
    ConsolidationQuestion,
    DecisionDisposition,
    ObligationKind,
    ObligationStatus,
    ReviewEvidenceGroup,
    SimilarityMethod,
    build_consolidation_dossier,
    build_decision_overlay,
    build_implementation_intent,
    build_implementation_obligation,
    build_similarity_candidate,
    evaluate_decision_overlay,
    verify_implementation_closure,
)


def _digest(value: str) -> str:
    return sha256_digest(value.encode())


def _candidate(state: str, content: str = "same"):  # type: ignore[no-untyped-def]
    return build_similarity_candidate(
        source_state_id=state,
        granularity=CandidateGranularity.DEFINITION,
        method=SimilarityMethod.NORMALIZED,
        method_version="assurance/1",
        configuration_digest=_digest("assurance-policy"),
        provider_run_id=None,
        threshold=None,
        token_count=40,
        members=[
            CandidateRegion(
                source_state_id=state,
                path=path,
                start_line=1,
                end_line=3,
                entity_id=entity,
                role="implementation",
                language="python",
                content_digest=_digest(content),
            )
            for path, entity in (("src/mean.py", "function:mean"), ("src/median.py", "function:median"))
        ],
        aligned_hunks=[],
        unmatched_member_regions=[],
        strength=EvidenceStrength.CONSERVATIVE,
        limitations=["Runtime and scientific intent remain independently reviewable."],
        rationale="Similarity candidate only; no disposition is inferred.",
    )


@pytest.mark.parametrize(
    ("case", "disposition", "protected_difference"),
    [
        ("exact-duplicate", DecisionDisposition.CONSOLIDATE, "none declared"),
        ("near-match", DecisionDisposition.INVESTIGATE, "unresolved behavior"),
        ("legitimate-divergence", DecisionDisposition.SEPARATE, "different contract"),
        ("compatibility-path", DecisionDisposition.KEEP, "compatibility surface"),
        ("generated-code", DecisionDisposition.ACCEPTED_DUPLICATE, "generated ownership"),
        ("research-variant", DecisionDisposition.SEPARATE, "different estimand"),
        ("false-coverage-reassurance", DecisionDisposition.KEEP, "coverage is not intent"),
        ("unresolved-runtime", DecisionDisposition.INVESTIGATE, "runtime unavailable"),
    ],
)
def test_labelled_lifecycle_corpus_preserves_consumer_reasoning(
    case: str,
    disposition: DecisionDisposition,
    protected_difference: str,
) -> None:
    candidate = _candidate("state:corpus")
    dossier = build_consolidation_dossier(
        repository_id="repository:corpus",
        base_dossier_id=f"dossier:{case}",
        base_dossier_digest=_digest(case),
        candidate=candidate,
        groups=[
            ReviewEvidenceGroup(
                question=question,
                evidence_refs=[f"{case}:{question.value}"],
                summary=f"Evidence for {question.value}.",
                limitations=[protected_difference]
                if question in {ConsolidationQuestion.CONFLICTS, ConsolidationQuestion.OMISSIONS}
                else [],
            )
            for question in ConsolidationQuestion
        ],
    )
    overlay = build_decision_overlay(
        candidate=candidate,
        relevant_source_digest=_digest("same sources"),
        provider_digests={},
        evidence_digests={"case": _digest(case), "protected": _digest(protected_difference)},
        decision_schema_version="assurance/1",
        decision_policy_digest=_digest("assurance-decision-policy"),
        owner_namespace="assurance.fixture",
        disposition=disposition,
        rationale=f"Labelled consumer decision preserves {protected_difference}.",
    )

    assert dossier.disposition_authority == "consumer_overlay_only"
    assert overlay.disposition is disposition
    assert evaluate_decision_overlay(
        overlay,
        candidate=candidate,
        relevant_source_digest=_digest("same sources"),
        provider_digests={},
        evidence_digests={"case": _digest(case), "protected": _digest(protected_difference)},
        decision_schema_version="assurance/1",
        decision_policy_digest=_digest("assurance-decision-policy"),
    ).current


def test_rejected_consolidation_runs_to_preservation_closure(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mean.py").write_text("def centre(x):\n    return sum(x) / len(x)\n")
    (tmp_path / "src" / "median.py").write_text("def centre(x):\n    return sorted(x)[len(x) // 2]\n")
    (tmp_path / "README.md").write_text("# Statistical centres\nMean and median answer different questions.\n")
    before = build_repository_index(tmp_path)
    before_state = f"state:{before.source_state.fact_digest}"
    candidate = _candidate(before_state)
    dossier = build_consolidation_dossier(
        repository_id="repository:research-variant",
        base_dossier_id="dossier:research-variant",
        base_dossier_digest=_digest(before.model_dump_json()),
        candidate=candidate,
        groups=[
            ReviewEvidenceGroup(
                question=question,
                evidence_refs=[f"research:{question.value}"],
                summary=(
                    "The estimands and edge-case contracts differ."
                    if question in {ConsolidationQuestion.PUBLIC_CONTRACTS, ConsolidationQuestion.TEST_INTENT}
                    else f"Reviewed {question.value}."
                ),
            )
            for question in ConsolidationQuestion
        ],
    )
    decision = build_decision_overlay(
        candidate=candidate,
        relevant_source_digest=_digest("mean-and-median-sources"),
        provider_digests={},
        evidence_digests={"contracts": _digest("different-estimands")},
        decision_schema_version="assurance/1",
        decision_policy_digest=_digest("policy"),
        owner_namespace="research.review",
        disposition=DecisionDisposition.SEPARATE,
        rationale="Preserve distinct estimands and behavior.",
    )
    obligations = [
        build_implementation_obligation(
            kind=kind,
            subject_ref=f"research:{kind.value}",
            expectation=f"Preserve both variants' {kind.value} evidence.",
            required_evidence_kinds=[kind.value],
        )
        for kind in (ObligationKind.OWNER, ObligationKind.CONTRACT, ObligationKind.TEST, ObligationKind.DOCUMENTATION)
    ]
    intent = build_implementation_intent(
        repository_id="repository:research-variant",
        before_source_state_id=before_state,
        before_dossier_id=dossier.dossier_id,
        before_evidence_digest=_digest(before.model_dump_json()),
        before_provider_digests={"baseline": _digest(before.model_dump_json())},
        decision_overlay_id=decision.decision_id,
        obligations=obligations,
        declared_unknowns=[],
    )

    (tmp_path / "REVIEW.md").write_text("# Decision\nBoth estimands are intentionally retained.\n")
    after = build_repository_index(tmp_path)
    after_state = f"state:{after.source_state.fact_digest}"
    after_digest = _digest(after.model_dump_json())
    report = verify_implementation_closure(
        intent,
        after_source_state_id=after_state,
        after_source_state_digest=after_digest,
        after_provider_digests={"baseline": after_digest},
        provider_completeness={"baseline": "complete"},
        observations=[
            ClosureObservation(
                obligation_id=item.obligation_id,
                source_state_id=after_state,
                status=ObligationStatus.SATISFIED,
                evidence_refs=[f"after:{item.kind.value}"],
                observed=f"Both research variants retain {item.kind.value} evidence.",
            )
            for item in obligations
        ],
        evidence_kinds_by_ref={
            f"after:{item.kind.value}": {item.kind.value} for item in obligations
        },
    )

    assert decision.disposition is DecisionDisposition.SEPARATE
    assert report.outcome is ClosureOutcome.CLOSED
    assert any("estimands" in group.summary for group in dossier.groups)


def test_public_lifecycle_artifacts_do_not_claim_automatic_safety_or_quality() -> None:
    candidate = _candidate("state:language-audit")
    dossier = build_consolidation_dossier(
        repository_id="repository:language-audit",
        base_dossier_id="dossier:language-audit",
        base_dossier_digest=_digest("base"),
        candidate=candidate,
        groups=[
            ReviewEvidenceGroup(question=question, evidence_refs=[], summary="Evidence is available for review.")
            for question in ConsolidationQuestion
        ],
    )
    rendered = dossier.model_dump_json().casefold()

    for unsupported in ("safe to delete", "is redundant", "should merge", "high quality", "complete safety"):
        assert unsupported not in rendered
