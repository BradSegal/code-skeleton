"""Public, deterministic dossier request and response contracts."""

from __future__ import annotations

import base64
import json
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from anatomize._artifacts import canonical_ordered_json_bytes, require_unique, sha256_digest
from anatomize.evidence import ContentClass, EvidenceModel, EvidenceStrength, validate_repository_path

DOSSIER_REQUEST_ARTIFACT_TYPE: Literal["anatomize.dossier-request"] = "anatomize.dossier-request"
DOSSIER_REQUEST_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
DOSSIER_ARTIFACT_TYPE: Literal["anatomize.dossier"] = "anatomize.dossier"
DOSSIER_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
DOSSIER_CURSOR_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class DossierProfile(str, Enum):
    """Lifecycle question whose proof boundary controls dossier selection."""

    ORIENTATION = "orientation"
    DESIGN = "design"
    AUDIT = "audit"
    LOCALISATION = "localisation"
    IMPLEMENTATION = "implementation"
    CHANGE_REVIEW = "change_review"
    CLOSURE = "closure"


class TargetKind(str, Enum):
    """Portable target families accepted by the query application layer."""

    REPOSITORY = "repository"
    FILE = "file"
    SYMBOL = "symbol"
    RANGE = "range"
    DOCUMENTATION_SECTION = "documentation_section"
    TEST = "test"
    CONFIGURATION = "configuration"
    DATA = "data"
    WORKFLOW = "workflow"
    ARTIFACT = "artifact"
    DIAGNOSTIC = "diagnostic"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    REVISION = "revision"
    EXTERNAL_DEPENDENCY = "external_dependency"


class QueryDirection(str, Enum):
    """Direction in which typed evidence relationships are traversed."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class DossierStatus(str, Enum):
    """Truthful terminal state of one dossier projection."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class TargetResolutionStatus(str, Enum):
    """Result of resolving a target without choosing an ambiguous candidate."""

    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"


class DossierSection(str, Enum):
    """Stable reader order below the separately represented question boundary."""

    DECISION_CRITICAL = "decision_critical"
    CONTRADICTIONS_UNKNOWNS = "contradictions_unknowns"
    SUPPORTING_CONTEXT = "supporting_context"


class EvidenceRole(str, Enum):
    """Task-facing purpose of evidence without a quality judgement."""

    STATE = "state"
    TOPOLOGY = "topology"
    OWNERSHIP = "ownership"
    PUBLIC_SURFACE = "public_surface"
    ENTRY_POINT = "entry_point"
    FLOW = "flow"
    DEFINITION = "definition"
    DECLARATION = "declaration"
    DEPENDENCY = "dependency"
    CALLER = "caller"
    CALLEE = "callee"
    REFERENCE = "reference"
    CONSUMER = "consumer"
    CONTRACT = "contract"
    ALTERNATIVE = "alternative"
    DECISION_CONTEXT = "decision_context"
    DIAGNOSTIC = "diagnostic"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    DATA = "data"
    WORKFLOW = "workflow"
    RUNTIME = "runtime"
    ARTIFACT = "artifact"
    CHANGE = "change"
    VALIDATION = "validation"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    PROVENANCE = "provenance"
    SUPPORTING_CONTEXT = "supporting_context"


class SelectionReasonCode(str, Enum):
    """Public, compositional selection rules; deliberately not scores."""

    PROFILE_REQUIRED = "profile_required"
    EXPLICIT_INCLUDE = "explicit_include"
    EXACT_TARGET = "exact_target"
    EXACT_RELATIONSHIP = "exact_relationship"
    CURRENT_CHANGE = "current_change"
    ROLE_MATCH = "role_match"
    STRUCTURAL_FALLBACK = "structural_fallback"
    CONSERVATIVE_CANDIDATE = "conservative_candidate"
    CONFLICT_SURFACE = "conflict_surface"
    SUPPORTING_CONTEXT = "supporting_context"


