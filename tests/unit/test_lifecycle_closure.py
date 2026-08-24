from __future__ import annotations

from typing import Literal

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.lifecycle import (
    ChangeBoundaryKind,
    ChangeDimension,
    ChangeEvidence,
    ClosureObservation,
    ClosureOutcome,
    ClosureReport,
    ImplementationIntent,
    ObligationKind,
    ObligationStatus,
    build_change_dossier,
    build_change_evidence,
    build_implementation_intent,
    build_implementation_obligation,
    verify_implementation_closure,
)


def _digest(value: str) -> str:
    return sha256_digest(value.encode())


def _change(
    dimension: ChangeDimension,
    kind: ChangeBoundaryKind = ChangeBoundaryKind.MODIFIED,
) -> ChangeEvidence:
    return build_change_evidence(
        dimension=dimension,
        kinds=[kind],
        predecessor_refs=[f"before:{dimension.value}"],
        successor_refs=[f"after:{dimension.value}"],
        evidence_refs=[f"evidence:{dimension.value}"],
        lineage="exact",
        rationale=f"Observed {dimension.value} change.",
    )


def _intent() -> ImplementationIntent:
    obligations = [
        build_implementation_obligation(
            kind=kind,
            subject_ref=f"subject:{kind.value}",
            expectation=f"Expected {kind.value} outcome.",
            required_evidence_kinds=[kind.value],
        )
        for kind in (
            ObligationKind.OWNER,
            ObligationKind.CONTRACT,
            ObligationKind.CONSUMER,
            ObligationKind.CANDIDATE,
            ObligationKind.TEST,
            ObligationKind.DOCUMENTATION,
            ObligationKind.WORKFLOW,
        )
    ]
    return build_implementation_intent(
        repository_id="repository:fixture",
        before_source_state_id="state:before",
        before_dossier_id="dossier:before",
        before_evidence_digest=_digest("before-evidence"),
        before_provider_digests={"semantic": _digest("provider-before")},
        decision_overlay_id="decision:chosen",
        obligations=obligations,
        declared_unknowns=[],
    )


def _observations(
    intent: ImplementationIntent,
    source_state: str = "state:after",
) -> list[ClosureObservation]:
    return [
        ClosureObservation(
            obligation_id=item.obligation_id,
            source_state_id=source_state,
            status=ObligationStatus.SATISFIED,
            evidence_refs=[f"after-evidence:{item.kind.value}"],
            observed=f"Observed {item.kind.value} outcome.",
        )
        for item in intent.obligations
    ]


def _verify(
    intent: ImplementationIntent,
    observations: list[ClosureObservation],
    *,
    provider_completeness: dict[str, Literal["complete", "partial", "unavailable", "unknown"]] | None = None,
    after_provider_digests: dict[str, str] | None = None,
    evidence_kinds_by_ref: dict[str, set[str]] | None = None,
) -> ClosureReport:
    obligations = {item.obligation_id: item for item in intent.obligations}
    inferred_kinds = {
        reference: set(obligations[observation.obligation_id].required_evidence_kinds)
        for observation in observations
        for reference in observation.evidence_refs
    }
    return verify_implementation_closure(
        intent,
        after_source_state_id="state:after",
        after_source_state_digest=_digest("after-source"),
        after_provider_digests=after_provider_digests or {"semantic": _digest("provider-after")},
        provider_completeness=provider_completeness or {"semantic": "complete"},
        observations=observations,
        evidence_kinds_by_ref=inferred_kinds if evidence_kinds_by_ref is None else evidence_kinds_by_ref,
    )


def test_change_dossier_distinguishes_every_dimension() -> None:
    changes = [_change(dimension) for dimension in ChangeDimension]
    dossier = build_change_dossier(
        repository_id="repository:fixture",
        before_source_state_id="state:before",
        after_source_state_id="state:after",
        before_dossier_id="dossier:before",
        after_dossier_id="dossier:after",
        before_evidence_digest=_digest("before"),
        after_evidence_digest=_digest("after"),
        before_provider_digests={"semantic": _digest("provider-before")},
        after_provider_digests={"semantic": _digest("provider-after")},
        changes=changes,
        affected_evidence_refs=["contracts", "tests", "documentation"],
        blast_radius_refs=["consumer:cli", "workflow:ci"],
        limitations=[],
    )

    assert {item.dimension for item in dossier.changes} == set(ChangeDimension)
    assert dossier.before_source_state_id == "state:before"
    assert dossier.after_source_state_id == "state:after"


