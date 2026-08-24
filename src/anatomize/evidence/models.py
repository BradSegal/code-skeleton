"""Canonical provider-neutral repository evidence contracts."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVIDENCE_ARTIFACT_TYPE: Literal["anatomize.evidence"] = "anatomize.evidence"
EVIDENCE_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class EvidenceModel(BaseModel):
    """Strict immutable base for every public evidence record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def reject_checkout_paths_as_identifiers(self) -> EvidenceModel:
        """Prevent local checkout paths from becoming portable public identities."""
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if field_name.endswith("_id") and isinstance(value, str):
                validate_portable_identity(value, label=field_name)
            elif field_name.endswith("_ids") and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        validate_portable_identity(item, label=field_name)
        return self


class EntityKind(str, Enum):
    """Provider-neutral entity families represented by the kernel."""

    REPOSITORY = "repository"
    FILE = "file"
    RANGE = "range"
    SYMBOL = "symbol"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    TEST = "test"
    DATA = "data"
    WORKFLOW = "workflow"
    ARTIFACT = "artifact"
    DEPENDENCY = "dependency"
    DIAGNOSTIC = "diagnostic"
    RUNTIME = "runtime"
    SIMILARITY = "similarity"
    EXTERNAL = "external"


class EvidenceStrength(str, Enum):
    """Strength claimed by one observation, never an aggregate score."""

    EXACT = "exact"
    DERIVED = "derived"
    CONSERVATIVE = "conservative"
    HEURISTIC = "heuristic"
    DECLARED = "declared"
    UNKNOWN = "unknown"


class ObservationStance(str, Enum):
    """How an observation relates to its evidence target."""

    SUPPORTS = "supports"
    QUALIFIES = "qualifies"
    CONFLICTS = "conflicts"


class CompletenessStatus(str, Enum):
    """Coverage of one provider over one explicitly declared scope."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderRunStatus(str, Enum):
    """Terminal state of one provider attempt."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContentClass(str, Enum):
    """Privacy-aware file/content classification."""

    ORDINARY = "ordinary"
    BINARY = "binary"
    LARGE = "large"
    GENERATED = "generated"
    VENDOR = "vendor"
    IGNORED = "ignored"
    SENSITIVE = "sensitive"
    EXTERNAL = "external"
    OPAQUE = "opaque"


class LocationOrigin(str, Enum):
    """Coordinate origin for a portable location."""

    REPOSITORY = "repository"
    GENERATED = "generated"
    EXTERNAL = "external"
    OPAQUE = "opaque"


class ProjectionCompleteness(str, Enum):
    """Exactness of a coordinate projection into another source space."""

    EXACT = "exact"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class RelationshipCategory(str, Enum):
    """Broad semantic family for an extensible exact predicate."""

    STRUCTURE = "structure"
    DEPENDENCY = "dependency"
    CALL = "call"
    REFERENCE = "reference"
    IMPLEMENTATION = "implementation"
    OVERRIDE = "override"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    DATA = "data"
    WORKFLOW = "workflow"
    ARTIFACT = "artifact"
    PROVENANCE = "provenance"
    GENERATED = "generated"
    DECLARATION = "declaration"


class CandidateKind(str, Enum):
    """Review candidate types that remain distinct from established edges."""

    REFERENCE = "reference"
    SIMILARITY = "similarity"
    DUPLICATION = "duplication"
    LINEAGE = "lineage"
    OWNERSHIP = "ownership"
    DOCUMENTATION_DRIFT = "documentation_drift"


class ContractKind(str, Enum):
    """Declared contract families, kept separate from observed behavior."""

    API = "api"
    BEHAVIOR = "behavior"
    CONFIGURATION = "configuration"
    SCHEMA = "schema"
    DATA = "data"
    WORKFLOW = "workflow"
    RUNTIME = "runtime"


class ConflictStatus(str, Enum):
    """Whether contradictory observations have external adjudication."""

    UNRESOLVED = "unresolved"
    EXTERNALLY_RESOLVED = "externally_resolved"


class IdentityResolutionStatus(str, Enum):
    """Outcome of reconciling provider identity claims."""

    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"


class IdentityReason(str, Enum):
    """Portable reasons retained when reconciling identities."""

    DECLARED_ALIAS = "declared_alias"
    REEXPORT = "reexport"
    OVERLOAD = "overload"
    METHOD_SCOPE = "method_scope"
    ANONYMOUS_SCOPE = "anonymous_scope"
    GENERATED_SOURCE = "generated_source"
    VENDORED_SOURCE = "vendored_source"
    CASE_SENSITIVE_PATH = "case_sensitive_path"
    SYMLINK_BOUNDARY = "symlink_boundary"
    EXTERNAL_PACKAGE = "external_package"
    COORDINATE_PROJECTION = "coordinate_projection"
    NO_CANDIDATE = "no_candidate"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    PROVIDER_DISAGREEMENT = "provider_disagreement"
    STATE_MISMATCH = "state_mismatch"
    KIND_MISMATCH = "kind_mismatch"
    INCOMPLETE_SCOPE = "incomplete_scope"


class LineageCertainty(str, Enum):
    """Whether cross-state continuity is established or remains a candidate."""

    EXACT = "exact"
    CANDIDATE = "candidate"


class LineageKind(str, Enum):
    """Cross-state entity continuity shape."""

    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    MOVED = "moved"
    RENAMED = "renamed"
    SPLIT = "split"
    MERGED = "merged"
    DELETED = "deleted"
    ADDED = "added"