class OmissionReason(str, Enum):
    """Why evidence is absent from this projection."""

    SCOPE = "scope"
    UNSUPPORTED = "unsupported"
    POLICY = "policy"
    BUDGET = "budget"
    PROVIDER_LIMITATION = "provider_limitation"
    AMBIGUITY = "ambiguity"
    SOURCE_DRIFT = "source_drift"
    FAILURE = "failure"


class ExpansionKind(str, Enum):
    """Closed expansion algebra shared by every interface."""

    GROUP = "group"
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    ROLE = "role"
    DEPTH = "depth"
    ADJACENT_CONTEXT = "adjacent_context"
    COMPLETE_FILE = "complete_file"
    PROVIDER = "provider"
    OMISSION = "omission"
    CURSOR = "cursor"
    HISTORY = "history"
    VALIDATION = "validation"
    REFRESH = "refresh"


class DossierLocator(EvidenceModel):
    """Structured portable target or evidence locator."""

    path: str | None = None
    name: str | None = None
    qualified_name: str | None = None
    heading: str | None = None
    rule_id: str | None = None
    revision: str | None = None
    external_identity: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    start_column: int | None = Field(default=None, ge=0)
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_nonempty(self) -> DossierLocator:
        if all(getattr(self, name) is None for name in type(self).model_fields):
            raise ValueError("dossier locator must contain at least one field")
        if self.path is not None:
            validate_repository_path(self.path)
        start = (self.start_line, self.start_column or 0) if self.start_line is not None else None
        end = (self.end_line, self.end_column or 0) if self.end_line is not None else None
        if start is not None and end is not None and end < start:
            raise ValueError("dossier locator end must not precede start")
        return self


class TargetSelector(EvidenceModel):
    """One exact identity or structured locator requested by a consumer."""

    kind: TargetKind
    identity: str | None = None
    locator: DossierLocator | None = None
    source_state_id: str | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> TargetSelector:
        if (self.identity is None) == (self.locator is None):
            raise ValueError("target selector requires exactly one identity or locator")
        if self.kind is TargetKind.RANGE:
            if self.locator is None or self.locator.path is None or self.locator.start_line is None:
                raise ValueError("range target requires a path and start line")
        return self


class DossierFilters(EvidenceModel):
    """Conjunctive restrictions applied without changing source facts."""

    evidence_kinds: list[str] = Field(default_factory=list)
    roles: list[EvidenceRole] = Field(default_factory=list)
    provider_ids: list[str] = Field(default_factory=list)
    relationship_categories: list[str] = Field(default_factory=list)
    content_classes: list[ContentClass] = Field(default_factory=list)
    source_state_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique(self) -> DossierFilters:
        _require_unique_lists(self)
        return self


class DossierBudget(EvidenceModel):
    """Explicit delivery bounds; no bound redefines proof completeness."""

    max_items: int = Field(default=32, ge=0)
    max_payload_bytes: int = Field(default=73_728, ge=4_096)
    max_inline_content_bytes: int = Field(default=24_576, ge=0)
    max_content_block_bytes: int = Field(default=8_192, ge=0)
    max_depth: int = Field(default=2, ge=0, le=32)
    max_disclosed_omission_ids: int = Field(default=8, ge=0, le=1_000)


class SourceSlicePolicy(EvidenceModel):
    """Content inclusion policy kept independent from evidence selection."""

    context_before: int = Field(default=3, ge=0, le=1_000)
    context_after: int = Field(default=3, ge=0, le=1_000)
    include_enclosing_structure: bool = True
    inline_content: bool = True
    allowed_content_classes: list[ContentClass] = Field(default_factory=lambda: [ContentClass.ORDINARY])

    @model_validator(mode="after")
    def validate_unique(self) -> SourceSlicePolicy:
        if len(self.allowed_content_classes) != len(set(self.allowed_content_classes)):
            raise ValueError("allowed content classes must be unique")
        return self