@pytest.mark.parametrize(
    "kind",
    [
        ChangeBoundaryKind.MOVED,
        ChangeBoundaryKind.RENAMED,
        ChangeBoundaryKind.DELETED,
        ChangeBoundaryKind.SPLIT,
        ChangeBoundaryKind.MERGED,
        ChangeBoundaryKind.REEXPORTED,
        ChangeBoundaryKind.GENERATED,
    ],
)
def test_change_boundaries_preserve_lineage_and_uncertainty(kind: ChangeBoundaryKind) -> None:
    change = build_change_evidence(
        dimension=ChangeDimension.ENTITY,
        kinds=[kind],
        predecessor_refs=["entity:before"],
        successor_refs=[] if kind is ChangeBoundaryKind.DELETED else ["entity:after"],
        evidence_refs=["lineage:observation"],
        lineage="candidate" if kind in {ChangeBoundaryKind.SPLIT, ChangeBoundaryKind.MERGED} else "exact",
        rationale="Typed boundary evidence.",
    )

    assert change.kinds == [kind]
    assert change.lineage in {"exact", "candidate"}


def test_absent_baseline_and_provider_mismatch_must_be_explicit() -> None:
    absent = build_change_evidence(
        dimension=ChangeDimension.ENTITY,
        kinds=[ChangeBoundaryKind.ABSENT_BASELINE],
        predecessor_refs=[],
        successor_refs=["state:after"],
        evidence_refs=[],
        lineage="unavailable",
        rationale="No baseline was supplied.",
    )
    mismatch = build_change_evidence(
        dimension=ChangeDimension.RUNTIME,
        kinds=[ChangeBoundaryKind.PROVIDER_MISMATCH],
        predecessor_refs=["provider:before"],
        successor_refs=["provider:after"],
        evidence_refs=[],
        lineage="unavailable",
        rationale="Provider sets differ.",
    )
    dossier = build_change_dossier(
        repository_id="repository:fixture",
        before_source_state_id=None,
        after_source_state_id="state:after",
        before_dossier_id=None,
        after_dossier_id="dossier:after",
        before_evidence_digest=None,
        after_evidence_digest=_digest("after"),
        before_provider_digests={},
        after_provider_digests={"semantic": _digest("provider-after")},
        changes=[absent, mismatch],
        affected_evidence_refs=[],
        blast_radius_refs=[],
        limitations=["Comparison is incomplete."],
    )

    assert {item.lineage for item in dossier.changes} == {"unavailable"}


def test_all_typed_obligations_can_close_at_one_exact_after_state() -> None:
    intent = _intent()
    report = _verify(intent, _observations(intent))

    assert report.outcome is ClosureOutcome.CLOSED
    assert report.after_source_state_id == "state:after"
    assert not report.discrepancy_obligation_ids
    assert "not complete safety" in report.limitations[0]


def test_passing_test_does_not_close_other_obligations_or_partial_providers() -> None:
    intent = _intent()
    test_obligation = next(item for item in intent.obligations if item.kind is ObligationKind.TEST)
    report = _verify(
        intent,
        [
            ClosureObservation(
                obligation_id=test_obligation.obligation_id,
                source_state_id="state:after",
                status=ObligationStatus.SATISFIED,
                evidence_refs=["junit:test-pass"],
                observed="Selected test passed.",
            )
        ],
        provider_completeness={"semantic": "partial"},
    )

    assert report.outcome is ClosureOutcome.INCOMPLETE
    assert len(report.unresolved_obligation_ids) == len(intent.obligations) - 1


def test_unknown_or_wrong_kind_after_evidence_cannot_close() -> None:
    intent = _intent()
    observations = _observations(intent)
    report = _verify(intent, observations, evidence_kinds_by_ref={})

    assert report.outcome is ClosureOutcome.INCOMPLETE
    assert all(item.status is ObligationStatus.UNRESOLVED for item in report.observations)
    assert all("absent from the after session" in item.limitations[0] for item in report.observations)


def test_stale_or_reused_pre_change_evidence_cannot_close() -> None:
    intent = _intent()
    stale = _verify(intent, _observations(intent, "state:before"))

    assert stale.outcome is ClosureOutcome.STALE
    assert all(item.status is ObligationStatus.STALE for item in stale.observations)
    with pytest.raises(ValueError, match="reuse pre-change"):
        _verify(
            intent,
            _observations(intent),
            after_provider_digests={"semantic": _digest("provider-before")},
        )
