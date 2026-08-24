from __future__ import annotations

from pathlib import Path

from anatomize._artifacts import sha256_digest
from anatomize.evidence import EvidenceStrength
from anatomize.index import build_repository_index
from anatomize.lifecycle import (
    CandidateGranularity,
    CandidateRegion,
    ChangeBoundaryKind,
    ChangeDimension,
    ClosureObservation,
    ClosureOutcome,
    ConsolidationQuestion,
    ObligationKind,
    ObligationStatus,
    ReviewEvidenceGroup,
    SimilarityMethod,
    build_change_dossier,
    build_change_evidence,
    build_consolidation_dossier,
    build_implementation_intent,
    build_implementation_obligation,
    build_similarity_candidate,
    verify_implementation_closure,
)


def _digest(value: str) -> str:
    return sha256_digest(value.encode())


def test_bounded_repository_change_runs_from_candidate_dossier_to_exact_closure(tmp_path: Path) -> None:
    source = "def normalise(value: str) -> str:\n    return value.strip().casefold()\n"
    (tmp_path / "a.py").write_text(source)
    (tmp_path / "b.py").write_text(source.replace("normalise", "canonicalise"))
    (tmp_path / "test_core.py").write_text(
        "from a import normalise\n\ndef test_normalise():\n    assert normalise(' A ') == 'a'\n"
    )
    (tmp_path / "GUIDE.md").write_text("# Normalisation\nUse `a.normalise()` for user input.\n")
    before = build_repository_index(tmp_path)
    before_state = f"state:{before.source_state.fact_digest}"
    candidate = build_similarity_candidate(
        source_state_id=before_state,
        granularity=CandidateGranularity.DEFINITION,
        method=SimilarityMethod.NORMALIZED,
        method_version="python-ast/1",
        configuration_digest=_digest("normalization-policy"),
        provider_run_id=None,
        threshold=None,
        token_count=20,
        members=[
            CandidateRegion(
                source_state_id=before_state,
                path=path,
                start_line=1,
                end_line=2,
                entity_id=symbol.symbol_id,
                role="implementation",
                language="python",
                content_digest=_digest(content),
            )
            for path, symbol, content in (
                ("a.py", next(item for item in before.symbols if item.path == "a.py"), source),
                (
                    "b.py",
                    next(item for item in before.symbols if item.path == "b.py"),
                    source.replace("normalise", "canonicalise"),
                ),
            )
        ],
        aligned_hunks=[],
        unmatched_member_regions=[],
        strength=EvidenceStrength.CONSERVATIVE,
        limitations=["Callers, tests, and documentation require review."],
        rationale="Normalized candidate; no deletion verdict.",
    )
    initial = build_consolidation_dossier(
        repository_id="repository:bounded",
        base_dossier_id=f"dossier:{before.source_state.fact_digest}",
        base_dossier_digest=_digest(before.model_dump_json()),
        candidate=candidate,
        groups=[
            ReviewEvidenceGroup(
                question=question,
                evidence_refs=[f"before:{question.value}"],
                summary=f"Bounded {question.value} review.",
            )
            for question in ConsolidationQuestion
        ],
    )
    obligations = [
        build_implementation_obligation(
            kind=kind,
            subject_ref=f"subject:{kind.value}",
            expectation=f"Preserve and verify {kind.value}.",
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
    intent = build_implementation_intent(
        repository_id="repository:bounded",
        before_source_state_id=before_state,
        before_dossier_id=initial.dossier_id,
        before_evidence_digest=_digest(before.model_dump_json()),
        before_provider_digests={"baseline": _digest(before.source_state.provider_digest)},
        decision_overlay_id=None,
        obligations=obligations,
        declared_unknowns=[],
    )

    (tmp_path / "b.py").write_text("from a import normalise\n\ncanonicalise = normalise\n")
    (tmp_path / "GUIDE.md").write_text(
        "# Normalisation\nUse `a.normalise()`; `b.canonicalise` is a documented alias.\n"
    )
    after = build_repository_index(tmp_path)
    after_state = f"state:{after.source_state.fact_digest}"
    change = build_change_evidence(
        dimension=ChangeDimension.CANDIDATE,
        kinds=[ChangeBoundaryKind.MERGED, ChangeBoundaryKind.REEXPORTED],
        predecessor_refs=[candidate.candidate_id],
        successor_refs=["module:b:reexport:canonicalise"],
        evidence_refs=["after:index", "after:test", "after:guide"],
        lineage="exact",
        rationale="The duplicate body became an explicit alias to the selected owner.",
    )
    changed = build_change_dossier(
        repository_id="repository:bounded",
        before_source_state_id=before_state,
        after_source_state_id=after_state,
        before_dossier_id=initial.dossier_id,
        after_dossier_id=f"dossier:{after.source_state.fact_digest}",
        before_evidence_digest=_digest(before.model_dump_json()),
        after_evidence_digest=_digest(after.model_dump_json()),
        before_provider_digests={"baseline": _digest(before.source_state.provider_digest)},
        after_provider_digests={"baseline": _digest(after.model_dump_json())},
        changes=[change],
        affected_evidence_refs=["test_core.py", "GUIDE.md"],
        blast_radius_refs=["b.canonicalise"],
        limitations=["Runtime execution is represented by an independently supplied observation."],
    )
    observations = [
        ClosureObservation(
            obligation_id=item.obligation_id,
            source_state_id=after_state,
            status=ObligationStatus.SATISFIED,
            evidence_refs=[f"after:{item.kind.value}"],
            observed=f"Verified {item.kind.value} against the after index and selected evidence.",
        )
        for item in intent.obligations
    ]
    closure = verify_implementation_closure(
        intent,
        after_source_state_id=after_state,
        after_source_state_digest=_digest(after.model_dump_json()),
        after_provider_digests={"baseline": _digest(after.model_dump_json())},
        provider_completeness={"baseline": "complete"},
        observations=observations,
        evidence_kinds_by_ref={
            f"after:{item.kind.value}": {item.kind.value} for item in intent.obligations
        },
    )

    assert before.source_state.fact_digest != after.source_state.fact_digest
    assert changed.changes[0].kinds == [ChangeBoundaryKind.MERGED, ChangeBoundaryKind.REEXPORTED]
    assert closure.outcome is ClosureOutcome.CLOSED