class DossierCursor(EvidenceModel):
    """Self-validating stable continuation bound to one query and source session."""

    schema_version: Literal["1.0.0"] = DOSSIER_CURSOR_SCHEMA_VERSION
    base_dossier_id: str = Field(min_length=1)
    base_dossier_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_request_id: str = Field(min_length=1)
    query_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_id: str = Field(min_length=1)
    session_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expansion_kind: ExpansionKind
    ordered_after: list[str] = Field(min_length=1)
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_checksum(self) -> DossierCursor:
        if self.checksum != sha256_digest(_ordered_compact_bytes(_without(self, "checksum"))):
            raise ValueError("dossier cursor checksum does not match its content")
        return self

    def token(self) -> str:
        """Encode the typed cursor for terminal and transport use."""
        raw = _ordered_compact_bytes(self.model_dump(mode="json"))
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class DossierExpansion(EvidenceModel):
    """One consumer-requested expansion over an immutable base dossier."""

    kind: ExpansionKind
    base_dossier_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    target_id: str | None = None
    role: EvidenceRole | None = None
    depth_increment: int = Field(default=0, ge=0, le=32)
    context_lines: int = Field(default=0, ge=0, le=10_000)
    cursor: DossierCursor | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> DossierExpansion:
        if self.kind is ExpansionKind.CURSOR and self.cursor is None:
            raise ValueError("cursor expansion requires a cursor")
        if self.cursor is not None and self.kind is not ExpansionKind.CURSOR:
            raise ValueError("only cursor expansion may contain a cursor")
        return self


class DossierRequest(EvidenceModel):
    """Normalized, content-addressed dossier query."""

    artifact_type: Literal["anatomize.dossier-request"] = DOSSIER_REQUEST_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = DOSSIER_REQUEST_SCHEMA_VERSION
    request_id: str = Field(min_length=1)
    profile: DossierProfile
    question: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    session_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    targets: list[TargetSelector] = Field(default_factory=list)
    direction: QueryDirection = QueryDirection.BOTH
    filters: DossierFilters = Field(default_factory=DossierFilters)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    budget: DossierBudget = Field(default_factory=DossierBudget)
    slice_policy: SourceSlicePolicy = Field(default_factory=SourceSlicePolicy)
    expansion: DossierExpansion | None = None

    @model_validator(mode="after")
    def validate_request(self) -> DossierRequest:
        if not self.targets and self.profile is not DossierProfile.ORIENTATION:
            raise ValueError("only orientation may omit explicit targets")
        require_unique(self.include, "included identities")
        require_unique(self.exclude, "excluded identities")
        overlap = sorted(set(self.include).intersection(self.exclude))
        if overlap:
            raise ValueError(f"identities cannot be both included and excluded: {overlap}")
        target_keys = [_ordered_json(item.model_dump(mode="json")) for item in self.targets]
        require_unique(target_keys, "target selectors")
        if self.request_id != dossier_request_id(self):
            raise ValueError("dossier request identifier does not match its content")
        return self


class ResolvedTarget(EvidenceModel):
    """Resolution outcome preserved for each requested selector."""

    selector: TargetSelector
    status: TargetResolutionStatus
    resolved_ids: list[str] = Field(default_factory=list)
    message: str


class DossierBoundary(EvidenceModel):
    """Question, authority, and task-specific proof boundary."""

    profile: DossierProfile
    question: str
    targets: list[ResolvedTarget]
    authority: str
    satisfied_stop_conditions: list[str] = Field(default_factory=list)
    unsatisfied_stop_conditions: list[str] = Field(default_factory=list)


class SelectionReason(EvidenceModel):
    """One independent and inspectable reason an item was selected."""

    code: SelectionReasonCode
    subject_id: str
    object_id: str | None = None
    relationship_id: str | None = None
    message: str


class EvidenceLocator(EvidenceModel):
    """Portable evidence location retained in a dossier item."""

    location_id: str | None = None
    path: str | None = None
    external_locator: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    start_column: int | None = Field(default=None, ge=0)
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=0)


