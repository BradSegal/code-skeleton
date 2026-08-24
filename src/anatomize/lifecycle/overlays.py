"""Consolidation evidence and reviewer-owned decision records."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from anatomize._artifacts import canonical_ordered_json_bytes, content_id, sha256_digest
from anatomize.evidence import EvidenceModel
from anatomize.lifecycle.similarity import SimilarityCandidate

CONSOLIDATION_DOSSIER_TYPE: Literal["anatomize.consolidation-dossier"] = (
    "anatomize.consolidation-dossier"
)
CONSOLIDATION_DOSSIER_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
DECISION_OVERLAY_TYPE: Literal["anatomize.decision-overlay"] = "anatomize.decision-overlay"
DECISION_OVERLAY_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
OVERLAY_EVALUATION_TYPE: Literal["anatomize.overlay-evaluation"] = "anatomize.overlay-evaluation"
OVERLAY_EVALUATION_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class ConsolidationQuestion(str, Enum):
    MEMBERS_AND_DIFFERENCES = "members_and_differences"
    PUBLIC_CONTRACTS = "public_contracts"
    CALLERS_AND_CONSUMERS = "callers_and_consumers"
    TEST_INTENT = "test_intent"
    RUNTIME_CONTEXT = "runtime_context"
    DOCUMENTATION = "documentation"
    WORKFLOWS = "workflows"
    HISTORY = "history"
    PROVENANCE = "provenance"
    CONFLICTS = "conflicts"
    OMISSIONS = "omissions"
    PRIOR_DECISIONS = "prior_decisions"


class ReviewEvidenceGroup(EvidenceModel):
    question: ConsolidationQuestion
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique(self) -> ReviewEvidenceGroup:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("review evidence references must be unique")
        return self


class ConsolidationDossier(EvidenceModel):
    artifact_type: Literal["anatomize.consolidation-dossier"] = CONSOLIDATION_DOSSIER_TYPE
    schema_version: Literal["1.0.0"] = CONSOLIDATION_DOSSIER_SCHEMA_VERSION
    dossier_id: str
    repository_id: str
    source_state_id: str
    base_dossier_id: str
    base_dossier_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate: SimilarityCandidate
    groups: list[ReviewEvidenceGroup]
    disposition_authority: Literal["consumer_overlay_only"] = "consumer_overlay_only"

    @model_validator(mode="after")
    def validate_dossier(self) -> ConsolidationDossier:
        questions = [item.question for item in self.groups]
        if len(questions) != len(set(questions)):
            raise ValueError("consolidation dossier review questions must be unique")
        missing = sorted(set(ConsolidationQuestion).difference(questions), key=lambda item: item.value)
        if missing:
            raise ValueError(
                "consolidation dossier requires every review question; missing "
                + ", ".join(item.value for item in missing)
            )
        if self.candidate.source_state_id != self.source_state_id:
            raise ValueError("consolidation candidate source state does not match dossier")
        if self.dossier_id != _consolidation_dossier_id(self):
            raise ValueError("consolidation dossier identifier does not match its evidence")
        return self


class DecisionDisposition(str, Enum):
    UNREVIEWED = "unreviewed"
    INVESTIGATE = "investigate"
    KEEP = "keep"
    CONSOLIDATE = "consolidate"
    GENERALISE = "generalise"
    SEPARATE = "separate"
    ACCEPTED_DUPLICATE = "accepted_duplicate"


class DecisionOverlay(EvidenceModel):
    artifact_type: Literal["anatomize.decision-overlay"] = DECISION_OVERLAY_TYPE
    schema_version: Literal["1.0.0"] = DECISION_OVERLAY_SCHEMA_VERSION
    decision_id: str
    candidate_id: str
    stable_candidate_key: str
    source_state_id: str
    relevant_source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    member_evidence_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_digests: dict[str, str]
    evidence_digests: dict[str, str]
    decision_schema_version: str
    decision_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    owner_namespace: str
    disposition: DecisionDisposition
    rationale: str
    preserved_divergence: list[str] = Field(default_factory=list)
    review_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_overlay(self) -> DecisionOverlay:
        for collection in (self.provider_digests, self.evidence_digests):
            if any(not _is_digest(value) for value in collection.values()):
                raise ValueError("overlay evidence values must be SHA-256 digests")
        if self.decision_id != _decision_id(self):
            raise ValueError("decision overlay identifier does not match its content")
        return self


class OverlayStaleReason(str, Enum):
    SOURCE_STATE = "source_state_changed"
    CANDIDATE = "candidate_or_member_evidence_changed"
    PROVIDER = "provider_set_or_artifact_changed"
    EVIDENCE = "referenced_evidence_changed"
    DECISION_SCHEMA = "decision_schema_changed"
    DECISION_POLICY = "decision_policy_changed"


class OverlayEvaluation(EvidenceModel):
    artifact_type: Literal["anatomize.overlay-evaluation"] = OVERLAY_EVALUATION_TYPE
    schema_version: Literal["1.0.0"] = OVERLAY_EVALUATION_SCHEMA_VERSION
    evaluation_id: str
    decision_id: str
    current: bool
    stale_reasons: list[OverlayStaleReason]
    explanation: str

    @model_validator(mode="after")
    def validate_evaluation(self) -> OverlayEvaluation:
        expected = content_id(
            "overlay-evaluation",
            self.model_dump(mode="json", exclude={"evaluation_id", "artifact_type", "schema_version"}),
        )
        if self.evaluation_id != expected:
            raise ValueError("overlay evaluation identifier does not match its content")
        return self


def build_consolidation_dossier(
    *,
    repository_id: str,
    base_dossier_id: str,
    base_dossier_digest: str,
    candidate: SimilarityCandidate,
    groups: list[ReviewEvidenceGroup],
) -> ConsolidationDossier:
    """Bind candidate evidence into a review-question surface."""
    provisional = ConsolidationDossier.model_construct(
        dossier_id="pending",
        repository_id=repository_id,
        source_state_id=candidate.source_state_id,
        base_dossier_id=base_dossier_id,
        base_dossier_digest=base_dossier_digest,
        candidate=candidate,
        groups=groups,
    )
    return ConsolidationDossier(
        dossier_id=_consolidation_dossier_id(provisional),
        repository_id=repository_id,
        source_state_id=candidate.source_state_id,
        base_dossier_id=base_dossier_id,
        base_dossier_digest=base_dossier_digest,
        candidate=candidate,
        groups=groups,
    )


def build_decision_overlay(
    *,
    candidate: SimilarityCandidate,
    relevant_source_digest: str,
    provider_digests: dict[str, str],
    evidence_digests: dict[str, str],
    decision_schema_version: str,
    decision_policy_digest: str,
    owner_namespace: str,
    disposition: DecisionDisposition,
    rationale: str,
    preserved_divergence: list[str] | None = None,
    review_conditions: list[str] | None = None,
) -> DecisionOverlay:
    """Create a portable judgement separate from canonical repository evidence."""
    stable_key = stable_candidate_key(candidate)
    member_digest = candidate_evidence_digest(candidate)
    sorted_providers = dict(sorted(provider_digests.items()))
    sorted_evidence = dict(sorted(evidence_digests.items()))
    preserved = sorted(preserved_divergence or [])
    conditions = sorted(review_conditions or [])
    provisional = DecisionOverlay.model_construct(
        decision_id="pending",
        candidate_id=candidate.candidate_id,
        stable_candidate_key=stable_key,
        source_state_id=candidate.source_state_id,
        relevant_source_digest=relevant_source_digest,
        member_evidence_digest=member_digest,
        provider_digests=sorted_providers,
        evidence_digests=sorted_evidence,
        decision_schema_version=decision_schema_version,
        decision_policy_digest=decision_policy_digest,
        owner_namespace=owner_namespace,
        disposition=disposition,
        rationale=rationale,
        preserved_divergence=preserved,
        review_conditions=conditions,
    )
    return DecisionOverlay(
        decision_id=_decision_id(provisional),
        candidate_id=candidate.candidate_id,
        stable_candidate_key=stable_key,
        source_state_id=candidate.source_state_id,
        relevant_source_digest=relevant_source_digest,
        member_evidence_digest=member_digest,
        provider_digests=sorted_providers,
        evidence_digests=sorted_evidence,
        decision_schema_version=decision_schema_version,
        decision_policy_digest=decision_policy_digest,
        owner_namespace=owner_namespace,
        disposition=disposition,
        rationale=rationale,
        preserved_divergence=preserved,
        review_conditions=conditions,
    )


def evaluate_decision_overlay(
    overlay: DecisionOverlay,
    *,
    candidate: SimilarityCandidate,
    relevant_source_digest: str,
    provider_digests: dict[str, str],
    evidence_digests: dict[str, str],
    decision_schema_version: str,
    decision_policy_digest: str,
) -> OverlayEvaluation:
    """Explain relevant staleness; unrelated repository state is deliberately absent."""
    reasons: list[OverlayStaleReason] = []
    if overlay.relevant_source_digest != relevant_source_digest:
        reasons.append(OverlayStaleReason.SOURCE_STATE)
    if (
        overlay.stable_candidate_key != stable_candidate_key(candidate)
        or overlay.member_evidence_digest != candidate_evidence_digest(candidate)
    ):
        reasons.append(OverlayStaleReason.CANDIDATE)
    if overlay.provider_digests != dict(sorted(provider_digests.items())):
        reasons.append(OverlayStaleReason.PROVIDER)
    if overlay.evidence_digests != dict(sorted(evidence_digests.items())):
        reasons.append(OverlayStaleReason.EVIDENCE)
    if overlay.decision_schema_version != decision_schema_version:
        reasons.append(OverlayStaleReason.DECISION_SCHEMA)
    if overlay.decision_policy_digest != decision_policy_digest:
        reasons.append(OverlayStaleReason.DECISION_POLICY)
    values = {
        "decision_id": overlay.decision_id,
        "current": not reasons,
        "stale_reasons": reasons,
        "explanation": (
            "Decision inputs are current; unrelated repository changes do not invalidate this overlay."
            if not reasons
            else "Decision requires review because: " + ", ".join(item.value for item in reasons)
        ),
    }
    return OverlayEvaluation(
        evaluation_id=content_id("overlay-evaluation", values),
        **values,
    )


def visible_candidates(
    candidates: list[SimilarityCandidate],
    overlays: list[DecisionOverlay],
    evaluations: list[OverlayEvaluation],
    *,
    show_accepted_duplicates: bool = False,
) -> list[SimilarityCandidate]:
    """Hide only explicitly accepted, still-current candidates when requested."""
    if show_accepted_duplicates:
        return candidates
    current = {item.decision_id for item in evaluations if item.current}
    hidden = {
        item.candidate_id
        for item in overlays
        if item.decision_id in current and item.disposition is DecisionDisposition.ACCEPTED_DUPLICATE
    }
    return [item for item in candidates if item.candidate_id not in hidden]


def candidate_evidence_digest(candidate: SimilarityCandidate) -> str:
    values = {
        "members": sorted(
            (
                {
                    "key": item.entity_id or item.path,
                    "path": item.path,
                    "content_digest": item.content_digest,
                    "role": item.role,
                }
                for item in candidate.members
            ),
            key=lambda item: str(item["key"]),
        ),
        "hunks": [
            item.model_dump(
                mode="json",
                exclude={"left_start_line", "left_end_line", "right_start_line", "right_end_line"},
            )
            for item in candidate.aligned_hunks
        ],
        "unmatched": [
            item.model_dump(mode="json", exclude={"source_state_id"})
            for item in candidate.unmatched_member_regions
        ],
        "strength": candidate.strength.value,
        "limitations": candidate.limitations,
    }
    return sha256_digest(canonical_ordered_json_bytes(values))


def stable_candidate_key(candidate: SimilarityCandidate) -> str:
    """Identify reconciled members and acquisition semantics across source states."""
    return content_id(
        "stable-similarity-candidate",
        {
            "granularity": candidate.granularity.value,
            "method": candidate.method.value,
            "method_version": candidate.method_version,
            "configuration_digest": candidate.configuration_digest,
            "members": sorted(item.entity_id or item.path for item in candidate.members),
        },
    )


def _consolidation_dossier_id(dossier: ConsolidationDossier) -> str:
    return content_id(
        "consolidation-dossier",
        {
            "repository_id": dossier.repository_id,
            "source_state_id": dossier.source_state_id,
            "base_dossier_id": dossier.base_dossier_id,
            "base_dossier_digest": dossier.base_dossier_digest,
            "candidate_id": dossier.candidate.candidate_id,
            "groups": [item.model_dump(mode="json") for item in dossier.groups],
        },
    )


def _decision_id(overlay: DecisionOverlay) -> str:
    return content_id(
        "consumer-decision",
        overlay.model_dump(mode="json", exclude={"decision_id", "artifact_type", "schema_version"}),
    )


def _is_digest(value: str) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))