class LineageReason(str, Enum):
    """Portable reasons supporting or qualifying a lineage record."""

    EXACT_HISTORY = "exact_history"
    PROVIDER_IDENTITY = "provider_identity"
    CONTENT_IDENTITY = "content_identity"
    QUALIFIED_NAME_MATCH = "qualified_name_match"
    SIGNATURE_MATCH = "signature_match"
    PATH_CHANGED = "path_changed"
    NAME_CHANGED = "name_changed"
    CONTENT_CHANGED = "content_changed"
    SPLIT_CANDIDATE = "split_candidate"
    MERGE_CANDIDATE = "merge_candidate"
    GENERATED_PROJECTION = "generated_projection"
    ADDED_TO_SCOPE = "added_to_scope"
    REMOVED_FROM_SCOPE = "removed_from_scope"
    AMBIGUOUS_HISTORY = "ambiguous_history"
    PROVIDER_MISMATCH = "provider_mismatch"


class EvidenceProducer(EvidenceModel):
    """Identity of the software that wrote the canonical artifact."""

    name: Literal["anatomize"] = "anatomize"
    version: str = Field(min_length=1)


class SourcePosition(EvidenceModel):
    """One one-based line and zero-based Unicode-codepoint column."""

    line: int = Field(ge=1)
    column: int = Field(ge=0)


class SourceRange(EvidenceModel):
    """Half-open source range."""

    start: SourcePosition
    end: SourcePosition

    @model_validator(mode="after")
    def validate_order(self) -> SourceRange:
        """Reject inverted ranges."""
        if (self.end.line, self.end.column) < (self.start.line, self.start.column):
            raise ValueError("source range end must not precede start")
        return self


class FileCoordinateSpace(EvidenceModel):
    """Coordinates in the repository file named by the location."""

    space_type: Literal["file"] = "file"


class NotebookCellCoordinateSpace(EvidenceModel):
    """Coordinates in the decoded source of one stable notebook cell."""

    space_type: Literal["notebook_cell"] = "notebook_cell"
    cell_id: str = Field(min_length=1)
    cell_index: int = Field(ge=0)
    source_digest: str = Field(min_length=1)


class EmbeddedRegionCoordinateSpace(EvidenceModel):
    """Coordinates in one language region projected from a host location."""

    space_type: Literal["embedded_region"] = "embedded_region"
    region_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    host_location_id: str = Field(min_length=1)
    projection: ProjectionCompleteness


class GeneratedCoordinateSpace(EvidenceModel):
    """Coordinates in generated output with explicit source-location provenance."""

    space_type: Literal["generated"] = "generated"
    generator_identity_id: str = Field(min_length=1)
    source_location_ids: list[str] = Field(default_factory=list)
    projection: ProjectionCompleteness

    @model_validator(mode="after")
    def validate_projection_sources(self) -> GeneratedCoordinateSpace:
        if len(self.source_location_ids) != len(set(self.source_location_ids)):
            raise ValueError("generated projection source locations must be unique")
        if self.projection in {ProjectionCompleteness.EXACT, ProjectionCompleteness.PARTIAL}:
            if not self.source_location_ids:
                raise ValueError("exact or partial generated projections require source locations")
        return self


CoordinateSpace = Annotated[
    FileCoordinateSpace | NotebookCellCoordinateSpace | EmbeddedRegionCoordinateSpace | GeneratedCoordinateSpace,
    Field(discriminator="space_type"),
]


class SourceStateRecord(EvidenceModel):
    """Portable identity of one repository source state."""

    record_type: Literal["source_state"] = "source_state"
    state_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    revision: str | None = None
    dirty: bool
    content_digest: str = Field(min_length=1)
    file_count: int = Field(ge=0)


class ProviderArtifactRecord(EvidenceModel):
    """Digest-bound provider-native artifact metadata; payload remains external."""

    record_type: Literal["provider_artifact"] = "provider_artifact"
    artifact_id: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    schema_version: str | None = None
    byte_size: int = Field(ge=0)
    locator: str | None = None
    identity_verified: bool = False

    @field_validator("locator")
    @classmethod
    def reject_local_artifact_locator(cls, value: str | None) -> str | None:
        if value is not None:
            validate_portable_identity(value, label="provider artifact locator")
        return value


class ProviderRunRecord(EvidenceModel):
    """Identity, method and outcome of one provider attempt over one state."""

    record_type: Literal["provider_run"] = "provider_run"
    provider_run_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    configuration_digest: str = Field(min_length=1)
    method: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    status: ProviderRunStatus
    limitation_ids: list[str] = Field(default_factory=list)