class DossierObservation(EvidenceModel):
    """One independent provider observation retained without aggregation."""

    observation_id: str
    provider_run_id: str
    method: str
    method_version: str
    strength: EvidenceStrength
    stance: str
    limitation_ids: list[str] = Field(default_factory=list)
    rationale: str


class DossierItem(EvidenceModel):
    """One role-labelled evidence reference with every independent reason."""

    item_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    record_kind: str = Field(min_length=1)
    entity_id: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    observations: list[DossierObservation] = Field(default_factory=list)
    role: EvidenceRole
    section: DossierSection
    evidence_kind: str = Field(min_length=1)
    strength: EvidenceStrength
    locators: list[EvidenceLocator] = Field(default_factory=list)
    provider_run_ids: list[str] = Field(default_factory=list)
    selection_reasons: list[SelectionReason] = Field(min_length=1)
    conflict_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    content_id: str | None = None
    required: bool
    distance: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_item(self) -> DossierItem:
        _require_unique_lists(self, excluded={"selection_reasons", "locators", "observations"})
        require_unique([item.observation_id for item in self.observations], "item observations")
        if self.item_id != dossier_item_id(self):
            raise ValueError("dossier item identifier does not match its content")
        return self


class DossierGroup(EvidenceModel):
    """Evidence items grouped by their purpose in reader order."""

    group_id: str = Field(min_length=1)
    section: DossierSection
    role: EvidenceRole
    proof_purpose: str
    required: bool
    item_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_group(self) -> DossierGroup:
        require_unique(self.item_ids, "group item identities")
        if self.group_id != dossier_group_id(self):
            raise ValueError("dossier group identifier does not match its content")
        return self


class ContentRange(EvidenceModel):
    """Half-open content range used by a dossier slice."""

    start_line: int = Field(ge=1)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)


class DossierContent(EvidenceModel):
    """Deduplicated exact content transmitted separately from semantic roles."""

    content_id: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    full_content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_state_id: str
    path: str | None = None
    external_locator: str | None = None
    content_class: ContentClass
    media_type: str
    language: str | None = None
    source_range: ContentRange | None = None
    enclosing_entity_id: str | None = None
    context_before: int = Field(ge=0)
    context_after: int = Field(ge=0)
    text: str | None = None
    truncated: bool

    @model_validator(mode="after")
    def validate_content(self) -> DossierContent:
        if (self.path is None) == (self.external_locator is None):
            raise ValueError("dossier content requires exactly one path or external locator")
        if self.content_id != dossier_content_id(self):
            raise ValueError("dossier content identifier does not match its content")
        return self


class DossierOmission(EvidenceModel):
    """Typed absence with requiredness and deterministic recovery."""

    omission_id: str = Field(min_length=1)
    reason: OmissionReason
    role: EvidenceRole | None = None
    required: bool
    total_count: int = Field(ge=0)
    disclosed_ids: list[str] = Field(default_factory=list)
    message: str
    recovery_action_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_omission(self) -> DossierOmission:
        require_unique(self.disclosed_ids, "disclosed omission identities")
        require_unique(self.recovery_action_ids, "omission recovery actions")
        if len(self.disclosed_ids) > self.total_count:
            raise ValueError("disclosed omissions cannot exceed total count")
        if self.omission_id != dossier_omission_id(self):
            raise ValueError("dossier omission identifier does not match its content")
        return self


class ExpansionAction(EvidenceModel):
    """One safe typed action that can derive a new request."""

    action_id: str = Field(min_length=1)
    kind: ExpansionKind
    target_id: str | None = None
    role: EvidenceRole | None = None
    base_dossier_id: str
    session_id: str
    session_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    maximum_incremental_bytes: int | None = Field(default=None, ge=0)
    cursor: DossierCursor | None = None

    @model_validator(mode="after")
    def validate_action(self) -> ExpansionAction:
        if self.kind is ExpansionKind.CURSOR and self.cursor is None:
            raise ValueError("cursor action requires a cursor")
        if self.action_id != expansion_action_id(self):
            raise ValueError("expansion action identifier does not match its content")
        return self


