"""Conflict-safe composition of independently validated evidence artifacts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Literal, TypeVar

from anatomize._artifacts import canonicalize_json, compact_json, content_id
from anatomize.evidence.models import (
    ConflictRecord,
    EvidenceModel,
    EvidenceProducer,
    ObservationRecord,
    ObservationStance,
    RepositoryEvidence,
    StructuralObservation,
)

_RecordT = TypeVar("_RecordT", bound=EvidenceModel)


def merge_repository_evidence(artifacts: Iterable[RepositoryEvidence]) -> RepositoryEvidence:
    """Merge same-repository provider artifacts without overwriting disagreements.

    Identical source-bound records are stored once. Reusing an identifier for a
    different record is an explicit composition error, not last-writer-wins.
    """
    values = list(artifacts)
    if not values:
        raise ValueError("evidence merge requires at least one artifact")
    repository_id = values[0].repository_id
    if any(item.repository_id != repository_id for item in values):
        raise ValueError("evidence merge cannot cross repository identities")
    observations = _deduplicate(
        (record for artifact in values for record in artifact.observations),
        lambda item: item.observation_id,
    )
    declared_conflicts = _deduplicate(
        (record for artifact in values for record in artifact.conflicts),
        lambda item: item.conflict_id,
    )
    return RepositoryEvidence(
        producer=EvidenceProducer(version="evidence-merge:1.0.0"),
        repository_id=repository_id,
        states=_deduplicate(
            (record for artifact in values for record in artifact.states),
            lambda item: item.state_id,
        ),
        provider_artifacts=_deduplicate(
            (record for artifact in values for record in artifact.provider_artifacts),
            lambda item: item.artifact_id,
        ),
        provider_runs=_deduplicate(
            (record for artifact in values for record in artifact.provider_runs),
            lambda item: item.provider_run_id,
        ),
        locations=_deduplicate(
            (record for artifact in values for record in artifact.locations),
            lambda item: item.location_id,
        ),
        entities=_deduplicate_direct_provenance(
            (record for artifact in values for record in artifact.entities),
            lambda item: item.entity_id,
        ),
        edges=_deduplicate_direct_provenance(
            (record for artifact in values for record in artifact.edges),
            lambda item: item.edge_id,
        ),
        contracts=_deduplicate(
            (record for artifact in values for record in artifact.contracts),
            lambda item: item.contract_id,
        ),
        candidates=_deduplicate_direct_provenance(
            (record for artifact in values for record in artifact.candidates),
            lambda item: item.candidate_id,
        ),
        observations=observations,
        completeness=_deduplicate(
            (record for artifact in values for record in artifact.completeness),
            lambda item: item.completeness_id,
        ),
        limitations=_deduplicate(
            (record for artifact in values for record in artifact.limitations),
            lambda item: item.limitation_id,
        ),
        omissions=_deduplicate(
            (record for artifact in values for record in artifact.omissions),
            lambda item: item.omission_id,
        ),
        conflicts=_deduplicate(
            [
                *declared_conflicts,
                *_structural_conflicts(
                    observations,
                    excluded_targets={
                        (item.source_state_id, item.target_type, item.target_id)
                        for item in declared_conflicts
                    },
                ),
            ],
            lambda item: item.conflict_id,
        ),
        aliases=_deduplicate(
            (record for artifact in values for record in artifact.aliases),
            lambda item: item.alias_id,
        ),
        lineage=_deduplicate(
            (record for artifact in values for record in artifact.lineage),
            lambda item: item.lineage_id,
        ),
    )


def _deduplicate(records: Iterable[_RecordT], identity: Callable[[_RecordT], str]) -> list[_RecordT]:
    merged: dict[str, _RecordT] = {}
    for record in records:
        record_id = identity(record)
        existing = merged.get(record_id)
        if existing is not None and not _canonically_equal(existing, record):
            raise ValueError(f"evidence merge found conflicting record identity: {record_id}")
        merged[record_id] = record
    return [merged[record_id] for record_id in sorted(merged)]


def _deduplicate_direct_provenance(
    records: Iterable[_RecordT],
    identity: Callable[[_RecordT], str],
) -> list[_RecordT]:
    """Merge identical facts while unioning their compact direct provenance."""
    merged: dict[str, _RecordT] = {}
    for record in records:
        record_id = identity(record)
        existing = merged.get(record_id)
        if existing is None:
            merged[record_id] = record
            continue
        existing_runs = list(getattr(existing, "provider_run_ids", []))
        record_runs = list(getattr(record, "provider_run_ids", []))
        if not _canonically_equal(
            existing.model_copy(update={"provider_run_ids": []}),
            record.model_copy(update={"provider_run_ids": []}),
        ):
            raise ValueError(f"evidence merge found conflicting record identity: {record_id}")
        merged[record_id] = existing.model_copy(
            update={"provider_run_ids": sorted(set(existing_runs) | set(record_runs))}
        )
    return [merged[record_id] for record_id in sorted(merged)]


def _canonically_equal(left: EvidenceModel, right: EvidenceModel) -> bool:
    return compact_json(canonicalize_json(left.model_dump(mode="json"))) == compact_json(
        canonicalize_json(right.model_dump(mode="json"))
    )


def _structural_conflicts(
    observations: list[ObservationRecord],
    *,
    excluded_targets: set[tuple[str, str, str]],
) -> list[ConflictRecord]:
    """Materialize only explicit cross-provider contradictions over one exact target."""
    by_target: dict[
        tuple[str, Literal["entity", "edge", "contract"], str],
        list[StructuralObservation],
    ] = {}
    for observation in observations:
        if isinstance(observation, StructuralObservation):
            key = (observation.source_state_id, observation.target_type, observation.target_id)
            by_target.setdefault(key, []).append(observation)
    conflicts = []
    for (state_id, target_type, target_id), values in sorted(by_target.items()):
        if (state_id, target_type, target_id) in excluded_targets:
            continue
        conflicting = [item for item in values if item.stance is ObservationStance.CONFLICTS]
        supporting = [item for item in values if item.stance is not ObservationStance.CONFLICTS]
        observation_ids = sorted(
            {
                item.observation_id
                for item in [*conflicting, *supporting]
                if any(other.provider_run_id != item.provider_run_id for other in values)
            }
        )
        if not conflicting or not supporting or len(observation_ids) < 2:
            continue
        payload = {
            "state": state_id,
            "target_type": target_type,
            "target_id": target_id,
            "observations": observation_ids,
        }
        conflicts.append(
            ConflictRecord(
                conflict_id=content_id("conflict:structural-observations", payload),
                source_state_id=state_id,
                target_type=target_type,
                target_id=target_id,
                observation_ids=observation_ids,
                summary="Independent providers explicitly support and conflict with the same exact target.",
            )
        )
    return conflicts