class LocationRecord(EvidenceModel):
    """Portable repository, generated, external, or opaque coordinate."""

    record_type: Literal["location"] = "location"
    location_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    origin: LocationOrigin
    file_id: str | None = None
    path: str | None = None
    source_range: SourceRange | None = None
    coordinate_system: Literal["line-1-column-0-unicode"] = "line-1-column-0-unicode"
    coordinate_space: CoordinateSpace | None = None
    opaque_locator: str | None = None

    @field_validator("path")
    @classmethod
    def validate_optional_path(cls, value: str | None) -> str | None:
        """Require portable relative paths whenever a path is disclosed."""
        return validate_repository_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_locator_shape(self) -> LocationRecord:
        """Keep repository and opaque/external locators unambiguous."""
        if self.origin in {LocationOrigin.REPOSITORY, LocationOrigin.GENERATED}:
            if self.file_id is None or self.path is None:
                raise ValueError("repository/generated locations require file_id and path")
            if self.opaque_locator is not None:
                raise ValueError("repository/generated locations cannot carry opaque_locator")
            if self.coordinate_space is None:
                raise ValueError("repository/generated locations require an explicit coordinate space")
            if self.origin is LocationOrigin.GENERATED:
                if not isinstance(self.coordinate_space, GeneratedCoordinateSpace):
                    raise ValueError("generated locations require a generated coordinate space")
            elif isinstance(self.coordinate_space, GeneratedCoordinateSpace):
                raise ValueError("repository locations cannot use a generated coordinate space")
        elif self.path is not None:
            raise ValueError("external/opaque locations must not disclose a repository path")
        elif self.opaque_locator is None:
            raise ValueError("external/opaque locations require opaque_locator")
        elif self.coordinate_space is not None or self.source_range is not None:
            raise ValueError("external/opaque locations cannot claim repository coordinates")
        if self.opaque_locator is not None:
            validate_portable_identity(self.opaque_locator, label="opaque locator")
        return self


class EntityBase(EvidenceModel):
    """Fields shared by all provider-neutral entities."""

    entity_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    location_ids: list[str] = Field(default_factory=list)
    provider_run_ids: list[str] = Field(default_factory=list)


class RepositoryEntity(EntityBase):
    entity_type: Literal[EntityKind.REPOSITORY] = EntityKind.REPOSITORY
    repository_id: str = Field(min_length=1)
    root_name: str = Field(min_length=1)


class FileEntity(EntityBase):
    entity_type: Literal[EntityKind.FILE] = EntityKind.FILE
    path: str
    language: str | None = None
    digest: str | None = None
    size_bytes: int = Field(ge=0)
    roles: list[str] = Field(default_factory=list)
    content_class: ContentClass = ContentClass.ORDINARY

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_repository_path(value)


class RangeEntity(EntityBase):
    entity_type: Literal[EntityKind.RANGE] = EntityKind.RANGE
    range_kind: str = Field(min_length=1)
    parent_entity_id: str | None = None


class SymbolEntity(EntityBase):
    entity_type: Literal[EntityKind.SYMBOL] = EntityKind.SYMBOL
    language: str = Field(min_length=1)
    symbol_kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    public: bool | None = None
    digest: str | None = None


class DocumentationEntity(EntityBase):
    entity_type: Literal[EntityKind.DOCUMENTATION] = EntityKind.DOCUMENTATION
    documentation_kind: str = Field(min_length=1)
    heading: str | None = None
    digest: str | None = None


class ConfigurationEntity(EntityBase):
    entity_type: Literal[EntityKind.CONFIGURATION] = EntityKind.CONFIGURATION
    configuration_kind: str = Field(min_length=1)
    key: str | None = None


class TestEntity(EntityBase):
    entity_type: Literal[EntityKind.TEST] = EntityKind.TEST
    test_kind: str = Field(min_length=1)
    framework: str | None = None
    test_name: str = Field(min_length=1)


class DataEntity(EntityBase):
    entity_type: Literal[EntityKind.DATA] = EntityKind.DATA
    data_kind: str = Field(min_length=1)
    media_type: str | None = None
    content_class: ContentClass = ContentClass.OPAQUE
    schema_identity: str | None = None


class WorkflowEntity(EntityBase):
    entity_type: Literal[EntityKind.WORKFLOW] = EntityKind.WORKFLOW
    workflow_kind: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)


class ArtifactEntity(EntityBase):
    entity_type: Literal[EntityKind.ARTIFACT] = EntityKind.ARTIFACT
    artifact_kind: str = Field(min_length=1)
    digest: str | None = None
    media_type: str | None = None
    generated: bool = False


class DependencyEntity(EntityBase):
    entity_type: Literal[EntityKind.DEPENDENCY] = EntityKind.DEPENDENCY
    ecosystem: str = Field(min_length=1)
    package: str = Field(min_length=1)
    version: str | None = None
    scope: str | None = None


class DiagnosticSuppression(EvidenceModel):
    """Producer-declared suppression state without interpreting its validity."""

    kind: str = Field(min_length=1)
    status: str | None = None
    justification: str | None = None


class DiagnosticFixSuggestion(EvidenceModel):
    """Digest-bound producer fix retained explicitly as an untrusted suggestion."""

    suggestion_id: str = Field(min_length=1)
    description: str | None = None
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_change_count: int = Field(ge=0)
    replacement_count: int = Field(ge=0)
    trust: Literal["untrusted_suggestion"] = "untrusted_suggestion"


class DiagnosticEntity(EntityBase):
    entity_type: Literal[EntityKind.DIAGNOSTIC] = EntityKind.DIAGNOSTIC
    rule_id: str = Field(min_length=1)
    rule_name: str | None = None
    rule_help_uri: str | None = None
    severity: str | None = None
    result_kind: str | None = None
    baseline_state: str | None = None
    message: str = Field(min_length=1)
    suppressions: list[DiagnosticSuppression] = Field(default_factory=list)
    fix_suggestions: list[DiagnosticFixSuggestion] = Field(default_factory=list)
    fingerprints: dict[str, str] = Field(default_factory=dict)
    duplicate_count: int = Field(default=1, ge=1)

    @field_validator("rule_help_uri")
    @classmethod
    def reject_local_help_uri(cls, value: str | None) -> str | None:
        return validate_portable_identity(value, label="diagnostic rule help URI") if value else value