class BudgetCounter(EvidenceModel):
    """One exact declared limit and its deterministic use."""

    limit: int = Field(ge=0)
    used: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_use(self) -> BudgetCounter:
        if self.used > self.limit:
            raise ValueError("budget use cannot exceed its limit")
        return self


class DossierBudgetUse(EvidenceModel):
    """Disaggregated result cost; payload bytes are measured canonical bytes."""

    items: BudgetCounter
    payload_bytes: BudgetCounter
    inline_content_bytes: BudgetCounter
    depth: BudgetCounter


class Dossier(EvidenceModel):
    """Source-bound progressively expandable evidence dossier."""

    artifact_type: Literal["anatomize.dossier"] = DOSSIER_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = DOSSIER_SCHEMA_VERSION
    dossier_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    base_dossier_id: str | None = None
    session_id: str = Field(min_length=1)
    session_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository_id: str = Field(min_length=1)
    source_state_ids: list[str] = Field(min_length=1, max_length=2)
    provider_run_ids: list[str] = Field(default_factory=list)
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: DossierStatus
    boundary: DossierBoundary
    groups: list[DossierGroup] = Field(default_factory=list)
    items: list[DossierItem] = Field(default_factory=list)
    content: list[DossierContent] = Field(default_factory=list)
    omissions: list[DossierOmission] = Field(default_factory=list)
    expansions: list[ExpansionAction] = Field(default_factory=list)
    budget_use: DossierBudgetUse
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dossier(self) -> Dossier:
        _require_unique_lists(
            self,
            excluded={"groups", "items", "content", "omissions", "expansions", "limitations"},
        )
        item_ids = [item.item_id for item in self.items]
        content_ids = [item.content_id for item in self.content]
        action_ids = [item.action_id for item in self.expansions]
        require_unique(item_ids, "dossier items")
        require_unique([item.group_id for item in self.groups], "dossier groups")
        require_unique(content_ids, "dossier content")
        require_unique([item.omission_id for item in self.omissions], "dossier omissions")
        require_unique(action_ids, "dossier actions")
        for group in self.groups:
            unknown = sorted(set(group.item_ids).difference(item_ids))
            if unknown:
                raise ValueError(f"dossier group references unknown items: {unknown}")
        for item in self.items:
            if item.content_id is not None and item.content_id not in content_ids:
                raise ValueError(f"dossier item references unknown content: {item.content_id}")
        for omission in self.omissions:
            unknown = sorted(set(omission.recovery_action_ids).difference(action_ids))
            if unknown:
                raise ValueError(f"dossier omission references unknown actions: {unknown}")
        if self.status is DossierStatus.COMPLETE:
            if self.boundary.unsatisfied_stop_conditions or any(item.required for item in self.omissions):
                raise ValueError("complete dossier cannot have unsatisfied conditions or required omissions")
        if self.dossier_id != dossier_id(self):
            raise ValueError("dossier identifier does not match its content")
        return self


def build_dossier_request(
    *,
    profile: DossierProfile,
    question: str,
    session_id: str,
    session_manifest_digest: str,
    targets: list[TargetSelector] | None = None,
    direction: QueryDirection = QueryDirection.BOTH,
    filters: DossierFilters | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    budget: DossierBudget | None = None,
    slice_policy: SourceSlicePolicy | None = None,
    expansion: DossierExpansion | None = None,
) -> DossierRequest:
    """Build a normalized request with one ordered content identity."""
    values: dict[str, Any] = {
        "artifact_type": DOSSIER_REQUEST_ARTIFACT_TYPE,
        "schema_version": DOSSIER_REQUEST_SCHEMA_VERSION,
        "profile": profile,
        "question": question,
        "session_id": session_id,
        "session_manifest_digest": session_manifest_digest,
        "targets": targets or [],
        "direction": direction,
        "filters": filters or DossierFilters(),
        "include": sorted(include or []),
        "exclude": sorted(exclude or []),
        "budget": budget or DossierBudget(),
        "slice_policy": slice_policy or SourceSlicePolicy(),
        "expansion": expansion,
    }
    payload = _json_values(values)
    return DossierRequest(request_id=_ordered_content_id("dossier-request", payload), **values)


