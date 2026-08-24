from __future__ import annotations

from typing import Any

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.lifecycle import (
    CandidateGranularity,
    ConsolidationDossier,
    ConsolidationQuestion,
    DecisionDisposition,
    DecisionOverlay,
    OverlayEvaluation,
    OverlayStaleReason,
    ReviewEvidenceGroup,
    SimilarityCandidate,
    SimilarityMethod,
    build_consolidation_dossier,
    build_decision_overlay,
    evaluate_decision_overlay,
    visible_candidates,
)
from tests.unit.test_lifecycle_similarity import _candidate


def _digest(value: str) -> str:
    return sha256_digest(value.encode())


def _groups() -> list[ReviewEvidenceGroup]:
    return [
        ReviewEvidenceGroup(
            question=question,
            evidence_refs=[f"evidence:{question.value}"],
            summary=f"Review {question.value.replace('_', ' ')}.",
            limitations=[] if question is not ConsolidationQuestion.OMISSIONS else ["Runtime evidence partial."],
        )
        for question in ConsolidationQuestion
    ]


def _overlay(candidate: SimilarityCandidate | None = None) -> DecisionOverlay:
    candidate = candidate or _candidate("state:after", CandidateGranularity.DEFINITION, SimilarityMethod.NORMALIZED)
    return build_decision_overlay(
        candidate=candidate,
        relevant_source_digest=_digest("candidate-member-source"),
        provider_digests={"semantic": _digest("semantic-run")},
        evidence_digests={
            "contracts": _digest("contracts"),
            "tests": _digest("test-intent"),
            "documentation": _digest("documentation"),
            "runtime": _digest("runtime"),
        },
        decision_schema_version="consumer.example/1",
        decision_policy_digest=_digest("decision-policy"),
        owner_namespace="example.review-team",
        disposition=DecisionDisposition.ACCEPTED_DUPLICATE,
        rationale="Different optimisations are intentionally retained.",
        preserved_divergence=["performance contract"],
    )


def _evaluate(
    overlay: DecisionOverlay,
    candidate: SimilarityCandidate | None = None,
    **changes: Any,
) -> OverlayEvaluation:
    values: dict[str, Any] = {
        "candidate": candidate
        or _candidate("state:after", CandidateGranularity.DEFINITION, SimilarityMethod.NORMALIZED),
        "relevant_source_digest": _digest("candidate-member-source"),
        "provider_digests": {"semantic": _digest("semantic-run")},
        "evidence_digests": {
            "contracts": _digest("contracts"),
            "tests": _digest("test-intent"),
            "documentation": _digest("documentation"),
            "runtime": _digest("runtime"),
        },
        "decision_schema_version": "consumer.example/1",
        "decision_policy_digest": _digest("decision-policy"),
    }
    values.update(changes)
    return evaluate_decision_overlay(overlay, **values)


def test_consolidation_dossier_requires_every_review_question() -> None:
    candidate = _candidate("state:after", CandidateGranularity.DEFINITION, SimilarityMethod.NORMALIZED)
    dossier = build_consolidation_dossier(
        repository_id="repository:fixture",
        base_dossier_id="dossier:base",
        base_dossier_digest=_digest("base-dossier"),
        candidate=candidate,
        groups=_groups(),
    )

    assert set(item.question for item in dossier.groups) == set(ConsolidationQuestion)
    assert dossier.disposition_authority == "consumer_overlay_only"
    assert ConsolidationDossier.model_validate_json(dossier.model_dump_json()) == dossier
    with pytest.raises(ValueError, match="every review question"):
        build_consolidation_dossier(
            repository_id="repository:fixture",
            base_dossier_id="dossier:base",
            base_dossier_digest=_digest("base-dossier"),
            candidate=candidate,
            groups=_groups()[:-1],
        )


def test_overlay_is_portable_consumer_state_and_reapplies_exactly() -> None:
    overlay = _overlay()
    loaded = DecisionOverlay.model_validate_json(overlay.model_dump_json())
    evaluation = _evaluate(loaded)

    assert evaluation.current
    assert not evaluation.stale_reasons
    assert loaded.owner_namespace == "example.review-team"
    assert loaded.disposition is DecisionDisposition.ACCEPTED_DUPLICATE


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"relevant_source_digest": _digest("changed-source")}, OverlayStaleReason.SOURCE_STATE),
        ({"provider_digests": {"semantic": _digest("changed-provider")}}, OverlayStaleReason.PROVIDER),
        ({"evidence_digests": {"tests": _digest("changed-tests")}}, OverlayStaleReason.EVIDENCE),
        ({"decision_schema_version": "consumer.example/2"}, OverlayStaleReason.DECISION_SCHEMA),
        ({"decision_policy_digest": _digest("changed-policy")}, OverlayStaleReason.DECISION_POLICY),
    ],
)
def test_relevant_overlay_changes_are_explained(change: dict[str, Any], reason: OverlayStaleReason) -> None:
    evaluation = _evaluate(_overlay(), **change)

    assert not evaluation.current
    assert reason in evaluation.stale_reasons
    assert reason.value in evaluation.explanation


def test_unrelated_source_state_change_does_not_invalidate_reconciled_candidate() -> None:
    before = _candidate("state:before", CandidateGranularity.DEFINITION, SimilarityMethod.NORMALIZED)
    after = _candidate("state:after", CandidateGranularity.DEFINITION, SimilarityMethod.NORMALIZED)
    evaluation = _evaluate(_overlay(before), candidate=after)

    assert before.candidate_id != after.candidate_id
    assert evaluation.current


def test_changed_candidate_evidence_reopens_accepted_duplicate() -> None:
    original = _candidate("state:after", CandidateGranularity.DEFINITION, SimilarityMethod.NORMALIZED)
    changed = _candidate(
        "state:after",
        CandidateGranularity.DEFINITION,
        SimilarityMethod.NORMALIZED,
        second_digest="changed",
    )
    overlay = _overlay(original)
    current = _evaluate(overlay, candidate=original)
    stale = _evaluate(overlay, candidate=changed)

    assert visible_candidates([original], [overlay], [current]) == []
    assert visible_candidates([original], [overlay], [current], show_accepted_duplicates=True) == [original]
    assert visible_candidates([changed], [overlay], [stale]) == [changed]
    assert stale.stale_reasons == [OverlayStaleReason.CANDIDATE]
