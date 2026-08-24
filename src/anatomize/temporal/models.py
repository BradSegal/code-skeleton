"""Exact state manifests, lineage, and provider-aware semantic deltas."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from anatomize._artifacts import canonical_json_bytes, content_id
from anatomize.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    ArtifactEntity,
    CandidateKind,
    ConfigurationEntity,
    ContentClass,
    ContractKind,
    DataEntity,
    DependencyEntity,
    DiagnosticEntity,
    DiagnosticObservation,
    DocumentationEntity,
    EvidenceModel,
    EvidenceStrength,
    FileEntity,
    LineageRecord,
    ProviderRunStatus,
    RepositoryEvidence,
    RuntimeObservation,
    SimilarityEntity,
    SourceStateRecord,
    TestEntity,
    WorkflowEntity,
    evidence_digest,
)

STATE_MANIFEST_ARTIFACT_TYPE: Literal["anatomize.state-manifest"] = "anatomize.state-manifest"
STATE_MANIFEST_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
COMPARISON_ARTIFACT_TYPE: Literal["anatomize.comparison"] = "anatomize.comparison"
COMPARISON_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class HistoryStatus(str, Enum):
    """Availability and trustworthiness of source history around one state."""

    COMPLETE = "complete"
    SHALLOW = "shallow"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


class ComparisonStatus(str, Enum):
    """Overall ability to compare two exact state manifests."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class BoundaryDisposition(str, Enum):
    """Whether one boundary issue qualifies or prevents comparison."""

    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ComparisonIssueCode(str, Enum):
    """Stable boundary conditions that affect temporal interpretation."""

    ABSENT_BASELINE = "absent_baseline"
    SHALLOW_HISTORY = "shallow_history"
    HISTORY_UNAVAILABLE = "history_unavailable"
    AMBIGUOUS_HISTORY = "ambiguous_history"
    DIRTY_WORKTREE = "dirty_worktree"
    GENERATED_OUTPUT = "generated_output"
    PROVIDER_MISMATCH = "provider_mismatch"
    CONFIGURATION_MISMATCH = "configuration_mismatch"


class DeltaFamily(str, Enum):
    """Lifecycle evidence family changed between two states."""

    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    API = "api"
    DIAGNOSTIC = "diagnostic"
    DUPLICATE = "duplicate"
    TEST = "test"
    DOCUMENTATION = "documentation"
    WORKFLOW = "workflow"
    DATA = "data"
    ENVIRONMENT = "environment"
    ARTIFACT = "artifact"


class DeltaStatus(str, Enum):
    """Directional semantic change for one evidence target."""

    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