def expand_dossier_request(
    base_request: DossierRequest,
    base_dossier: Dossier,
    action: ExpansionAction,
    *,
    budget: DossierBudget | None = None,
    slice_policy: SourceSlicePolicy | None = None,
    depth_increment: int = 1,
    context_lines: int = 3,
) -> DossierRequest:
    """Derive one immutable request from an action exposed by a base dossier."""
    if base_dossier.request_id != base_request.request_id:
        raise ValueError("base dossier does not answer the supplied request")
    if action not in base_dossier.expansions:
        raise ValueError("expansion action was not issued by the supplied base dossier")
    if action.base_dossier_id != base_dossier.dossier_id:
        raise ValueError("expansion action belongs to another dossier")
    if (action.session_id, action.session_manifest_digest) != (
        base_request.session_id,
        base_request.session_manifest_digest,
    ):
        raise ValueError("expansion action belongs to another source session")
    expansion = DossierExpansion(
        kind=action.kind,
        base_dossier_id=base_dossier.dossier_id,
        action_id=action.action_id,
        target_id=action.target_id,
        role=action.role,
        depth_increment=depth_increment if action.kind is ExpansionKind.DEPTH else 0,
        context_lines=context_lines if action.kind is ExpansionKind.ADJACENT_CONTEXT else 0,
        cursor=action.cursor,
    )
    return build_dossier_request(
        profile=base_request.profile,
        question=base_request.question,
        session_id=base_request.session_id,
        session_manifest_digest=base_request.session_manifest_digest,
        targets=base_request.targets,
        direction=base_request.direction,
        filters=base_request.filters,
        include=base_request.include,
        exclude=base_request.exclude,
        budget=budget or base_request.budget,
        slice_policy=slice_policy or base_request.slice_policy,
        expansion=expansion,
    )


def build_dossier_cursor(**values: Any) -> DossierCursor:
    """Build a checksum-bound cursor from fields other than checksum."""
    payload = {"schema_version": DOSSIER_CURSOR_SCHEMA_VERSION, **_json_values(values)}
    return DossierCursor(checksum=sha256_digest(_ordered_compact_bytes(payload)), **values)


def build_dossier_item(**values: Any) -> DossierItem:
    """Build one content-addressed dossier item."""
    payload = _json_values(values)
    return DossierItem(item_id=_ordered_content_id("dossier-item", payload), **values)


def build_dossier_group(**values: Any) -> DossierGroup:
    """Build one content-addressed ordered evidence group."""
    payload = _json_values(values)
    return DossierGroup(group_id=_ordered_content_id("dossier-group", payload), **values)


def build_dossier_content(**values: Any) -> DossierContent:
    """Build one content-addressed source or document slice."""
    payload = _json_values(values)
    return DossierContent(content_id=_ordered_content_id("dossier-content", payload), **values)


def build_dossier_omission(**values: Any) -> DossierOmission:
    """Build one content-addressed explicit omission."""
    payload = _json_values(values)
    return DossierOmission(omission_id=_ordered_content_id("dossier-omission", payload), **values)


def build_expansion_action(**values: Any) -> ExpansionAction:
    """Build one content-addressed safe expansion action."""
    return ExpansionAction(action_id=expansion_action_core_id(**values), **values)


def expansion_action_core_id(**values: Any) -> str:
    """Identify recovery semantics before the base dossier and cursor exist."""
    payload = _json_values(values)
    payload.pop("base_dossier_id", None)
    payload.pop("cursor", None)
    return _ordered_content_id("dossier-action", payload)


