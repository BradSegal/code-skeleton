"""Shared canonical accumulation primitives for artifact normalization adapters."""

from __future__ import annotations

from dataclasses import dataclass, field

from anatomize._artifacts import content_id
from anatomize.evidence import (
    AliasRecord,
    CandidateRecord,
    CompletenessRecord,
    ConflictRecord,
    ContractRecord,
    EdgeRecord,
    EntityRecord,
    LimitationRecord,
    LineageRecord,
    LocationRecord,
    ObservationRecord,
    OmissionRecord,
    RepositoryEntity,
    RepositoryEvidence,
    SourceStateRecord,
)
from anatomize.identity import RepositoryIdentityKey, canonical_identity_id
from anatomize.providers.models import ProviderEvidenceBatch


@dataclass
class ProviderBatchBuilder:
    """Deduplicate records and close baseline entity/location references."""

    baseline: RepositoryEvidence
    locations: dict[str, LocationRecord] = field(default_factory=dict)
    entities: dict[str, EntityRecord] = field(default_factory=dict)
    edges: dict[str, EdgeRecord] = field(default_factory=dict)
    contracts: dict[str, ContractRecord] = field(default_factory=dict)
    candidates: dict[str, CandidateRecord] = field(default_factory=dict)
    observations: dict[str, ObservationRecord] = field(default_factory=dict)
    limitations: dict[str, LimitationRecord] = field(default_factory=dict)
    omissions: dict[str, OmissionRecord] = field(default_factory=dict)
    conflicts: dict[str, ConflictRecord] = field(default_factory=dict)
    aliases: dict[str, AliasRecord] = field(default_factory=dict)
    lineage: dict[str, LineageRecord] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)

    def include_baseline_entity(self, entity: EntityRecord) -> None:
        """Include an entity and every baseline location/file needed to validate it."""
        if entity.entity_id in self.entities:
            return
        self.entities[entity.entity_id] = entity.model_copy(update={"provider_run_ids": []})
        for location_id in entity.location_ids:
            self._include_baseline_location(location_id)

    def _include_baseline_location(self, location_id: str) -> None:
        """Close file, generated-source, and embedded-host location references."""
        if location_id in self.locations:
            return
        locations = {item.location_id: item for item in self.baseline.locations}
        entities = {item.entity_id: item for item in self.baseline.entities}
        location = locations[location_id]
        self.locations[location_id] = location
        coordinate_space = location.coordinate_space
        dependency_ids = list(getattr(coordinate_space, "source_location_ids", []))
        host_location_id = getattr(coordinate_space, "host_location_id", None)
        if host_location_id is not None:
            dependency_ids.append(host_location_id)
        for dependency_id in dependency_ids:
            self._include_baseline_location(dependency_id)
        if location.file_id is not None and location.file_id not in self.entities:
            self.include_baseline_entity(entities[location.file_id])

    def repository_entity(self, state: SourceStateRecord) -> RepositoryEntity:
        """Reuse or create the one canonical repository entity for a state."""
        match = next(
            (
                item
                for item in self.baseline.entities
                if isinstance(item, RepositoryEntity) and item.source_state_id == state.state_id
            ),
            None,
        )
        if match is not None:
            self.include_baseline_entity(match)
            included = self.entities[match.entity_id]
            if not isinstance(included, RepositoryEntity):
                raise TypeError("baseline repository identity changed type during normalization")
            return included
        entity = RepositoryEntity(
            entity_id=canonical_identity_id(
                RepositoryIdentityKey(
                    repository_id=state.repository_id,
                    source_state_id=state.state_id,
                )
            ),
            source_state_id=state.state_id,
            repository_id=state.repository_id,
            display_name=state.repository_id,
            root_name=state.repository_id,
        )
        self.entities[entity.entity_id] = entity
        return entity

    def add_degradation(
        self,
        *,
        run_id: str,
        state_id: str,
        code: str,
        summary: str,
        scope_type: str,
        scope_id: str,
        remediation: str | None,
        recoverable: bool = True,
        discriminator: str | None = None,
    ) -> None:
        """Record one stable limitation and matching recoverability-aware omission."""
        key = discriminator or content_id(
            "provider-degradation",
            {"code": code, "scope": scope_id, "summary": summary},
        )
        limitation = LimitationRecord(
            limitation_id=content_id("limitation:provider", {"run": run_id, "key": key}),
            provider_run_id=run_id,
            code=code,
            summary=summary,
        )
        omission = OmissionRecord(
            omission_id=content_id("omission:provider", {"run": run_id, "key": key}),
            source_state_id=state_id,
            provider_run_id=run_id,
            reason=summary,
            scope_type=scope_type,
            scope_id=scope_id,
            recoverable=recoverable,
            remediation=remediation,
        )
        self.limitations[limitation.limitation_id] = limitation
        self.omissions[omission.omission_id] = omission

    def build_payload(self, completeness: list[CompletenessRecord]) -> ProviderEvidenceBatch:
        """Build one deterministic provider payload from all accumulated families."""
        return ProviderEvidenceBatch(
            locations=sorted(self.locations.values(), key=lambda item: item.location_id),
            entities=sorted(self.entities.values(), key=lambda item: item.entity_id),
            edges=sorted(self.edges.values(), key=lambda item: item.edge_id),
            contracts=sorted(self.contracts.values(), key=lambda item: item.contract_id),
            candidates=sorted(self.candidates.values(), key=lambda item: item.candidate_id),
            observations=sorted(self.observations.values(), key=lambda item: item.observation_id),
            completeness=completeness,
            limitations=sorted(self.limitations.values(), key=lambda item: item.limitation_id),
            omissions=sorted(self.omissions.values(), key=lambda item: item.omission_id),
            conflicts=sorted(self.conflicts.values(), key=lambda item: item.conflict_id),
            aliases=sorted(self.aliases.values(), key=lambda item: item.alias_id),
            lineage=sorted(self.lineage.values(), key=lambda item: item.lineage_id),
        )
