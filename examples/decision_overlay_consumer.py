"""Independent consumer example: decide, reapply, invalidate, and review."""

from __future__ import annotations

import json

from anatomize._artifacts import sha256_digest
from anatomize.evidence import EvidenceStrength
from anatomize.lifecycle import (
    CandidateGranularity,
    CandidateRegion,
    DecisionDisposition,
    SimilarityMethod,
    build_decision_overlay,
    build_similarity_candidate,
    evaluate_decision_overlay,
    visible_candidates,
)


def digest(value: str) -> str:
    return sha256_digest(value.encode())


def candidate(content: str):  # type: ignore[no-untyped-def]
    return build_similarity_candidate(
        source_state_id="state:review",
        granularity=CandidateGranularity.DEFINITION,
        method=SimilarityMethod.NORMALIZED,
        method_version="1",
        configuration_digest=digest("normalization-policy"),
        provider_run_id=None,
        threshold=None,
        token_count=80,
        members=[
            CandidateRegion(
                source_state_id="state:review",
                path=path,
                start_line=1,
                end_line=8,
                entity_id=entity,
                role="implementation",
                language="python",
                content_digest=digest(content),
            )
            for path, entity in [("src/a.py", "function:a"), ("src/b.py", "function:b")]
        ],
        aligned_hunks=[],
        unmatched_member_regions=[],
        strength=EvidenceStrength.CONSERVATIVE,
        limitations=["Intent and runtime evidence require separate review."],
        rationale="Candidate evidence only; no consolidation verdict.",
    )


def main() -> None:
    original = candidate("same implementation")
    overlay = build_decision_overlay(
        candidate=original,
        relevant_source_digest=digest("candidate sources"),
        provider_digests={},
        evidence_digests={"tests": digest("different intent")},
        decision_schema_version="example/1",
        decision_policy_digest=digest("policy"),
        owner_namespace="example.consumer",
        disposition=DecisionDisposition.ACCEPTED_DUPLICATE,
        rationale="The implementations are similar but tests protect different behavior.",
    )
    common = {
        "relevant_source_digest": digest("candidate sources"),
        "provider_digests": {},
        "evidence_digests": {"tests": digest("different intent")},
        "decision_schema_version": "example/1",
        "decision_policy_digest": digest("policy"),
    }
    current = evaluate_decision_overlay(overlay, candidate=original, **common)
    changed = candidate("diverged implementation")
    stale = evaluate_decision_overlay(overlay, candidate=changed, **common)
    print(
        json.dumps(
            {
                "decision_id": overlay.decision_id,
                "current_hidden": not visible_candidates([original], [overlay], [current]),
                "stale_reopened": visible_candidates([changed], [overlay], [stale]) == [changed],
                "stale_reasons": [item.value for item in stale.stale_reasons],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