def dossier_core_id(**values: Any) -> str:
    """Compute the dossier identity before self-referencing actions are built."""
    return _ordered_content_id("dossier", _json_values(values))


def decode_dossier_cursor(token: str, *, max_bytes: int = 16_384) -> DossierCursor:
    """Decode one bounded base64url cursor and validate every typed binding."""
    if len(token) > max_bytes * 2:
        raise ValueError("dossier cursor token exceeds the explicit bound")
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode((token + padding).encode("ascii"), altchars=b"-_", validate=True)
        if len(raw) > max_bytes:
            raise ValueError("dossier cursor payload exceeds the explicit bound")
        payload = json.loads(raw)
    except (UnicodeEncodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("dossier cursor is not valid bounded base64url JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("dossier cursor root must be an object")
    return DossierCursor.model_validate(payload)


def dossier_request_id(request: DossierRequest) -> str:
    return _ordered_content_id("dossier-request", _without(request, "request_id"))


def dossier_query_digest(request: DossierRequest) -> str:
    """Bind selection semantics while permitting an explicit expansion budget."""
    values = request.model_dump(mode="json", exclude={"request_id", "budget", "expansion"})
    return sha256_digest(_ordered_compact_bytes(values))


def dossier_item_id(item: DossierItem) -> str:
    return _ordered_content_id("dossier-item", _without(item, "item_id"))


def dossier_group_id(group: DossierGroup) -> str:
    return _ordered_content_id("dossier-group", _without(group, "group_id"))


def dossier_content_id(content: DossierContent) -> str:
    return _ordered_content_id("dossier-content", _without(content, "content_id"))


def dossier_omission_id(omission: DossierOmission) -> str:
    return _ordered_content_id("dossier-omission", _without(omission, "omission_id"))


def expansion_action_id(action: ExpansionAction) -> str:
    values = _without(action, "action_id")
    values.pop("base_dossier_id", None)
    values.pop("cursor", None)
    return _ordered_content_id("dossier-action", values)


def dossier_id(dossier: Dossier) -> str:
    fields = {
        "request_id",
        "base_dossier_id",
        "session_id",
        "session_manifest_digest",
        "repository_id",
        "source_state_ids",
        "provider_run_ids",
        "policy_digest",
        "status",
        "boundary",
        "groups",
        "items",
        "content",
        "omissions",
        "limitations",
    }
    values = dossier.model_dump(mode="json", include=fields)
    return _ordered_content_id("dossier", values)


def canonical_dossier_bytes(dossier: Dossier) -> bytes:
    """Serialize a dossier while preserving its semantically ordered arrays."""
    return canonical_ordered_json_bytes(dossier.model_dump(mode="json"))


def _ordered_content_id(prefix: str, value: Any) -> str:
    return f"{prefix}:{sha256_digest(_ordered_compact_bytes(value))}"


def _ordered_compact_bytes(value: Any) -> bytes:
    return _ordered_json(value).encode("utf-8")


def _ordered_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _without(model: EvidenceModel, field: str) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude={field})


def _json_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.model_dump(mode="json")
        if isinstance(value, EvidenceModel)
        else (
            value.value
            if isinstance(value, Enum)
            else [
                item.model_dump(mode="json")
                if isinstance(item, EvidenceModel)
                else (item.value if isinstance(item, Enum) else item)
                for item in value
            ]
            if isinstance(value, list)
            else value
        )
        for key, value in values.items()
    }


def _require_unique_lists(model: EvidenceModel, *, excluded: set[str] | None = None) -> None:
    for name in type(model).model_fields:
        if name in (excluded or set()):
            continue
        value = getattr(model, name)
        if isinstance(value, list) and all(isinstance(item, (str, Enum)) for item in value):
            rendered = [item.value if isinstance(item, Enum) else item for item in value]
            require_unique(rendered, name.replace("_", " "))