class DeltaAvailability(str, Enum):
    """Coverage available to establish one delta."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class DeltaReason(str, Enum):
    """Portable reason for one semantic delta or unknown state."""

    LINEAGE = "lineage"
    CONTENT_CHANGED = "content_changed"
    RELATIONSHIP_CHANGED = "relationship_changed"
    CONTRACT_CHANGED = "contract_changed"
    DIAGNOSTIC_CHANGED = "diagnostic_changed"
    DUPLICATE_MEMBERSHIP_CHANGED = "duplicate_membership_changed"
    TEST_INTENT_CHANGED = "test_intent_changed"
    DOCUMENTATION_CHANGED = "documentation_changed"
    WORKFLOW_CHANGED = "workflow_changed"
    DATA_CHANGED = "data_changed"
    ENVIRONMENT_CHANGED = "environment_changed"
    ARTIFACT_CHANGED = "artifact_changed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_MISMATCH = "provider_mismatch"
    GENERATED_PROJECTION = "generated_projection"
    AMBIGUOUS_HISTORY = "ambiguous_history"


class EvidenceRecordType(str, Enum):
    """Addressable record collections in an external evidence artifact."""

    ENTITY = "entity"
    EDGE = "edge"
    CONTRACT = "contract"
    CANDIDATE = "candidate"
    OBSERVATION = "observation"
    PROVIDER_ARTIFACT = "provider_artifact"


class StateArtifactBinding(EvidenceModel):
    """One provider artifact identity retained without its native payload."""

    artifact_id: str = Field(min_length=1)
    digest: str = Field(min_length=1)


class ProviderStateBinding(EvidenceModel):
    """Exact provider/configuration/artifact state used for one source state."""

    provider_run_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    configuration_digest: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    artifacts: list[StateArtifactBinding] = Field(default_factory=list)
    status: ProviderRunStatus

    @model_validator(mode="after")
    def validate_provider_binding(self) -> ProviderStateBinding:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("provider state capabilities must be unique")
        artifact_ids = [item.artifact_id for item in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("provider state artifact identifiers must be unique")
        return self


class StateManifest(EvidenceModel):
    """Portable exact state identity for one side of a comparison."""

    artifact_type: Literal["anatomize.state-manifest"] = STATE_MANIFEST_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = STATE_MANIFEST_SCHEMA_VERSION
    repository_id: str = Field(min_length=1)
    source_state: SourceStateRecord
    evidence_schema_version: Literal["1.0.0"] = EVIDENCE_SCHEMA_VERSION
    evidence_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    configuration_digest: str = Field(min_length=1)
    provider_states: list[ProviderStateBinding] = Field(default_factory=list)
    history_status: HistoryStatus
    baseline_available: bool
    generated_entity_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest(self) -> StateManifest:
        if self.source_state.repository_id != self.repository_id:
            raise ValueError("state manifest source belongs to another repository")
        run_ids = [item.provider_run_id for item in self.provider_states]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("state manifest provider runs must be unique")
        if len(self.generated_entity_ids) != len(set(self.generated_entity_ids)):
            raise ValueError("state manifest generated entities must be unique")
        return self


class ComparisonBoundaryIssue(EvidenceModel):
    """One explicit temporal boundary failure or degradation."""

    issue_id: str = Field(min_length=1)
    code: ComparisonIssueCode
    disposition: BoundaryDisposition
    side: Literal["before", "after", "comparison"]
    rationale: str = Field(min_length=1)
    remediation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_issue_identity(self) -> ComparisonBoundaryIssue:
        expected = content_id(
            "comparison-issue",
            {
                "code": self.code.value,
                "disposition": self.disposition.value,
                "side": self.side,
                "rationale": self.rationale,
                "remediation": self.remediation,
            },
        )
        if self.issue_id != expected:
            raise ValueError("comparison issue identifier does not match its content")
        return self


class EvidenceTargetReference(EvidenceModel):
    """Source-bound reference into one external canonical evidence artifact."""

    source_state_id: str = Field(min_length=1)
    record_type: EvidenceRecordType
    record_id: str = Field(min_length=1)


class SemanticDelta(EvidenceModel):
    """One provider-aware semantic difference without a consumer verdict."""

    delta_id: str = Field(min_length=1)
    family: DeltaFamily
    status: DeltaStatus
    predecessor_refs: list[EvidenceTargetReference] = Field(default_factory=list)
    successor_refs: list[EvidenceTargetReference] = Field(default_factory=list)
    lineage_ids: list[str] = Field(default_factory=list)
    provider_run_ids: list[str] = Field(default_factory=list)
    required_provider_ids: list[str] = Field(default_factory=list)
    availability: DeltaAvailability
    strength: EvidenceStrength
    reason_codes: list[DeltaReason] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_delta_shape(self) -> SemanticDelta:
        if self.status is DeltaStatus.ADDED and (self.predecessor_refs or not self.successor_refs):
            raise ValueError("added delta requires only successor references")
        if self.status is DeltaStatus.REMOVED and (not self.predecessor_refs or self.successor_refs):
            raise ValueError("removed delta requires only predecessor references")
        if self.status in {DeltaStatus.CHANGED, DeltaStatus.UNCHANGED}:
            if not self.predecessor_refs or not self.successor_refs:
                raise ValueError(f"{self.status.value} delta requires both comparison sides")
        if self.availability is DeltaAvailability.UNAVAILABLE and self.status is not DeltaStatus.UNKNOWN:
            raise ValueError("unavailable delta evidence must retain unknown status")
        if self.status is DeltaStatus.UNKNOWN and self.availability is DeltaAvailability.COMPLETE:
            raise ValueError("unknown delta cannot claim complete evidence")
        for values, label in [
            (self.lineage_ids, "lineage"),
            (self.provider_run_ids, "provider run"),
            (self.required_provider_ids, "required provider"),
            ([item.value for item in self.reason_codes], "reason"),
        ]:
            if len(values) != len(set(values)):
                raise ValueError(f"semantic delta {label} values must be unique")
        allowed_record_types = _DELTA_RECORD_TYPES[self.family]
        invalid = sorted(
            {
                reference.record_type.value
                for reference in [*self.predecessor_refs, *self.successor_refs]
                if reference.record_type not in allowed_record_types
            }
        )
        if invalid:
            raise ValueError(f"{self.family.value} delta cannot reference record types: {invalid}")
        if self.delta_id != content_id("delta", _semantic_delta_payload(self)):
            raise ValueError("semantic delta identifier does not match its content")
        return self


class RepositoryComparison(EvidenceModel):
    """Portable two-state lineage and semantic-delta artifact."""

    artifact_type: Literal["anatomize.comparison"] = COMPARISON_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = COMPARISON_SCHEMA_VERSION
    repository_id: str = Field(min_length=1)
    before: StateManifest | None
    after: StateManifest
    status: ComparisonStatus
    issues: list[ComparisonBoundaryIssue] = Field(default_factory=list)
    lineage: list[LineageRecord] = Field(default_factory=list)
    deltas: list[SemanticDelta] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_comparison(self) -> RepositoryComparison:
        if self.after.repository_id != self.repository_id:
            raise ValueError("after manifest belongs to another repository")
        if self.before is not None:
            if self.before.repository_id != self.repository_id:
                raise ValueError("before manifest belongs to another repository")
            if self.before.source_state.state_id == self.after.source_state.state_id:
                raise ValueError("comparison sides must be different source states")
        issue_ids = [item.issue_id for item in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("comparison issue identifiers must be unique")
        lineage_ids = [item.lineage_id for item in self.lineage]
        delta_ids = [item.delta_id for item in self.deltas]
        if len(lineage_ids) != len(set(lineage_ids)) or len(delta_ids) != len(set(delta_ids)):
            raise ValueError("comparison lineage and delta identifiers must be unique")

        expected_status = _comparison_status(self.issues, self.deltas)
        if self.status is not expected_status:
            raise ValueError(f"comparison status must be {expected_status.value} for its boundary issues")
        expected_by_key = {
            (item.code, item.side): item for item in _boundary_issues(self.before, self.after)
        }
        actual_by_key = {(item.code, item.side): item for item in self.issues}
        expected_issue_keys = set(expected_by_key)
        actual_issue_keys = set(actual_by_key)
        if actual_issue_keys != expected_issue_keys:
            missing = sorted(f"{code.value}:{side}" for code, side in expected_issue_keys - actual_issue_keys)
            extra = sorted(f"{code.value}:{side}" for code, side in actual_issue_keys - expected_issue_keys)
            raise ValueError(f"comparison boundary issues differ; missing={missing}, extra={extra}")
        if any(actual_by_key[key] != expected_by_key[key] for key in expected_issue_keys):
            raise ValueError("comparison boundary issue content differs from its manifest-derived issue")
        if self.before is None and (self.lineage or self.deltas):
            raise ValueError("comparison without a baseline cannot contain lineage or deltas")
        if self.before is not None:
            before_state = self.before.source_state.state_id
            after_state = self.after.source_state.state_id
            for lineage in self.lineage:
                if lineage.predecessor_state_id != before_state or lineage.successor_state_id != after_state:
                    raise ValueError(f"lineage {lineage.lineage_id} does not bind the comparison sides")
            lineage_id_set = set(lineage_ids)
            provider_runs = {
                item.provider_run_id
                for manifest in (self.before, self.after)
                for item in manifest.provider_states
            }
            provider_ids = {
                item.provider_id
                for manifest in (self.before, self.after)
                for item in manifest.provider_states
            }
            for delta in self.deltas:
                if any(item.source_state_id != before_state for item in delta.predecessor_refs):
                    raise ValueError(f"delta {delta.delta_id} predecessor references bind another state")
                if any(item.source_state_id != after_state for item in delta.successor_refs):
                    raise ValueError(f"delta {delta.delta_id} successor references bind another state")
                unknown_lineage = sorted(set(delta.lineage_ids).difference(lineage_id_set))
                if unknown_lineage:
                    raise ValueError(f"delta {delta.delta_id} references unknown lineage: {unknown_lineage}")
                unknown_runs = sorted(set(delta.provider_run_ids).difference(provider_runs))
                if unknown_runs:
                    raise ValueError(f"delta {delta.delta_id} references unknown provider runs: {unknown_runs}")
                missing_providers = sorted(set(delta.required_provider_ids).difference(provider_ids))
                if missing_providers and delta.availability is DeltaAvailability.COMPLETE:
                    raise ValueError(
                        f"delta {delta.delta_id} cannot be complete without providers: {missing_providers}"
                    )
        return self


_DELTA_RECORD_TYPES: dict[DeltaFamily, frozenset[EvidenceRecordType]] = {
    DeltaFamily.ENTITY: frozenset({EvidenceRecordType.ENTITY}),
    DeltaFamily.RELATIONSHIP: frozenset({EvidenceRecordType.EDGE}),
    DeltaFamily.API: frozenset({EvidenceRecordType.CONTRACT}),
    DeltaFamily.DIAGNOSTIC: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.OBSERVATION}),
    DeltaFamily.DUPLICATE: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.CANDIDATE}),
    DeltaFamily.TEST: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.OBSERVATION}),
    DeltaFamily.DOCUMENTATION: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.CONTRACT}),
    DeltaFamily.WORKFLOW: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.CONTRACT}),
    DeltaFamily.DATA: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.CONTRACT}),
    DeltaFamily.ENVIRONMENT: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.CONTRACT}),
    DeltaFamily.ARTIFACT: frozenset({EvidenceRecordType.ENTITY, EvidenceRecordType.PROVIDER_ARTIFACT}),
}


def build_state_manifest(
    evidence: RepositoryEvidence,
    *,
    source_state_id: str,
    configuration_digest: str,
    history_status: HistoryStatus,
    baseline_available: bool,
) -> StateManifest:
    """Bind one source state to exact evidence, provider, configuration, and artifact state."""
    states = {item.state_id: item for item in evidence.states}
    if source_state_id not in states:
        raise ValueError(f"state manifest source is absent from evidence: {source_state_id}")
    source_state = states[source_state_id]
    artifacts = {item.artifact_id: item for item in evidence.provider_artifacts}
    provider_states = []
    for run in sorted(
        (item for item in evidence.provider_runs if item.source_state_id == source_state_id),
        key=lambda item: item.provider_run_id,
    ):
        provider_states.append(
            ProviderStateBinding(
                provider_run_id=run.provider_run_id,
                provider_id=run.provider_id,
                provider_version=run.provider_version,
                configuration_digest=run.configuration_digest,
                capabilities=sorted(set(run.capabilities)),
                artifacts=[
                    StateArtifactBinding(artifact_id=artifact_id, digest=artifacts[artifact_id].digest)
                    for artifact_id in sorted(set(run.artifact_ids))
                ],
                status=run.status,
            )
        )
    generated_entity_ids = sorted(
        entity.entity_id
        for entity in evidence.entities
        if entity.source_state_id == source_state_id
        and (
            isinstance(entity, ArtifactEntity)
            and entity.generated
            or isinstance(entity, FileEntity)
            and entity.content_class is ContentClass.GENERATED
        )
    )
    return StateManifest(
        repository_id=evidence.repository_id,
        source_state=source_state,
        evidence_artifact_digest=evidence_digest(evidence),
        configuration_digest=configuration_digest,
        provider_states=provider_states,
        history_status=history_status,
        baseline_available=baseline_available,
        generated_entity_ids=generated_entity_ids,
    )


def build_repository_comparison(
    *,
    repository_id: str,
    before: StateManifest | None,
    after: StateManifest,
    lineage: list[LineageRecord],
    deltas: list[SemanticDelta],
    limitations: list[str] | None = None,
) -> RepositoryComparison:
    """Build one comparison and derive every manifest-visible boundary issue."""
    issues = _boundary_issues(before, after)
    return RepositoryComparison(
        repository_id=repository_id,
        before=before,
        after=after,
        status=_comparison_status(issues, deltas),
        issues=issues,
        lineage=lineage,
        deltas=deltas,
        limitations=limitations or [],
    )


def build_semantic_delta(
    *,
    family: DeltaFamily,
    status: DeltaStatus,
    predecessor_refs: list[EvidenceTargetReference],
    successor_refs: list[EvidenceTargetReference],
    lineage_ids: list[str],
    provider_run_ids: list[str],
    required_provider_ids: list[str],
    availability: DeltaAvailability,
    strength: EvidenceStrength,
    reason_codes: list[DeltaReason],
    rationale: str,
) -> SemanticDelta:
    """Create a content-addressed semantic delta from its complete meaning."""
    sorted_lineage_ids = sorted(lineage_ids)
    sorted_provider_run_ids = sorted(provider_run_ids)
    sorted_required_provider_ids = sorted(required_provider_ids)
    sorted_reasons = sorted(reason_codes, key=lambda item: item.value)
    payload: dict[str, object] = {
        "family": family.value,
        "status": status.value,
        "predecessor_refs": [item.model_dump(mode="json") for item in predecessor_refs],
        "successor_refs": [item.model_dump(mode="json") for item in successor_refs],
        "lineage_ids": sorted_lineage_ids,
        "provider_run_ids": sorted_provider_run_ids,
        "required_provider_ids": sorted_required_provider_ids,
        "availability": availability.value,
        "strength": strength.value,
        "reason_codes": [item.value for item in sorted_reasons],
        "rationale": rationale,
    }
    return SemanticDelta(
        delta_id=content_id("delta", payload),
        family=family,
        status=status,
        predecessor_refs=predecessor_refs,
        successor_refs=successor_refs,
        lineage_ids=sorted_lineage_ids,
        provider_run_ids=sorted_provider_run_ids,
        required_provider_ids=sorted_required_provider_ids,
        availability=availability,
        strength=strength,
        reason_codes=sorted_reasons,
        rationale=rationale,
    )


def validate_comparison_evidence(
    comparison: RepositoryComparison,
    *,
    before_evidence: RepositoryEvidence | None,
    after_evidence: RepositoryEvidence,
) -> None:
    """Validate every external comparison reference against its digest-bound evidence."""
    _validate_manifest_evidence(comparison.after, after_evidence, side="after")
    if comparison.before is None:
        if before_evidence is not None:
            raise ValueError("before evidence was supplied for a comparison without a baseline")
        return
    if before_evidence is None:
        raise ValueError("comparison baseline evidence is required")
    _validate_manifest_evidence(comparison.before, before_evidence, side="before")
    before_entities = {item.entity_id for item in before_evidence.entities}
    after_entities = {item.entity_id for item in after_evidence.entities}
    for lineage in comparison.lineage:
        _require_known(lineage.predecessor_entity_ids, before_entities, f"lineage {lineage.lineage_id} before")
        _require_known(lineage.successor_entity_ids, after_entities, f"lineage {lineage.lineage_id} after")
    for delta in comparison.deltas:
        for reference in delta.predecessor_refs:
            _validate_target(reference, before_evidence, family=delta.family)
        for reference in delta.successor_refs:
            _validate_target(reference, after_evidence, family=delta.family)


def canonical_comparison_bytes(comparison: RepositoryComparison) -> bytes:
    """Serialize a comparison deterministically for review and hashing."""
    return canonical_json_bytes(comparison.model_dump(mode="json"))


def _boundary_issues(before: StateManifest | None, after: StateManifest) -> list[ComparisonBoundaryIssue]:
    issues: list[ComparisonBoundaryIssue] = []
    if before is None or not after.baseline_available:
        issues.append(
            _issue(
                ComparisonIssueCode.ABSENT_BASELINE,
                BoundaryDisposition.UNAVAILABLE,
                "comparison",
                "The selected baseline is unavailable.",
                "Acquire and index an exact baseline state before comparing.",
            )
        )
        return issues
    manifest_sides: tuple[
        tuple[Literal["before", "after"], StateManifest],
        tuple[Literal["before", "after"], StateManifest],
    ] = (("before", before), ("after", after))
    for side, manifest in manifest_sides:
        if manifest.history_status is HistoryStatus.SHALLOW:
            issues.append(
                _issue(
                    ComparisonIssueCode.SHALLOW_HISTORY,
                    BoundaryDisposition.DEGRADED,
                    side,
                    "Repository history is shallow, so continuity evidence is incomplete.",
                    "Acquire sufficient history or retain lineage as candidate evidence.",
                )
            )
        elif manifest.history_status is HistoryStatus.UNAVAILABLE:
            issues.append(
                _issue(
                    ComparisonIssueCode.HISTORY_UNAVAILABLE,
                    BoundaryDisposition.DEGRADED,
                    side,
                    "Repository history is unavailable; only exact state comparison is possible.",
                    "Provide history evidence when historical continuity is required.",
                )
            )
        elif manifest.history_status is HistoryStatus.AMBIGUOUS:
            issues.append(
                _issue(
                    ComparisonIssueCode.AMBIGUOUS_HISTORY,
                    BoundaryDisposition.DEGRADED,
                    side,
                    "History has more than one plausible continuity mapping.",
                    "Preserve all candidates or provide exact external history evidence.",
                )
            )
        if manifest.source_state.dirty:
            issues.append(
                _issue(
                    ComparisonIssueCode.DIRTY_WORKTREE,
                    BoundaryDisposition.DEGRADED,
                    side,
                    "The exact state includes uncommitted content.",
                    "Retain the content digest or compare clean committed states when Git history is required.",
                )
            )
        if manifest.generated_entity_ids:
            issues.append(
                _issue(
                    ComparisonIssueCode.GENERATED_OUTPUT,
                    BoundaryDisposition.DEGRADED,
                    side,
                    "Generated entities require explicit source projections for lineage.",
                    "Use generated-location projections and keep uncertain lineage as candidates.",
                )
            )
    before_providers = {
        (item.provider_id, item.provider_version, item.configuration_digest) for item in before.provider_states
    }
    after_providers = {
        (item.provider_id, item.provider_version, item.configuration_digest) for item in after.provider_states
    }
    if before_providers != after_providers:
        issues.append(
            _issue(
                ComparisonIssueCode.PROVIDER_MISMATCH,
                BoundaryDisposition.DEGRADED,
                "comparison",
                "Provider identity, version, or provider configuration differs between states.",
                "Compare overlapping evidence families and mark provider-dependent deltas partial or unavailable.",
            )
        )
    if before.configuration_digest != after.configuration_digest:
        issues.append(
            _issue(
                ComparisonIssueCode.CONFIGURATION_MISMATCH,
                BoundaryDisposition.DEGRADED,
                "comparison",
                "Aggregate acquisition configuration differs between states.",
                "Rebuild both states with one configuration or qualify affected deltas explicitly.",
            )
        )
    return sorted(issues, key=lambda item: (item.side, item.code.value))


def _issue(
    code: ComparisonIssueCode,
    disposition: BoundaryDisposition,
    side: Literal["before", "after", "comparison"],
    rationale: str,
    remediation: str,
) -> ComparisonBoundaryIssue:
    payload = {
        "code": code.value,
        "disposition": disposition.value,
        "side": side,
        "rationale": rationale,
        "remediation": remediation,
    }
    return ComparisonBoundaryIssue(
        issue_id=content_id("comparison-issue", payload),
        code=code,
        disposition=disposition,
        side=side,
        rationale=rationale,
        remediation=remediation,
    )


def _comparison_status(
    issues: list[ComparisonBoundaryIssue],
    deltas: list[SemanticDelta],
) -> ComparisonStatus:
    if any(item.disposition is BoundaryDisposition.UNAVAILABLE for item in issues):
        return ComparisonStatus.UNAVAILABLE
    if issues or any(item.availability is not DeltaAvailability.COMPLETE for item in deltas):
        return ComparisonStatus.PARTIAL
    return ComparisonStatus.COMPLETE


def _validate_target(
    reference: EvidenceTargetReference,
    evidence: RepositoryEvidence,
    *,
    family: DeltaFamily,
) -> None:
    state_ids = {item.state_id for item in evidence.states}
    if reference.source_state_id not in state_ids:
        raise ValueError(f"delta reference binds unknown source state: {reference.source_state_id}")
    collections: dict[EvidenceRecordType, list[object]] = {
        EvidenceRecordType.ENTITY: list(evidence.entities),
        EvidenceRecordType.EDGE: list(evidence.edges),
        EvidenceRecordType.CONTRACT: list(evidence.contracts),
        EvidenceRecordType.CANDIDATE: list(evidence.candidates),
        EvidenceRecordType.OBSERVATION: list(evidence.observations),
        EvidenceRecordType.PROVIDER_ARTIFACT: list(evidence.provider_artifacts),
    }
    id_fields = {
        EvidenceRecordType.ENTITY: "entity_id",
        EvidenceRecordType.EDGE: "edge_id",
        EvidenceRecordType.CONTRACT: "contract_id",
        EvidenceRecordType.CANDIDATE: "candidate_id",
        EvidenceRecordType.OBSERVATION: "observation_id",
        EvidenceRecordType.PROVIDER_ARTIFACT: "artifact_id",
    }
    record = next(
        (
            item
            for item in collections[reference.record_type]
            if getattr(item, id_fields[reference.record_type]) == reference.record_id
        ),
        None,
    )
    if record is None:
        raise ValueError(f"delta reference is absent from evidence: {reference.record_id}")
    record_state = getattr(record, "source_state_id", reference.source_state_id)
    if record_state != reference.source_state_id:
        raise ValueError(f"delta reference belongs to another source state: {reference.record_id}")
    if not _family_accepts_record(family, record):
        raise ValueError(f"{family.value} delta references an incompatible evidence record: {reference.record_id}")


def _validate_manifest_evidence(
    manifest: StateManifest,
    evidence: RepositoryEvidence,
    *,
    side: Literal["before", "after"],
) -> None:
    if evidence_digest(evidence) != manifest.evidence_artifact_digest:
        raise ValueError(f"{side} evidence digest does not match its state manifest")
    expected = build_state_manifest(
        evidence,
        source_state_id=manifest.source_state.state_id,
        configuration_digest=manifest.configuration_digest,
        history_status=manifest.history_status,
        baseline_available=manifest.baseline_available,
    )
    if expected != manifest:
        raise ValueError(f"{side} state manifest does not match its evidence provider or artifact state")


def _family_accepts_record(family: DeltaFamily, record: object) -> bool:
    if family in {DeltaFamily.ENTITY, DeltaFamily.RELATIONSHIP}:
        return True
    if family is DeltaFamily.API:
        return getattr(record, "kind", None) is ContractKind.API
    if family is DeltaFamily.DIAGNOSTIC:
        return isinstance(record, (DiagnosticEntity, DiagnosticObservation))
    if family is DeltaFamily.DUPLICATE:
        return isinstance(record, SimilarityEntity) or getattr(record, "kind", None) is CandidateKind.DUPLICATION
    if family is DeltaFamily.TEST:
        return isinstance(record, (TestEntity, RuntimeObservation))
    if family is DeltaFamily.DOCUMENTATION:
        return isinstance(record, DocumentationEntity) or getattr(record, "kind", None) is ContractKind.BEHAVIOR
    if family is DeltaFamily.WORKFLOW:
        return isinstance(record, WorkflowEntity) or getattr(record, "kind", None) is ContractKind.WORKFLOW
    if family is DeltaFamily.DATA:
        return isinstance(record, DataEntity) or getattr(record, "kind", None) is ContractKind.DATA
    if family is DeltaFamily.ENVIRONMENT:
        return isinstance(record, (ConfigurationEntity, DependencyEntity)) or getattr(
            record, "kind", None
        ) is ContractKind.CONFIGURATION
    if family is DeltaFamily.ARTIFACT:
        return isinstance(record, ArtifactEntity) or getattr(record, "record_type", None) == "provider_artifact"
    return False


def _require_known(values: list[str], allowed: set[str], label: str) -> None:
    unknown = sorted(set(values).difference(allowed))
    if unknown:
        raise ValueError(f"{label} references unknown entities: {unknown}")


def _semantic_delta_payload(delta: SemanticDelta) -> dict[str, object]:
    return {
        "family": delta.family.value,
        "status": delta.status.value,
        "predecessor_refs": [item.model_dump(mode="json") for item in delta.predecessor_refs],
        "successor_refs": [item.model_dump(mode="json") for item in delta.successor_refs],
        "lineage_ids": sorted(delta.lineage_ids),
        "provider_run_ids": sorted(delta.provider_run_ids),
        "required_provider_ids": sorted(delta.required_provider_ids),
        "availability": delta.availability.value,
        "strength": delta.strength.value,
        "reason_codes": sorted(item.value for item in delta.reason_codes),
        "rationale": delta.rationale,
    }
