"""Minimal exact comparison over canonical evidence records.

The matcher intentionally avoids speculative rename, split, merge, or semantic
equivalence inference. It matches stable domain keys, detects unique file moves
by exact content digest, and leaves every other continuity question explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anatomize._artifacts import canonical_json_bytes, canonicalize_json, content_id, sha256_digest
from anatomize.evidence import (
    ArtifactEntity,
    CandidateKind,
    ConfigurationEntity,
    ContractRecord,
    DataEntity,
    DependencyEntity,
    DiagnosticEntity,
    DiagnosticObservation,
    DocumentationEntity,
    EntityRecord,
    EvidenceStrength,
    ExternalEntity,
    FileEntity,
    LineageCertainty,
    LineageKind,
    LineageReason,
    LineageRecord,
    ObservationRecord,
    RangeEntity,
    RepositoryEntity,
    RepositoryEvidence,
    RuntimeObservation,
    SimilarityEntity,
    SymbolEntity,
    TestEntity,
    WorkflowEntity,
)
from anatomize.temporal.models import (
    DeltaAvailability,
    DeltaFamily,
    DeltaReason,
    DeltaStatus,
    EvidenceRecordType,
    EvidenceTargetReference,
    HistoryStatus,
    RepositoryComparison,
    SemanticDelta,
    build_repository_comparison,
    build_semantic_delta,
    build_state_manifest,
    validate_comparison_evidence,
)


@dataclass(frozen=True)
class _Comparable:
    family: DeltaFamily
    record_type: EvidenceRecordType
    stable_key: str
    record_id: str
    payload_digest: str
    provider_run_ids: tuple[str, ...]
    value: object


def compare_repository_evidence(
    before: RepositoryEvidence,
    after: RepositoryEvidence,
    *,
    configuration_digest: str,
    history_status: HistoryStatus = HistoryStatus.UNAVAILABLE,
    include_unchanged: bool = False,
) -> RepositoryComparison:
    """Compare two exact evidence states with conservative, validated lineage."""
    if before.repository_id != after.repository_id:
        raise ValueError("comparison evidence belongs to different repositories")
    if len(before.states) != 1 or len(after.states) != 1:
        raise ValueError("exact comparison requires one source state on each side")
    before_manifest = build_state_manifest(
        before,
        source_state_id=before.states[0].state_id,
        configuration_digest=configuration_digest,
        history_status=history_status,
        baseline_available=True,
    )
    after_manifest = build_state_manifest(
        after,
        source_state_id=after.states[0].state_id,
        configuration_digest=configuration_digest,
        history_status=history_status,
        baseline_available=True,
    )
    before_items = _inventory(before)
    after_items = _inventory(after)
    lineage: list[LineageRecord] = []
    deltas = []

    common = sorted(before_items.keys() & after_items.keys())
    for key in common:
        left, right = before_items[key], after_items[key]
        unchanged = left.payload_digest == right.payload_digest
        current_lineage: LineageRecord | None = None
        if left.record_type is EvidenceRecordType.ENTITY:
            current_lineage = _lineage(
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                before_ids=[left.record_id],
                after_ids=[right.record_id],
                kind=LineageKind.UNCHANGED if unchanged else LineageKind.MODIFIED,
                reasons=[LineageReason.CONTENT_IDENTITY if unchanged else LineageReason.CONTENT_CHANGED],
                rationale=(
                    "Stable domain identity and normalized content are unchanged."
                    if unchanged
                    else "Stable domain identity is retained while normalized content changed."
                ),
            )
            if not unchanged or include_unchanged:
                lineage.append(current_lineage)
        if unchanged and not include_unchanged:
            continue
        deltas.append(
            _delta(
                left,
                right,
                status=DeltaStatus.UNCHANGED if unchanged else DeltaStatus.CHANGED,
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                lineage_ids=[current_lineage.lineage_id] if current_lineage is not None else [],
                rationale=(
                    "Stable domain identity and canonical record content are unchanged."
                    if unchanged
                    else "Stable domain identity matches and canonical record content differs."
                ),
            )
        )

    removed = {key: before_items[key] for key in before_items.keys() - after_items.keys()}
    added = {key: after_items[key] for key in after_items.keys() - before_items.keys()}
    moves = _exact_file_moves(removed, added)
    for left_key, right_key in moves:
        left, right = removed.pop(left_key), added.pop(right_key)
        current_lineage = _lineage(
            before_state=before.states[0].state_id,
            after_state=after.states[0].state_id,
            before_ids=[left.record_id],
            after_ids=[right.record_id],
            kind=LineageKind.MOVED,
            reasons=[LineageReason.CONTENT_IDENTITY, LineageReason.PATH_CHANGED],
            rationale="A unique file content digest is unchanged at a different repository path.",
        )
        lineage.append(current_lineage)
        deltas.append(
            _delta(
                left,
                right,
                status=DeltaStatus.CHANGED,
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                lineage_ids=[current_lineage.lineage_id],
                rationale="Exact file content moved to a different repository path.",
            )
        )
    for item in sorted(removed.values(), key=lambda value: (value.family.value, value.stable_key)):
        current_lineage = None
        if item.record_type is EvidenceRecordType.ENTITY:
            current_lineage = _lineage(
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                before_ids=[item.record_id],
                after_ids=[],
                kind=LineageKind.DELETED,
                reasons=[LineageReason.REMOVED_FROM_SCOPE],
                rationale="The stable domain key is absent from the successor evidence boundary.",
            )
            lineage.append(current_lineage)
        deltas.append(
            _delta(
                item,
                None,
                status=DeltaStatus.REMOVED,
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                lineage_ids=[current_lineage.lineage_id] if current_lineage is not None else [],
                rationale="The record is absent from the successor evidence boundary.",
            )
        )
    for item in sorted(added.values(), key=lambda value: (value.family.value, value.stable_key)):
        current_lineage = None
        if item.record_type is EvidenceRecordType.ENTITY:
            current_lineage = _lineage(
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                before_ids=[],
                after_ids=[item.record_id],
                kind=LineageKind.ADDED,
                reasons=[LineageReason.ADDED_TO_SCOPE],
                rationale="The stable domain key is newly present in successor evidence.",
            )
            lineage.append(current_lineage)
        deltas.append(
            _delta(
                None,
                item,
                status=DeltaStatus.ADDED,
                before_state=before.states[0].state_id,
                after_state=after.states[0].state_id,
                lineage_ids=[current_lineage.lineage_id] if current_lineage is not None else [],
                rationale="The record is newly present in successor evidence.",
            )
        )

    comparison = build_repository_comparison(
        repository_id=before.repository_id,
        before=before_manifest,
        after=after_manifest,
        lineage=sorted(lineage, key=lambda item: item.lineage_id),
        deltas=sorted(deltas, key=lambda item: item.delta_id),
        limitations=[
            "Only stable domain keys and unique exact file-content moves establish continuity; "
            "rename, split, merge, and semantic equivalence require external history or compiler evidence."
        ],
    )
    validate_comparison_evidence(comparison, before_evidence=before, after_evidence=after)
    return comparison


def _inventory(evidence: RepositoryEvidence) -> dict[tuple[EvidenceRecordType, str], _Comparable]:
    locations = {item.location_id: item for item in evidence.locations}
    entity_keys = {item.entity_id: _entity_key(item, locations) for item in evidence.entities}
    artifact_runs: dict[str, list[str]] = {}
    for run in evidence.provider_runs:
        for artifact_id in run.artifact_ids:
            artifact_runs.setdefault(artifact_id, []).append(run.provider_run_id)
    items: list[_Comparable] = []
    for entity in evidence.entities:
        items.append(
            _item(
                family=_entity_family(entity),
                record_type=EvidenceRecordType.ENTITY,
                stable_key=entity_keys[entity.entity_id],
                record_id=entity.entity_id,
                value=entity,
                provider_run_ids=entity.provider_run_ids,
                payload=_entity_payload(entity, locations),
            )
        )
    for edge in evidence.edges:
        key = _stable_json(
            {
                "category": edge.category.value,
                "predicate": edge.predicate,
                "source": entity_keys[edge.source_entity_id],
                "target": entity_keys[edge.target_entity_id],
            }
        )
        items.append(
            _item(
                family=DeltaFamily.RELATIONSHIP,
                record_type=EvidenceRecordType.EDGE,
                stable_key=key,
                record_id=edge.edge_id,
                value=edge,
                provider_run_ids=edge.provider_run_ids,
                payload={"key": key},
            )
        )
    for contract in evidence.contracts:
        subjects = sorted(entity_keys[item] for item in contract.subject_entity_ids)
        key = _stable_json({"kind": contract.kind.value, "subjects": subjects})
        items.append(
            _item(
                family=DeltaFamily.API if contract.kind.value == "api" else _contract_family(contract),
                record_type=EvidenceRecordType.CONTRACT,
                stable_key=key,
                record_id=contract.contract_id,
                value=contract,
                provider_run_ids=contract.provider_run_ids,
                payload=_normalized_payload(contract),
            )
        )
    for candidate in evidence.candidates:
        if candidate.kind is not CandidateKind.DUPLICATION:
            continue
        key = _stable_json(
            {
                "kind": candidate.kind.value,
                "method": candidate.method,
                "members": sorted(entity_keys[item] for item in candidate.member_entity_ids),
            }
        )
        items.append(
            _item(
                family=DeltaFamily.DUPLICATE,
                record_type=EvidenceRecordType.CANDIDATE,
                stable_key=key,
                record_id=candidate.candidate_id,
                value=candidate,
                provider_run_ids=candidate.provider_run_ids,
                payload=_normalized_payload(candidate),
            )
        )
    for observation in evidence.observations:
        family = _observation_family(observation)
        if family is None:
            continue
        key = _observation_key(observation, entity_keys)
        items.append(
            _item(
                family=family,
                record_type=EvidenceRecordType.OBSERVATION,
                stable_key=key,
                record_id=observation.observation_id,
                value=observation,
                provider_run_ids=[observation.provider_run_id],
                payload=_normalized_payload(observation),
            )
        )
    for artifact in evidence.provider_artifacts:
        key = _stable_json(
            {"media_type": artifact.media_type, "schema": artifact.schema_version, "locator": artifact.locator}
        )
        items.append(
            _item(
                family=DeltaFamily.ARTIFACT,
                record_type=EvidenceRecordType.PROVIDER_ARTIFACT,
                stable_key=key,
                record_id=artifact.artifact_id,
                value=artifact,
                provider_run_ids=artifact_runs.get(artifact.artifact_id, []),
                payload=_normalized_payload(artifact),
            )
        )
    result: dict[tuple[EvidenceRecordType, str], _Comparable] = {}
    for item in items:
        inventory_key = (item.record_type, item.stable_key)
        if inventory_key in result:
            # Multiple claims with one domain key remain independently comparable.
            inventory_key = (item.record_type, f"{item.stable_key}:{item.record_id}")
        result[inventory_key] = item
    return result


def _item(
    *,
    family: DeltaFamily,
    record_type: EvidenceRecordType,
    stable_key: str,
    record_id: str,
    value: object,
    provider_run_ids: list[str],
    payload: object,
) -> _Comparable:
    return _Comparable(
        family=family,
        record_type=record_type,
        stable_key=stable_key,
        record_id=record_id,
        payload_digest=sha256_digest(canonical_json_bytes(canonicalize_json(payload))),
        provider_run_ids=tuple(sorted(provider_run_ids)),
        value=value,
    )


def _entity_key(entity: EntityRecord, locations: dict[str, Any]) -> str:
    paths = sorted(
        item.path
        for location_id in entity.location_ids
        if (item := locations.get(location_id)) is not None and item.path is not None
    )
    common: dict[str, object] = {"type": entity.entity_type.value, "paths": paths}
    if isinstance(entity, RepositoryEntity):
        common["repository"] = entity.repository_id
    elif isinstance(entity, FileEntity):
        common["path"] = entity.path
    elif isinstance(entity, SymbolEntity):
        common.update(language=entity.language, qualified_name=entity.qualified_name)
    elif isinstance(entity, DocumentationEntity):
        common.update(kind=entity.documentation_kind, heading=entity.heading)
    elif isinstance(entity, TestEntity):
        common.update(kind=entity.test_kind, framework=entity.framework, name=entity.test_name)
    elif isinstance(entity, ConfigurationEntity):
        common.update(kind=entity.configuration_kind, key=entity.key, name=entity.display_name)
    elif isinstance(entity, DataEntity):
        common.update(kind=entity.data_kind, name=entity.display_name)
    elif isinstance(entity, WorkflowEntity):
        common.update(kind=entity.workflow_kind, rule=entity.rule_name)
    elif isinstance(entity, ArtifactEntity):
        common.update(kind=entity.artifact_kind, name=entity.display_name)
    elif isinstance(entity, DependencyEntity):
        common.update(ecosystem=entity.ecosystem, package=entity.package, scope=entity.scope)
    elif isinstance(entity, DiagnosticEntity):
        common.update(rule=entity.rule_id, name=entity.display_name)
    elif isinstance(entity, SimilarityEntity):
        common.update(method=entity.method, normalization=entity.normalization)
    elif isinstance(entity, RangeEntity):
        common.update(kind=entity.range_kind, name=entity.display_name)
    elif isinstance(entity, ExternalEntity):
        common.update(scheme=entity.identity_scheme, identity=entity.external_identity)
    return _stable_json(common)


def _entity_payload(entity: EntityRecord, locations: dict[str, Any]) -> object:
    payload = _normalized_payload(entity)
    if not isinstance(payload, dict):
        return payload
    normalized_locations = [
        _normalized_payload(locations[item])
        for item in entity.location_ids
        if item in locations
    ]
    payload["locations"] = sorted(normalized_locations, key=_stable_json)
    return payload


def _normalized_payload(value: object) -> object:
    if not hasattr(value, "model_dump"):
        return value
    payload = value.model_dump(mode="json")
    for key in list(payload):
        if key.endswith("_id") or key.endswith("_ids") or key in {"source_state_id", "provider_run_ids"}:
            payload.pop(key)
    return canonicalize_json(payload)


def _entity_family(entity: EntityRecord) -> DeltaFamily:
    if isinstance(entity, DiagnosticEntity):
        return DeltaFamily.DIAGNOSTIC
    if isinstance(entity, (TestEntity,)):
        return DeltaFamily.TEST
    if isinstance(entity, DocumentationEntity):
        return DeltaFamily.DOCUMENTATION
    if isinstance(entity, WorkflowEntity):
        return DeltaFamily.WORKFLOW
    if isinstance(entity, DataEntity):
        return DeltaFamily.DATA
    if isinstance(entity, (ConfigurationEntity, DependencyEntity)):
        return DeltaFamily.ENVIRONMENT
    if isinstance(entity, ArtifactEntity):
        return DeltaFamily.ARTIFACT
    if isinstance(entity, SimilarityEntity):
        return DeltaFamily.DUPLICATE
    return DeltaFamily.ENTITY


def _contract_family(contract: ContractRecord) -> DeltaFamily:
    return {
        "configuration": DeltaFamily.ENVIRONMENT,
        "data": DeltaFamily.DATA,
        "workflow": DeltaFamily.WORKFLOW,
    }.get(contract.kind.value, DeltaFamily.ENTITY)


def _observation_family(observation: ObservationRecord) -> DeltaFamily | None:
    if isinstance(observation, DiagnosticObservation):
        return DeltaFamily.DIAGNOSTIC
    if isinstance(observation, RuntimeObservation):
        return DeltaFamily.TEST
    return None


def _observation_key(observation: ObservationRecord, entity_keys: dict[str, str]) -> str:
    subjects = sorted(
        entity_keys[item]
        for item in getattr(observation, "subject_entity_ids", [])
        if item in entity_keys
    )
    return _stable_json(
        {
            "type": observation.record_type,
            "method": observation.method,
            "subjects": subjects,
            "rule": getattr(observation, "rule_id", None),
        }
    )


def _exact_file_moves(
    removed: dict[tuple[EvidenceRecordType, str], _Comparable],
    added: dict[tuple[EvidenceRecordType, str], _Comparable],
) -> list[tuple[tuple[EvidenceRecordType, str], tuple[EvidenceRecordType, str]]]:
    left_by_digest: dict[str, list[tuple[EvidenceRecordType, str]]] = {}
    right_by_digest: dict[str, list[tuple[EvidenceRecordType, str]]] = {}
    for key, item in removed.items():
        if isinstance(item.value, FileEntity) and item.value.digest:
            left_by_digest.setdefault(item.value.digest, []).append(key)
    for key, item in added.items():
        if isinstance(item.value, FileEntity) and item.value.digest:
            right_by_digest.setdefault(item.value.digest, []).append(key)
    return [
        (left_by_digest[digest][0], right_by_digest[digest][0])
        for digest in sorted(left_by_digest.keys() & right_by_digest.keys())
        if len(left_by_digest[digest]) == 1 and len(right_by_digest[digest]) == 1
    ]


def _delta(
    left: _Comparable | None,
    right: _Comparable | None,
    *,
    status: DeltaStatus,
    before_state: str,
    after_state: str,
    lineage_ids: list[str],
    rationale: str,
) -> SemanticDelta:
    item = left or right
    if item is None:
        raise TypeError("delta requires at least one record")
    reason = {
        DeltaFamily.ENTITY: DeltaReason.CONTENT_CHANGED,
        DeltaFamily.RELATIONSHIP: DeltaReason.RELATIONSHIP_CHANGED,
        DeltaFamily.API: DeltaReason.CONTRACT_CHANGED,
        DeltaFamily.DIAGNOSTIC: DeltaReason.DIAGNOSTIC_CHANGED,
        DeltaFamily.DUPLICATE: DeltaReason.DUPLICATE_MEMBERSHIP_CHANGED,
        DeltaFamily.TEST: DeltaReason.TEST_INTENT_CHANGED,
        DeltaFamily.DOCUMENTATION: DeltaReason.DOCUMENTATION_CHANGED,
        DeltaFamily.WORKFLOW: DeltaReason.WORKFLOW_CHANGED,
        DeltaFamily.DATA: DeltaReason.DATA_CHANGED,
        DeltaFamily.ENVIRONMENT: DeltaReason.ENVIRONMENT_CHANGED,
        DeltaFamily.ARTIFACT: DeltaReason.ARTIFACT_CHANGED,
    }[item.family]
    return build_semantic_delta(
        family=item.family,
        status=status,
        predecessor_refs=(
            [
                EvidenceTargetReference(
                    source_state_id=before_state,
                    record_type=left.record_type,
                    record_id=left.record_id,
                )
            ]
            if left is not None
            else []
        ),
        successor_refs=(
            [
                EvidenceTargetReference(
                    source_state_id=after_state,
                    record_type=right.record_type,
                    record_id=right.record_id,
                )
            ]
            if right is not None
            else []
        ),
        lineage_ids=lineage_ids,
        provider_run_ids=sorted(
            set((left.provider_run_ids if left else ()) + (right.provider_run_ids if right else ()))
        ),
        required_provider_ids=[],
        availability=DeltaAvailability.COMPLETE,
        strength=EvidenceStrength.EXACT,
        reason_codes=[DeltaReason.LINEAGE, reason] if lineage_ids else [reason],
        rationale=rationale,
    )


def _lineage(
    *,
    before_state: str,
    after_state: str,
    before_ids: list[str],
    after_ids: list[str],
    kind: LineageKind,
    reasons: list[LineageReason],
    rationale: str,
) -> LineageRecord:
    payload = {
        "before_state": before_state,
        "after_state": after_state,
        "before_ids": before_ids,
        "after_ids": after_ids,
        "kind": kind.value,
        "reasons": [item.value for item in reasons],
    }
    return LineageRecord(
        lineage_id=content_id("lineage:exact-comparison", payload),
        predecessor_state_id=before_state,
        successor_state_id=after_state,
        predecessor_entity_ids=before_ids,
        successor_entity_ids=after_ids,
        kind=kind,
        certainty=LineageCertainty.EXACT,
        method="stable-domain-key-and-content-digest",
        strength=EvidenceStrength.EXACT,
        reason_codes=reasons,
        rationale=rationale,
    )


def _stable_json(value: object) -> str:
    return canonical_json_bytes(canonicalize_json(value)).decode("utf-8")
