"""Lossless reconciliation of provider alias and reference claims."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from enum import Enum

from pydantic import Field, model_validator

from anatomize.evidence import (
    AliasRecord,
    EvidenceModel,
    IdentityCandidate,
    IdentityReason,
    IdentityResolutionStatus,
    validate_portable_identity,
)


class ClaimCertainty(str, Enum):
    """What one provider claims about its candidate set."""

    EXACT = "exact"
    CANDIDATE = "candidate"
    UNRESOLVED = "unresolved"


class IdentityMappingClaim(EvidenceModel):
    """One provider's complete claim for one alias in one state."""

    provider_run_id: str = Field(min_length=1)
    certainty: ClaimCertainty
    candidate_entity_ids: list[str] = Field(default_factory=list)
    reason_codes: list[IdentityReason] = Field(min_length=1)
    location_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim(self) -> IdentityMappingClaim:
        if len(self.candidate_entity_ids) != len(set(self.candidate_entity_ids)):
            raise ValueError("identity claim candidates must be unique")
        if self.certainty is ClaimCertainty.EXACT and len(self.candidate_entity_ids) != 1:
            raise ValueError("exact identity claims require one candidate")
        if self.certainty is ClaimCertainty.CANDIDATE and not self.candidate_entity_ids:
            raise ValueError("candidate identity claims require at least one candidate")
        if self.certainty is ClaimCertainty.UNRESOLVED and self.candidate_entity_ids:
            raise ValueError("unresolved identity claims cannot declare candidates")
        return self


def reconcile_identity_claims(
    *,
    repository_id: str,
    source_state_id: str,
    scheme: str,
    value: str,
    claims: Sequence[IdentityMappingClaim],
    known_entity_ids: AbstractSet[str],
    rationale: str,
) -> AliasRecord:
    """Reconcile without choosing among ambiguity or conflicting exact claims."""
    validate_portable_identity(repository_id, label="repository identity")
    if not claims:
        raise ValueError("identity reconciliation requires at least one provider claim")
    provider_ids = [claim.provider_run_id for claim in claims]
    if len(provider_ids) != len(set(provider_ids)):
        raise ValueError("identity reconciliation accepts one claim per provider run")
    claimed_ids = {
        entity_id for claim in claims for entity_id in claim.candidate_entity_ids
    }
    unknown = sorted(claimed_ids.difference(known_entity_ids))
    if unknown:
        raise ValueError(f"identity claims reference unknown candidate entities: {unknown}")

    exact_ids = {
        claim.candidate_entity_ids[0]
        for claim in claims
        if claim.certainty is ClaimCertainty.EXACT
    }
    if len(exact_ids) > 1:
        resolution = IdentityResolutionStatus.CONFLICTING
    elif not claimed_ids:
        resolution = IdentityResolutionStatus.UNRESOLVED
    elif len(exact_ids) == 1 and claimed_ids == exact_ids:
        resolution = IdentityResolutionStatus.EXACT
    else:
        resolution = IdentityResolutionStatus.AMBIGUOUS

    provider_runs: defaultdict[str, set[str]] = defaultdict(set)
    reasons: defaultdict[str, set[IdentityReason]] = defaultdict(set)
    exact_candidates: set[str] = set()
    locations: set[str] = set()
    for claim in claims:
        locations.update(claim.location_ids)
        for entity_id in claim.candidate_entity_ids:
            provider_runs[entity_id].add(claim.provider_run_id)
            reasons[entity_id].update(claim.reason_codes)
            if claim.certainty is ClaimCertainty.EXACT:
                exact_candidates.add(entity_id)

    candidates = [
        IdentityCandidate(
            entity_id=entity_id,
            provider_run_ids=sorted(provider_runs[entity_id]),
            reason_codes=sorted(reasons[entity_id], key=lambda item: item.value),
            exact=entity_id in exact_candidates,
        )
        for entity_id in sorted(claimed_ids)
    ]
    alias_payload = json.dumps(
        {
            "repository_id": repository_id,
            "source_state_id": source_state_id,
            "scheme": scheme,
            "value": value,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return AliasRecord(
        alias_id=f"alias:sha256:{hashlib.sha256(alias_payload).hexdigest()}",
        source_state_id=source_state_id,
        scheme=scheme,
        value=value,
        resolution=resolution,
        provider_run_ids=sorted(provider_ids),
        reason_codes=sorted(
            {reason for claim in claims for reason in claim.reason_codes},
            key=lambda item: item.value,
        ),
        candidates=candidates,
        location_ids=sorted(locations),
        rationale=rationale,
    )