class RuntimeEntity(EntityBase):
    entity_type: Literal[EntityKind.RUNTIME] = EntityKind.RUNTIME
    runtime_kind: str = Field(min_length=1)
    status: str = Field(min_length=1)
    run_identity: str | None = None


class SimilarityEntity(EntityBase):
    entity_type: Literal[EntityKind.SIMILARITY] = EntityKind.SIMILARITY
    method: str = Field(min_length=1)
    normalization: str = Field(min_length=1)
    member_entity_ids: list[str] = Field(min_length=2)


class ExternalEntity(EntityBase):
    entity_type: Literal[EntityKind.EXTERNAL] = EntityKind.EXTERNAL
    identity_scheme: str = Field(min_length=1)
    external_identity: str = Field(min_length=1)
    entity_kind: str = Field(min_length=1)

    @field_validator("external_identity")
    @classmethod
    def reject_local_external_identity(cls, value: str) -> str:
        return validate_portable_identity(value, label="external identity")


EntityRecord = Annotated[
    RepositoryEntity
    | FileEntity
    | RangeEntity
    | SymbolEntity
    | DocumentationEntity
    | ConfigurationEntity
    | TestEntity
    | DataEntity
    | WorkflowEntity
    | ArtifactEntity
    | DependencyEntity
    | DiagnosticEntity
    | RuntimeEntity
    | SimilarityEntity
    | ExternalEntity,
    Field(discriminator="entity_type"),
]


class EdgeRecord(EvidenceModel):
    """Provider-neutral relationship target that can receive many observations."""

    record_type: Literal["edge"] = "edge"
    edge_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)
    category: RelationshipCategory
    predicate: str = Field(min_length=1)
    provider_run_ids: list[str] = Field(default_factory=list)


class ContractRecord(EvidenceModel):
    """A declared contract, distinct from observed behavior and diagnostics."""

    record_type: Literal["declared_contract"] = "declared_contract"
    contract_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    kind: ContractKind
    subject_entity_ids: list[str] = Field(min_length=1)
    declaration_location_ids: list[str] = Field(default_factory=list)
    provider_run_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    terms_digest: str = Field(min_length=1)


class CandidateRecord(EvidenceModel):
    """A bounded review candidate, never an established edge or verdict."""

    record_type: Literal["candidate"] = "candidate"
    candidate_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    kind: CandidateKind
    member_entity_ids: list[str] = Field(min_length=1)
    method: str = Field(min_length=1)
    strength: EvidenceStrength
    observation_ids: list[str] = Field(default_factory=list)
    provider_run_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ObservationBase(EvidenceModel):
    """Provenance retained independently for every observation."""

    observation_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    provider_run_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    method_version: str = Field(min_length=1)
    strength: EvidenceStrength
    stance: ObservationStance
    location_ids: list[str] = Field(default_factory=list)
    completeness_id: str | None = None
    limitation_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class StructuralObservation(ObservationBase):
    record_type: Literal["structural_observation"] = "structural_observation"
    target_type: Literal["entity", "edge", "contract"]
    target_id: str = Field(min_length=1)


class DiagnosticObservation(ObservationBase):
    record_type: Literal["diagnostic_observation"] = "diagnostic_observation"
    diagnostic_entity_id: str = Field(min_length=1)
    subject_entity_ids: list[str] = Field(min_length=1)


class RuntimeObservation(ObservationBase):
    record_type: Literal["runtime_observation"] = "runtime_observation"
    runtime_entity_id: str = Field(min_length=1)
    subject_entity_ids: list[str] = Field(min_length=1)
    outcome: str = Field(min_length=1)
    metrics: dict[str, str | int | float | bool] = Field(default_factory=dict)


class SimilarityObservation(ObservationBase):
    record_type: Literal["similarity_observation"] = "similarity_observation"
    similarity_entity_id: str = Field(min_length=1)
    member_entity_ids: list[str] = Field(min_length=2)
    score: float | None = None


ObservationRecord = Annotated[
    StructuralObservation | DiagnosticObservation | RuntimeObservation | SimilarityObservation,
    Field(discriminator="record_type"),
]


class CompletenessRecord(EvidenceModel):
    """Provider coverage for one state, scope and evidence-family set."""

    record_type: Literal["completeness"] = "completeness"
    completeness_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    provider_run_id: str = Field(min_length=1)
    scope_type: Literal["repository", "entity", "location", "query"]
    scope_id: str = Field(min_length=1)
    evidence_families: list[str] = Field(min_length=1)
    status: CompletenessStatus
    omission_ids: list[str] = Field(default_factory=list)


class LimitationRecord(EvidenceModel):
    """One stable statement of what a provider or method cannot establish."""

    record_type: Literal["limitation"] = "limitation"
    limitation_id: str = Field(min_length=1)
    provider_run_id: str | None = None
    code: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class OmissionRecord(EvidenceModel):
    """Material evidence absent because of scope, policy, support, or budget."""

    record_type: Literal["omission"] = "omission"
    omission_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    provider_run_id: str | None = None
    reason: str = Field(min_length=1)
    scope_type: str = Field(min_length=1)
    scope_id: str | None = None
    recoverable: bool
    remediation: str | None = None


class ConflictRecord(EvidenceModel):
    """Explicit contradiction retained without overwriting either observation."""

    record_type: Literal["conflict"] = "conflict"
    conflict_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    target_type: Literal["entity", "edge", "contract", "candidate"]
    target_id: str = Field(min_length=1)
    observation_ids: list[str] = Field(min_length=2)
    status: ConflictStatus = ConflictStatus.UNRESOLVED
    summary: str = Field(min_length=1)


class IdentityCandidate(EvidenceModel):
    """One exact candidate retained with provider-specific provenance."""

    entity_id: str = Field(min_length=1)
    provider_run_ids: list[str] = Field(min_length=1)
    reason_codes: list[IdentityReason] = Field(min_length=1)
    exact: bool

    @model_validator(mode="after")
    def validate_candidate_provenance(self) -> IdentityCandidate:
        if len(self.provider_run_ids) != len(set(self.provider_run_ids)):
            raise ValueError("identity candidate provider runs must be unique")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("identity candidate reasons must be unique")
        return self


class AliasRecord(EvidenceModel):
    """Lossless reconciliation of one provider identity across state-scoped entities."""

    record_type: Literal["alias"] = "alias"
    alias_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    scheme: str = Field(min_length=1)
    value: str = Field(min_length=1)
    resolution: IdentityResolutionStatus
    provider_run_ids: list[str] = Field(min_length=1)
    reason_codes: list[IdentityReason] = Field(min_length=1)
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    location_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def reject_local_alias_value(cls, value: str) -> str:
        return validate_portable_identity(value, label="alias value")

    @model_validator(mode="after")
    def validate_resolution(self) -> AliasRecord:
        if len(self.provider_run_ids) != len(set(self.provider_run_ids)):
            raise ValueError("alias provider runs must be unique")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("alias reason codes must be unique")
        candidate_ids = [item.entity_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("alias candidate entities must be unique")
        exact_count = sum(item.exact for item in self.candidates)
        if self.resolution is IdentityResolutionStatus.EXACT:
            if len(self.candidates) != 1 or exact_count != 1:
                raise ValueError("exact alias resolution requires one exact candidate")
        elif self.resolution is IdentityResolutionStatus.UNRESOLVED:
            if self.candidates:
                raise ValueError("unresolved alias resolution cannot declare candidates")
        elif self.resolution is IdentityResolutionStatus.AMBIGUOUS:
            if not self.candidates or exact_count > 1:
                raise ValueError("ambiguous alias resolution requires candidates and at most one exact claim")
        elif len(self.candidates) < 2 or exact_count < 2:
            raise ValueError("conflicting alias resolution requires at least two exact candidates")
        return self


class LineageRecord(EvidenceModel):
    """Cross-state continuity candidate, distinct from state-scoped identity."""

    record_type: Literal["lineage"] = "lineage"
    lineage_id: str = Field(min_length=1)
    predecessor_state_id: str = Field(min_length=1)
    successor_state_id: str = Field(min_length=1)
    predecessor_entity_ids: list[str] = Field(default_factory=list)
    successor_entity_ids: list[str] = Field(default_factory=list)
    kind: LineageKind
    certainty: LineageCertainty
    method: str = Field(min_length=1)
    strength: EvidenceStrength
    reason_codes: list[LineageReason] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    observation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lineage_shape(self) -> LineageRecord:
        if self.predecessor_state_id == self.successor_state_id:
            raise ValueError("lineage must connect two different source states")
        if len(self.predecessor_entity_ids) != len(set(self.predecessor_entity_ids)):
            raise ValueError("lineage predecessor entities must be unique")
        if len(self.successor_entity_ids) != len(set(self.successor_entity_ids)):
            raise ValueError("lineage successor entities must be unique")
        shapes = {
            LineageKind.UNCHANGED: (1, 1),
            LineageKind.MODIFIED: (1, 1),
            LineageKind.MOVED: (1, 1),
            LineageKind.RENAMED: (1, 1),
            LineageKind.SPLIT: (1, 2),
            LineageKind.MERGED: (2, 1),
            LineageKind.DELETED: (1, 0),
            LineageKind.ADDED: (0, 1),
        }
        minimum_before, minimum_after = shapes[self.kind]
        if len(self.predecessor_entity_ids) < minimum_before or len(self.successor_entity_ids) < minimum_after:
            raise ValueError(f"{self.kind.value} lineage has invalid endpoint cardinality")
        if self.kind not in {LineageKind.SPLIT, LineageKind.MERGED}:
            if len(self.predecessor_entity_ids) > minimum_before or len(self.successor_entity_ids) > minimum_after:
                raise ValueError(f"{self.kind.value} lineage has invalid endpoint cardinality")
        if self.certainty is LineageCertainty.EXACT and self.strength is not EvidenceStrength.EXACT:
            raise ValueError("exact lineage requires exact evidence strength")
        if self.certainty is LineageCertainty.CANDIDATE and self.strength is EvidenceStrength.EXACT:
            raise ValueError("candidate lineage cannot claim exact evidence strength")
        return self


class RepositoryEvidence(EvidenceModel):
    """The sole canonical portable repository-evidence aggregate."""

    artifact_type: Literal["anatomize.evidence"] = EVIDENCE_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = EVIDENCE_SCHEMA_VERSION
    producer: EvidenceProducer
    repository_id: str = Field(min_length=1)
    states: list[SourceStateRecord] = Field(min_length=1)
    provider_artifacts: list[ProviderArtifactRecord] = Field(default_factory=list)
    provider_runs: list[ProviderRunRecord] = Field(default_factory=list)
    locations: list[LocationRecord] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    edges: list[EdgeRecord] = Field(default_factory=list)
    contracts: list[ContractRecord] = Field(default_factory=list)
    candidates: list[CandidateRecord] = Field(default_factory=list)
    observations: list[ObservationRecord] = Field(default_factory=list)
    completeness: list[CompletenessRecord] = Field(default_factory=list)
    limitations: list[LimitationRecord] = Field(default_factory=list)
    omissions: list[OmissionRecord] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    aliases: list[AliasRecord] = Field(default_factory=list)
    lineage: list[LineageRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> RepositoryEvidence:
        """Reject duplicate IDs, dangling references and cross-state confusion."""
        collections: list[tuple[str, list[tuple[str, object]]]] = [
            ("state", [(item.state_id, item) for item in self.states]),
            ("provider artifact", [(item.artifact_id, item) for item in self.provider_artifacts]),
            ("provider run", [(item.provider_run_id, item) for item in self.provider_runs]),
            ("location", [(item.location_id, item) for item in self.locations]),
            ("entity", [(item.entity_id, item) for item in self.entities]),
            ("edge", [(item.edge_id, item) for item in self.edges]),
            ("contract", [(item.contract_id, item) for item in self.contracts]),
            ("candidate", [(item.candidate_id, item) for item in self.candidates]),
            ("observation", [(item.observation_id, item) for item in self.observations]),
            ("completeness", [(item.completeness_id, item) for item in self.completeness]),
            ("limitation", [(item.limitation_id, item) for item in self.limitations]),
            ("omission", [(item.omission_id, item) for item in self.omissions]),
            ("conflict", [(item.conflict_id, item) for item in self.conflicts]),
            ("alias", [(item.alias_id, item) for item in self.aliases]),
            ("lineage", [(item.lineage_id, item) for item in self.lineage]),
        ]
        all_ids: set[str] = set()
        for label, records in collections:
            ids = [record_id for record_id, _item in records]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} identifier")
            overlap = all_ids.intersection(ids)
            if overlap:
                raise ValueError(f"record identifiers must be globally unique: {sorted(overlap)}")
            all_ids.update(ids)

        states = {item.state_id: item for item in self.states}
        artifacts = {item.artifact_id for item in self.provider_artifacts}
        runs = {item.provider_run_id for item in self.provider_runs}
        locations = {item.location_id for item in self.locations}
        entities = {item.entity_id for item in self.entities}
        edges = {item.edge_id for item in self.edges}
        contracts = {item.contract_id for item in self.contracts}
        candidates = {item.candidate_id for item in self.candidates}
        observations = {item.observation_id for item in self.observations}
        completeness = {item.completeness_id for item in self.completeness}
        limitations = {item.limitation_id for item in self.limitations}
        omissions = {item.omission_id for item in self.omissions}
        location_states = {item.location_id: item.source_state_id for item in self.locations}
        entity_states = {item.entity_id: item.source_state_id for item in self.entities}
        edge_states = {item.edge_id: item.source_state_id for item in self.edges}
        contract_states = {item.contract_id: item.source_state_id for item in self.contracts}
        candidate_states = {item.candidate_id: item.source_state_id for item in self.candidates}

        for state in self.states:
            if state.repository_id != self.repository_id:
                raise ValueError(f"state {state.state_id} belongs to another repository")
        for run in self.provider_runs:
            _require(run.source_state_id, states, f"provider run {run.provider_run_id} source state")
            _require_all(run.artifact_ids, artifacts, f"provider run {run.provider_run_id} artifacts")
            _require_all(run.limitation_ids, limitations, f"provider run {run.provider_run_id} limitations")
        for location in self.locations:
            _require(location.source_state_id, states, f"location {location.location_id} source state")
            if isinstance(location.coordinate_space, GeneratedCoordinateSpace):
                _require_all(
                    location.coordinate_space.source_location_ids,
                    locations,
                    f"location {location.location_id} generated sources",
                )
            elif isinstance(location.coordinate_space, EmbeddedRegionCoordinateSpace):
                _require(
                    location.coordinate_space.host_location_id,
                    locations,
                    f"location {location.location_id} embedded host",
                )
        _validate_location_projection_graph(self.locations)
        for entity in self.entities:
            _require(entity.source_state_id, states, f"entity {entity.entity_id} source state")
            _require_all(entity.location_ids, locations, f"entity {entity.entity_id} locations")
            _require_all(entity.provider_run_ids, runs, f"entity {entity.entity_id} provider runs")
            _require_same_state(
                entity.location_ids,
                entity.source_state_id,
                location_states,
                f"entity {entity.entity_id} locations",
            )
        file_entities = {item.entity_id: item for item in self.entities if isinstance(item, FileEntity)}
        for location in self.locations:
            if location.file_id is not None:
                _require(location.file_id, file_entities, f"location {location.location_id} file")
                file_entity = file_entities[location.file_id]
                if file_entity.source_state_id != location.source_state_id or file_entity.path != location.path:
                    raise ValueError(f"location {location.location_id} does not match its source-bound file")
                if location.origin is LocationOrigin.GENERATED:
                    if file_entity.content_class is not ContentClass.GENERATED:
                        raise ValueError(f"generated location {location.location_id} requires a generated file")
        for entity in self.entities:
            if isinstance(entity, RangeEntity) and entity.parent_entity_id is not None:
                _require(entity.parent_entity_id, entities, f"range entity {entity.entity_id} parent")
            if isinstance(entity, SimilarityEntity):
                _require_all(entity.member_entity_ids, entities, f"similarity entity {entity.entity_id} members")
        for edge in self.edges:
            _require(edge.source_state_id, states, f"edge {edge.edge_id} source state")
            _require_all(edge.provider_run_ids, runs, f"edge {edge.edge_id} provider runs")
            _require(edge.source_entity_id, entities, f"edge {edge.edge_id} source")
            _require(edge.target_entity_id, entities, f"edge {edge.edge_id} target")
            _require_same_state(
                [edge.source_entity_id, edge.target_entity_id],
                edge.source_state_id,
                entity_states,
                f"edge {edge.edge_id} endpoints",
            )
        for contract in self.contracts:
            _require(contract.source_state_id, states, f"contract {contract.contract_id} source state")
            _require_all(contract.provider_run_ids, runs, f"contract {contract.contract_id} provider runs")
            _require_all(contract.subject_entity_ids, entities, f"contract {contract.contract_id} subjects")
            _require_same_state(
                contract.subject_entity_ids,
                contract.source_state_id,
                entity_states,
                f"contract {contract.contract_id} subjects",
            )
            _require_all(
                contract.declaration_location_ids,
                locations,
                f"contract {contract.contract_id} declaration locations",
            )
        for candidate in self.candidates:
            _require(candidate.source_state_id, states, f"candidate {candidate.candidate_id} source state")
            _require_all(candidate.provider_run_ids, runs, f"candidate {candidate.candidate_id} provider runs")
            _require_all(candidate.member_entity_ids, entities, f"candidate {candidate.candidate_id} members")
            _require_same_state(
                candidate.member_entity_ids,
                candidate.source_state_id,
                entity_states,
                f"candidate {candidate.candidate_id} members",
            )
            _require_all(candidate.observation_ids, observations, f"candidate {candidate.candidate_id} observations")
        for observation in self.observations:
            _require(observation.source_state_id, states, f"observation {observation.observation_id} source state")
            _require(observation.provider_run_id, runs, f"observation {observation.observation_id} provider run")
            _require_all(observation.location_ids, locations, f"observation {observation.observation_id} locations")
            _require_same_state(
                observation.location_ids,
                observation.source_state_id,
                location_states,
                f"observation {observation.observation_id} locations",
            )
            _require_all(
                observation.limitation_ids,
                limitations,
                f"observation {observation.observation_id} limitations",
            )
            if observation.completeness_id is not None:
                _require(
                    observation.completeness_id,
                    completeness,
                    f"observation {observation.observation_id} completeness",
                )
            if isinstance(observation, StructuralObservation):
                targets = {"entity": entities, "edge": edges, "contract": contracts}[observation.target_type]
                target_states = {
                    "entity": entity_states,
                    "edge": edge_states,
                    "contract": contract_states,
                }[observation.target_type]
                _require(observation.target_id, targets, f"observation {observation.observation_id} target")
                _require_same_state(
                    [observation.target_id],
                    observation.source_state_id,
                    target_states,
                    f"observation {observation.observation_id} target",
                )
            elif isinstance(observation, DiagnosticObservation):
                _require(observation.diagnostic_entity_id, entities, "diagnostic observation target")
                _require_all(observation.subject_entity_ids, entities, "diagnostic observation subjects")
                _require_same_state(
                    [observation.diagnostic_entity_id, *observation.subject_entity_ids],
                    observation.source_state_id,
                    entity_states,
                    "diagnostic observation entities",
                )
            elif isinstance(observation, RuntimeObservation):
                _require(observation.runtime_entity_id, entities, "runtime observation target")
                _require_all(observation.subject_entity_ids, entities, "runtime observation subjects")
                _require_same_state(
                    [observation.runtime_entity_id, *observation.subject_entity_ids],
                    observation.source_state_id,
                    entity_states,
                    "runtime observation entities",
                )
            elif isinstance(observation, SimilarityObservation):
                _require(observation.similarity_entity_id, entities, "similarity observation target")
                _require_all(observation.member_entity_ids, entities, "similarity observation members")
                _require_same_state(
                    [observation.similarity_entity_id, *observation.member_entity_ids],
                    observation.source_state_id,
                    entity_states,
                    "similarity observation entities",
                )
        for record in self.completeness:
            _require(record.source_state_id, states, f"completeness {record.completeness_id} source state")
            _require(record.provider_run_id, runs, f"completeness {record.completeness_id} provider run")
            _require_all(record.omission_ids, omissions, f"completeness {record.completeness_id} omissions")
        for limitation in self.limitations:
            if limitation.provider_run_id is not None:
                _require(limitation.provider_run_id, runs, f"limitation {limitation.limitation_id} provider run")
        for omission in self.omissions:
            _require(omission.source_state_id, states, f"omission {omission.omission_id} source state")
            if omission.provider_run_id is not None:
                _require(omission.provider_run_id, runs, f"omission {omission.omission_id} provider run")
        for conflict in self.conflicts:
            _require(conflict.source_state_id, states, f"conflict {conflict.conflict_id} source state")
            conflict_targets = {
                "entity": entities,
                "edge": edges,
                "contract": contracts,
                "candidate": candidates,
            }[conflict.target_type]
            conflict_target_states = {
                "entity": entity_states,
                "edge": edge_states,
                "contract": contract_states,
                "candidate": candidate_states,
            }[conflict.target_type]
            _require(conflict.target_id, conflict_targets, f"conflict {conflict.conflict_id} target")
            _require_same_state(
                [conflict.target_id],
                conflict.source_state_id,
                conflict_target_states,
                f"conflict {conflict.conflict_id} target",
            )
            _require_all(conflict.observation_ids, observations, f"conflict {conflict.conflict_id} observations")
        for alias in self.aliases:
            _require(alias.source_state_id, states, f"alias {alias.alias_id} source state")
            _require_all(alias.location_ids, locations, f"alias {alias.alias_id} locations")
            _require_same_state(
                alias.location_ids,
                alias.source_state_id,
                location_states,
                f"alias {alias.alias_id} locations",
            )
            _require_all(alias.provider_run_ids, runs, f"alias {alias.alias_id} providers")
            for alias_candidate in alias.candidates:
                _require(alias_candidate.entity_id, entities, f"alias {alias.alias_id} candidate")
                _require_same_state(
                    [alias_candidate.entity_id],
                    alias.source_state_id,
                    entity_states,
                    f"alias {alias.alias_id} candidate",
                )
                _require_all(
                    alias_candidate.provider_run_ids,
                    runs,
                    f"alias {alias.alias_id} candidate providers",
                )
        for lineage in self.lineage:
            _require(lineage.predecessor_state_id, states, f"lineage {lineage.lineage_id} predecessor state")
            _require(lineage.successor_state_id, states, f"lineage {lineage.lineage_id} successor state")
            _require_all(lineage.predecessor_entity_ids, entities, f"lineage {lineage.lineage_id} predecessors")
            _require_all(lineage.successor_entity_ids, entities, f"lineage {lineage.lineage_id} successors")
            _require_all(lineage.observation_ids, observations, f"lineage {lineage.lineage_id} observations")
            predecessor_states = {
                _entity_state(entity_id, self.entities) for entity_id in lineage.predecessor_entity_ids
            }
            successor_states = {_entity_state(entity_id, self.entities) for entity_id in lineage.successor_entity_ids}
            if predecessor_states.difference({lineage.predecessor_state_id}):
                raise ValueError(f"lineage {lineage.lineage_id} predecessors belong to another state")
            if successor_states.difference({lineage.successor_state_id}):
                raise ValueError(f"lineage {lineage.lineage_id} successors belong to another state")

        repository_entities = [
            entity
            for entity in self.entities
            if isinstance(entity, RepositoryEntity) and entity.repository_id == self.repository_id
        ]
        repository_states = {entity.source_state_id for entity in repository_entities}
        missing_repository_states = sorted(set(states).difference(repository_states))
        if missing_repository_states:
            raise ValueError(
                f"repository_id must have a repository entity in every source state: {missing_repository_states}"
            )
        return self


def validate_repository_path(value: str) -> str:
    """Return one normalized repository-relative POSIX path or fail closed."""
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("repository paths must be non-empty portable POSIX paths")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("repository paths must use Unicode NFC normalization")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {".", ".."} or path.as_posix() != value:
        raise ValueError(f"repository path must be normalized and relative (repository-relative): {value}")
    return value


def validate_portable_identity(value: str, *, label: str = "identity") -> str:
    """Reject local absolute paths while retaining opaque external identities."""
    lowered = value.casefold()
    looks_windows_absolute = len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}
    if (
        value.startswith("/")
        or value.startswith("\\\\")
        or lowered.startswith("file:/")
        or looks_windows_absolute
    ):
        raise ValueError(f"{label} must not contain a checkout-specific absolute path")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use Unicode NFC normalization")
    return value


def _require(value: str, allowed: AbstractSet[str] | Mapping[str, object], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label} references unknown identifier: {value}")


def _require_all(values: list[str], allowed: AbstractSet[str] | Mapping[str, object], label: str) -> None:
    unknown = sorted(set(values).difference(allowed))
    if unknown:
        raise ValueError(f"{label} reference unknown identifiers: {unknown}")


def _require_same_state(
    values: list[str],
    expected_state_id: str,
    states_by_id: Mapping[str, str],
    label: str,
) -> None:
    mismatched = sorted(value for value in values if states_by_id.get(value) != expected_state_id)
    if mismatched:
        raise ValueError(f"{label} crosses source states: {mismatched}")


def _entity_state(entity_id: str, entities: list[EntityRecord]) -> str:
    for entity in entities:
        if entity.entity_id == entity_id:
            return entity.source_state_id
    raise ValueError(f"unknown lineage entity: {entity_id}")


def _validate_location_projection_graph(locations: list[LocationRecord]) -> None:
    dependencies: dict[str, list[str]] = {}
    by_id = {location.location_id: location for location in locations}
    for location in locations:
        coordinate_space = location.coordinate_space
        if isinstance(coordinate_space, GeneratedCoordinateSpace):
            dependencies[location.location_id] = coordinate_space.source_location_ids
        elif isinstance(coordinate_space, EmbeddedRegionCoordinateSpace):
            dependencies[location.location_id] = [coordinate_space.host_location_id]
        else:
            dependencies[location.location_id] = []
        mismatched = sorted(
            dependency_id
            for dependency_id in dependencies[location.location_id]
            if by_id[dependency_id].source_state_id != location.source_state_id
        )
        if mismatched:
            raise ValueError(f"location {location.location_id} projection crosses source states: {mismatched}")
        if isinstance(coordinate_space, EmbeddedRegionCoordinateSpace):
            host = by_id[coordinate_space.host_location_id]
            if host.file_id != location.file_id or host.path != location.path:
                raise ValueError(f"location {location.location_id} embedded host belongs to another file")

    visited: set[str] = set()
    active: set[str] = set()

    def visit(location_id: str) -> None:
        if location_id in active:
            raise ValueError(f"location projection cycle contains {location_id}")
        if location_id in visited:
            return
        active.add(location_id)
        for dependency_id in dependencies[location_id]:
            visit(dependency_id)
        active.remove(location_id)
        visited.add(location_id)

    for location_id in dependencies:
        visit(location_id)
