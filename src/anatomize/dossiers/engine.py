"""Deterministic dossier resolution, traversal, grouping, budgets, and cursors."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from anatomize._artifacts import sha256_digest
from anatomize._errors import AnatomizeError
from anatomize.dossiers.context import DossierContext
from anatomize.dossiers.models import (
    BudgetCounter,
    Dossier,
    DossierBoundary,
    DossierBudget,
    DossierBudgetUse,
    DossierContent,
    DossierCursor,
    DossierExpansion,
    DossierItem,
    DossierObservation,
    DossierOmission,
    DossierProfile,
    DossierRequest,
    DossierSection,
    DossierStatus,
    EvidenceLocator,
    EvidenceRole,
    ExpansionAction,
    ExpansionKind,
    OmissionReason,
    QueryDirection,
    ResolvedTarget,
    SelectionReason,
    SelectionReasonCode,
    SourceSlicePolicy,
    TargetKind,
    TargetResolutionStatus,
    build_dossier_content,
    build_dossier_cursor,
    build_dossier_group,
    build_dossier_item,
    build_dossier_omission,
    build_dossier_request,
    build_expansion_action,
    canonical_dossier_bytes,
    dossier_core_id,
    dossier_query_digest,
    expansion_action_core_id,
)
from anatomize.dossiers.slicing import DossierSource, slice_dossier_source, source_identity
from anatomize.evidence import (
    AliasRecord,
    ArtifactEntity,
    CandidateKind,
    CandidateRecord,
    ConfigurationEntity,
    ContentClass,
    ContractRecord,
    DataEntity,
    DependencyEntity,
    DiagnosticEntity,
    DiagnosticObservation,
    DocumentationEntity,
    EdgeRecord,
    EntityRecord,
    EvidenceStrength,
    ExternalEntity,
    FileEntity,
    IdentityResolutionStatus,
    LineageRecord,
    LocationRecord,
    ObservationRecord,
    OmissionRecord,
    RangeEntity,
    RelationshipCategory,
    RepositoryEntity,
    RuntimeEntity,
    RuntimeObservation,
    SimilarityEntity,
    SimilarityObservation,
    SourceStateRecord,
    StructuralObservation,
    SymbolEntity,
    TestEntity,
    WorkflowEntity,
)

_AUTHORITY = "Anatomize reports evidence from this checkout; the reviewer decides and approves any change."

_SECTION_ORDER = {
    DossierSection.DECISION_CRITICAL: 0,
    DossierSection.CONTRADICTIONS_UNKNOWNS: 1,
    DossierSection.SUPPORTING_CONTEXT: 2,
}
_REASON_ORDER = {
    SelectionReasonCode.PROFILE_REQUIRED: 0,
    SelectionReasonCode.EXPLICIT_INCLUDE: 1,
    SelectionReasonCode.EXACT_TARGET: 2,
    SelectionReasonCode.EXACT_RELATIONSHIP: 3,
    SelectionReasonCode.CURRENT_CHANGE: 4,
    SelectionReasonCode.ROLE_MATCH: 5,
    SelectionReasonCode.STRUCTURAL_FALLBACK: 6,
    SelectionReasonCode.CONSERVATIVE_CANDIDATE: 7,
    SelectionReasonCode.CONFLICT_SURFACE: 8,
    SelectionReasonCode.SUPPORTING_CONTEXT: 9,
}
_STRENGTH_ORDER = {
    EvidenceStrength.EXACT: 0,
    EvidenceStrength.DERIVED: 1,
    EvidenceStrength.DECLARED: 2,
    EvidenceStrength.CONSERVATIVE: 3,
    EvidenceStrength.HEURISTIC: 4,
    EvidenceStrength.UNKNOWN: 5,
}

_PROFILE_ROLE_ORDER: dict[DossierProfile, tuple[EvidenceRole, ...]] = {
    DossierProfile.ORIENTATION: (
        EvidenceRole.STATE,
        EvidenceRole.TOPOLOGY,
        EvidenceRole.OWNERSHIP,
        EvidenceRole.PUBLIC_SURFACE,
        EvidenceRole.ENTRY_POINT,
        EvidenceRole.FLOW,
        EvidenceRole.CONTRACT,
        EvidenceRole.TEST,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.WORKFLOW,
        EvidenceRole.DATA,
        EvidenceRole.UNKNOWN,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
    DossierProfile.DESIGN: (
        EvidenceRole.CONTRACT,
        EvidenceRole.DEFINITION,
        EvidenceRole.CONSUMER,
        EvidenceRole.DEPENDENCY,
        EvidenceRole.ALTERNATIVE,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.CONFLICT,
        EvidenceRole.DECISION_CONTEXT,
        EvidenceRole.TEST,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.UNKNOWN,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
    DossierProfile.AUDIT: (
        EvidenceRole.CONTRACT,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.DEFINITION,
        EvidenceRole.PROVENANCE,
        EvidenceRole.CONFLICT,
        EvidenceRole.UNKNOWN,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
    DossierProfile.LOCALISATION: (
        EvidenceRole.DEFINITION,
        EvidenceRole.DECLARATION,
        EvidenceRole.TEST,
        EvidenceRole.CALLER,
        EvidenceRole.CONSUMER,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.CALLEE,
        EvidenceRole.REFERENCE,
        EvidenceRole.UNKNOWN,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
    DossierProfile.IMPLEMENTATION: (
        EvidenceRole.DECISION_CONTEXT,
        EvidenceRole.DEFINITION,
        EvidenceRole.TEST,
        EvidenceRole.CONTRACT,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.CALLER,
        EvidenceRole.CONSUMER,
        EvidenceRole.CALLEE,
        EvidenceRole.RUNTIME,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.DUPLICATE_CANDIDATE,
        EvidenceRole.UNKNOWN,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
    DossierProfile.CHANGE_REVIEW: (
        EvidenceRole.STATE,
        EvidenceRole.CHANGE,
        EvidenceRole.CONTRACT,
        EvidenceRole.CONSUMER,
        EvidenceRole.TEST,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.WORKFLOW,
        EvidenceRole.ARTIFACT,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.VALIDATION,
        EvidenceRole.CONFLICT,
        EvidenceRole.UNKNOWN,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
    DossierProfile.CLOSURE: (
        EvidenceRole.DECISION_CONTEXT,
        EvidenceRole.STATE,
        EvidenceRole.CHANGE,
        EvidenceRole.VALIDATION,
        EvidenceRole.CONTRACT,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.DUPLICATE_CANDIDATE,
        EvidenceRole.PROVENANCE,
        EvidenceRole.CONFLICT,
        EvidenceRole.UNKNOWN,
        EvidenceRole.SUPPORTING_CONTEXT,
    ),
}

_PROFILE_ROLE_CAPS: dict[DossierProfile, dict[EvidenceRole, int]] = {
    DossierProfile.DESIGN: {
        EvidenceRole.TEST: 3,
        EvidenceRole.DOCUMENTATION: 2,
        EvidenceRole.CONSUMER: 4,
        EvidenceRole.DEPENDENCY: 4,
    },
    DossierProfile.AUDIT: {
        EvidenceRole.DIAGNOSTIC: 6,
        EvidenceRole.CONFIGURATION: 4,
        EvidenceRole.PUBLIC_SURFACE: 8,
        EvidenceRole.TEST: 4,
        EvidenceRole.DOCUMENTATION: 2,
    },
    DossierProfile.LOCALISATION: {
        EvidenceRole.TEST: 3,
        EvidenceRole.DOCUMENTATION: 2,
        EvidenceRole.CONFIGURATION: 2,
        EvidenceRole.CALLER: 4,
        EvidenceRole.CONSUMER: 4,
        EvidenceRole.CALLEE: 4,
        EvidenceRole.REFERENCE: 4,
    },
    DossierProfile.IMPLEMENTATION: {
        EvidenceRole.TEST: 3,
        EvidenceRole.DOCUMENTATION: 2,
        EvidenceRole.CONFIGURATION: 2,
        EvidenceRole.CALLER: 4,
        EvidenceRole.CONSUMER: 4,
        EvidenceRole.CALLEE: 4,
        EvidenceRole.DIAGNOSTIC: 3,
        EvidenceRole.DUPLICATE_CANDIDATE: 3,
    },
}

_PROOF_PURPOSE = {
    EvidenceRole.STATE: "Identify the exact checkout reviewed here.",
    EvidenceRole.TOPOLOGY: "Show the repository or subsystem structure.",
    EvidenceRole.OWNERSHIP: "Show where responsibility for this code appears to live.",
    EvidenceRole.PUBLIC_SURFACE: "Identify code or documentation intended for users.",
    EvidenceRole.ENTRY_POINT: "Identify an execution or workflow entry point.",
    EvidenceRole.FLOW: "Trace a typed code, data, workflow, or artifact flow.",
    EvidenceRole.DEFINITION: "Locate the implementation being reviewed.",
    EvidenceRole.DECLARATION: "Establish the exact declaration surface.",
    EvidenceRole.DEPENDENCY: "Show an outbound dependency required by the target.",
    EvidenceRole.CALLER: "Show a typed inbound caller.",
    EvidenceRole.CALLEE: "Show a typed outbound callee.",
    EvidenceRole.REFERENCE: "Show an exact or qualified reference.",
    EvidenceRole.CONSUMER: "Show code or documentation that depends on the target.",
    EvidenceRole.CONTRACT: "Preserve a declared contract separately from observations.",
    EvidenceRole.ALTERNATIVE: "Expose a conservative alternative or comparison candidate.",
    EvidenceRole.DECISION_CONTEXT: "Reference recorded requirements or prior review decisions.",
    EvidenceRole.DIAGNOSTIC: "Expose a tool-native diagnostic without turning it into a decision.",
    EvidenceRole.CONFLICT: "Keep contradictory observations visible.",
    EvidenceRole.UNKNOWN: "Keep unresolved, ambiguous, or unavailable evidence visible.",
    EvidenceRole.TEST: "Locate verification evidence related to the target.",
    EvidenceRole.DOCUMENTATION: "Locate documentation evidence related to the target.",
    EvidenceRole.CONFIGURATION: "Locate configuration evidence related to the target.",
    EvidenceRole.DATA: "Locate a data boundary or metadata record.",
    EvidenceRole.WORKFLOW: "Locate workflow or pipeline evidence.",
    EvidenceRole.RUNTIME: "Locate captured runtime evidence.",
    EvidenceRole.ARTIFACT: "Locate generated or released artifact evidence.",
    EvidenceRole.CHANGE: "Show an exact cross-state change or lineage record.",
    EvidenceRole.VALIDATION: "Show validation evidence bound to the selected state.",
    EvidenceRole.DUPLICATE_CANDIDATE: "Show a possible duplicate that still needs review.",
    EvidenceRole.PROVENANCE: "Show provider and artifact provenance.",
    EvidenceRole.SUPPORTING_CONTEXT: "Provide lower-priority context that qualifies the core evidence.",
}


class DossierQueryError(AnatomizeError):
    """Stable query failure used when even a bounded response cannot be encoded."""


@dataclass(frozen=True)
class _Record:
    record_id: str
    kind: str
    value: Any
    source_state_id: str


@dataclass
class _Selection:
    record: _Record
    role: EvidenceRole
    section: DossierSection
    required: bool
    distance: int
    reasons: dict[tuple[str, str, str | None, str | None], SelectionReason] = field(default_factory=dict)
    relationship_ids: set[str] = field(default_factory=set)
    conflict_ids: set[str] = field(default_factory=set)
    unknown_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _ActionSpec:
    kind: ExpansionKind
    target_id: str | None = None
    role: EvidenceRole | None = None
    maximum_incremental_bytes: int | None = None
    cursor: DossierCursor | None = None


class DossierEngine:
    """Reusable deterministic query maps over one immutable dossier context."""

    def __init__(self, context: DossierContext) -> None:
        self.context = context
        self._records: dict[str, _Record] = {}
        self._entities: dict[str, EntityRecord] = {}
        self._locations: dict[str, LocationRecord] = {}
        self._edges: dict[str, EdgeRecord] = {}
        self._contracts: dict[str, ContractRecord] = {}
        self._candidates: dict[str, CandidateRecord] = {}
        self._observations: dict[str, ObservationRecord] = {}
        self._aliases: dict[str, AliasRecord] = {}
        self._lineage: dict[str, LineageRecord] = {}
        self._evidence_omissions: dict[str, OmissionRecord] = {}
        self._conflicts: dict[str, Any] = {}
        self._limitations: dict[str, Any] = {}
        self._observations_by_record: dict[str, list[ObservationRecord]] = defaultdict(list)
        self._edges_by_source: dict[str, list[EdgeRecord]] = defaultdict(list)
        self._edges_by_target: dict[str, list[EdgeRecord]] = defaultdict(list)
        self._contracts_by_subject: dict[str, list[ContractRecord]] = defaultdict(list)
        self._candidates_by_member: dict[str, list[CandidateRecord]] = defaultdict(list)
        self._conflicts_by_target: dict[str, list[Any]] = defaultdict(list)
        self._lineage_by_endpoint: dict[str, list[LineageRecord]] = defaultdict(list)
        self._provider_ids_by_run: dict[str, str] = {}
        self._cache: dict[str, Dossier] = {}
        self._dossiers_by_id: dict[str, Dossier] = {}
        self._sources: dict[tuple[str, str], DossierSource] = {
            source_identity(item): item for item in context.sources
        }
        self._build_maps()

    @property
    def cached_query_count(self) -> int:
        """Number of exact request results reused by this immutable engine."""
        return len(self._cache)

    def query(self, request: DossierRequest) -> Dossier:
        """Resolve, traverse, group, bound, and explain one dossier request."""
        cached = self._cache.get(request.request_id)
        if cached is not None:
            return cached
        if request.session_id != self.context.session_id:
            result = self._binding_failure(request, "session identity differs from the query context")
        elif request.session_manifest_digest != self.context.session_manifest_digest:
            result = self._binding_failure(request, "session manifest digest differs from the query context")
        elif request.expansion is not None:
            mismatch = self._expansion_mismatch(request)
            result = self._binding_failure(request, mismatch) if mismatch is not None else self._query_bound(request)
        else:
            result = self._query_bound(request)
        self._cache[request.request_id] = result
        self._dossiers_by_id[result.dossier_id] = result
        return result

    def expand(
        self,
        base_request: DossierRequest,
        base_dossier: Dossier,
        kind: ExpansionKind,
        *,
        target_id: str | None = None,
        role: EvidenceRole | None = None,
        budget: DossierBudget | None = None,
        slice_policy: SourceSlicePolicy | None = None,
        depth_increment: int = 1,
        context_lines: int = 3,
    ) -> Dossier:
        """Apply any public expansion kind through the same query application."""
        if self._dossiers_by_id.get(base_dossier.dossier_id) != base_dossier:
            raise DossierQueryError(
                "unknown_base_dossier",
                "The expansion base was not built or registered in this immutable engine.",
                remediation="Query or register the exact base dossier before expanding it.",
            )
        selected_budget = budget or base_request.budget
        cursor_action = next(
            (item for item in base_dossier.expansions if item.kind is kind and item.cursor is not None),
            None,
        )
        if kind is ExpansionKind.CURSOR:
            if cursor_action is None:
                raise DossierQueryError(
                    "cursor_unavailable",
                    "The base dossier has no continuation cursor.",
                    remediation="Use a non-cursor expansion or query a dossier with omitted items.",
                )
            action_id = cursor_action.action_id
            cursor = cursor_action.cursor
        else:
            action_id = expansion_action_core_id(
                kind=kind,
                target_id=target_id,
                role=role,
                base_dossier_id=base_dossier.dossier_id,
                session_id=self.context.session_id,
                session_manifest_digest=self.context.session_manifest_digest,
                maximum_incremental_bytes=base_dossier.budget_use.payload_bytes.limit,
                cursor=None,
            )
            cursor = None
        request = build_dossier_request(
            profile=base_request.profile,
            question=base_request.question,
            session_id=base_request.session_id,
            session_manifest_digest=base_request.session_manifest_digest,
            targets=base_request.targets,
            direction=base_request.direction,
            filters=base_request.filters,
            include=base_request.include,
            exclude=base_request.exclude,
            budget=selected_budget,
            slice_policy=slice_policy or base_request.slice_policy,
            expansion=DossierExpansion(
                kind=kind,
                base_dossier_id=base_dossier.dossier_id,
                action_id=action_id,
                target_id=target_id,
                role=role,
                depth_increment=depth_increment if kind is ExpansionKind.DEPTH else 0,
                context_lines=context_lines if kind is ExpansionKind.ADJACENT_CONTEXT else 0,
                cursor=cursor,
            ),
        )
        return self.query(request)

    def register_dossier(self, dossier: Dossier) -> None:
        """Register a validated portable base dossier for cross-process expansion."""
        if (
            dossier.session_id,
            dossier.session_manifest_digest,
            dossier.repository_id,
            dossier.policy_digest,
        ) != (
            self.context.session_id,
            self.context.session_manifest_digest,
            self.context.repository_id,
            self.context.policy_digest,
        ):
            raise DossierQueryError(
                "base_dossier_binding_mismatch",
                "The base dossier belongs to another query context.",
                remediation="Load the exact source-bound session used to build the dossier.",
            )
        self._dossiers_by_id[dossier.dossier_id] = dossier

    def _build_maps(self) -> None:
        for evidence in self.context.evidence:
            self._provider_ids_by_run.update(
                {item.provider_run_id: item.provider_id for item in evidence.provider_runs}
            )
            for state in evidence.states:
                self._add_record(state.state_id, "state", state, state.state_id)
            for location in evidence.locations:
                self._locations[location.location_id] = location
                self._add_record(location.location_id, "location", location, location.source_state_id)
            for entity in evidence.entities:
                self._entities[entity.entity_id] = entity
                self._add_record(entity.entity_id, "entity", entity, entity.source_state_id)
            for edge in evidence.edges:
                self._edges[edge.edge_id] = edge
                self._edges_by_source[edge.source_entity_id].append(edge)
                self._edges_by_target[edge.target_entity_id].append(edge)
                self._add_record(edge.edge_id, "edge", edge, edge.source_state_id)
            for contract in evidence.contracts:
                self._contracts[contract.contract_id] = contract
                for subject_id in contract.subject_entity_ids:
                    self._contracts_by_subject[subject_id].append(contract)
                self._add_record(contract.contract_id, "contract", contract, contract.source_state_id)
            for candidate in evidence.candidates:
                self._candidates[candidate.candidate_id] = candidate
                for member_id in candidate.member_entity_ids:
                    self._candidates_by_member[member_id].append(candidate)
                self._add_record(candidate.candidate_id, "candidate", candidate, candidate.source_state_id)
            for observation in evidence.observations:
                self._observations[observation.observation_id] = observation
                self._add_record(observation.observation_id, "observation", observation, observation.source_state_id)
                for target_id in _observation_target_ids(observation):
                    self._observations_by_record[target_id].append(observation)
            for alias in evidence.aliases:
                self._aliases[alias.alias_id] = alias
                self._add_record(alias.alias_id, "alias", alias, alias.source_state_id)
            for lineage in evidence.lineage:
                self._lineage[lineage.lineage_id] = lineage
                for endpoint_id in [*lineage.predecessor_entity_ids, *lineage.successor_entity_ids]:
                    self._lineage_by_endpoint[endpoint_id].append(lineage)
                self._add_record(lineage.lineage_id, "lineage", lineage, lineage.successor_state_id)
            for omission in evidence.omissions:
                self._evidence_omissions[omission.omission_id] = omission
                self._add_record(omission.omission_id, "omission", omission, omission.source_state_id)
            for conflict in evidence.conflicts:
                self._conflicts[conflict.conflict_id] = conflict
                self._conflicts_by_target[conflict.target_id].append(conflict)
                self._add_record(conflict.conflict_id, "conflict", conflict, conflict.source_state_id)
            for limitation in evidence.limitations:
                self._limitations[limitation.limitation_id] = limitation
        for values in (
            self._edges_by_source,
            self._edges_by_target,
            self._observations_by_record,
            self._contracts_by_subject,
            self._candidates_by_member,
            self._conflicts_by_target,
            self._lineage_by_endpoint,
        ):
            for records in values.values():
                records.sort(key=lambda item: _record_identity(item))

    def _add_record(self, record_id: str, kind: str, value: Any, source_state_id: str) -> None:
        if record_id in self._records:
            raise ValueError(f"dossier context contains duplicate record identity: {record_id}")
        self._records[record_id] = _Record(record_id, kind, value, source_state_id)

    def _query_bound(self, request: DossierRequest) -> Dossier:
        resolutions = [self._resolve_target(selector) for selector in request.targets]
        if not request.targets and request.profile is DossierProfile.ORIENTATION:
            resolutions = [self._repository_resolution()]
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection] = {}
        omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]] = []
        stop_satisfied: list[str] = []
        stop_unsatisfied: list[str] = []

        exact_ids: set[str] = set()
        for resolution in resolutions:
            if resolution.status is TargetResolutionStatus.EXACT:
                exact_ids.update(resolution.resolved_ids)
                stop_satisfied.append(f"target resolved exactly: {resolution.resolved_ids[0]}")
            elif resolution.status in {TargetResolutionStatus.AMBIGUOUS, TargetResolutionStatus.CONFLICTING}:
                stop_unsatisfied.append(f"target remains {resolution.status.value}")
                for candidate_id in resolution.resolved_ids:
                    record = self._records.get(candidate_id)
                    if record is not None:
                        self._select(
                            selections,
                            record,
                            role=EvidenceRole.ALTERNATIVE,
                            section=DossierSection.CONTRADICTIONS_UNKNOWNS,
                            required=True,
                            distance=0,
                            reason=self._reason(
                                SelectionReasonCode.CONSERVATIVE_CANDIDATE,
                                candidate_id,
                                message="Retained as a target-resolution candidate; no candidate was chosen.",
                            ),
                        )
                action = (
                    _ActionSpec(ExpansionKind.ENTITY, target_id=resolution.resolved_ids[0])
                    if resolution.resolved_ids
                    else None
                )
                omissions.append(
                    (
                        OmissionReason.AMBIGUITY,
                        EvidenceRole.UNKNOWN,
                        True,
                        resolution.resolved_ids,
                        resolution.message,
                        action,
                    )
                )
            else:
                stop_unsatisfied.append(f"target is {resolution.status.value}")
                omissions.append(
                    (
                        OmissionReason.UNSUPPORTED
                        if resolution.status is TargetResolutionStatus.UNSUPPORTED
                        else OmissionReason.SCOPE,
                        EvidenceRole.UNKNOWN,
                        True,
                        [],
                        resolution.message,
                        None,
                    )
                )

        for record_id in sorted(exact_ids):
            record = self._records[record_id]
            role = self._role_for_record(record)
            selection = self._select(
                selections,
                record,
                role=role,
                section=DossierSection.DECISION_CRITICAL,
                required=True,
                distance=0,
                reason=self._reason(
                    SelectionReasonCode.EXACT_TARGET,
                    record_id,
                    message="Resolved the explicit target exactly.",
                ),
            )
            self._add_reason(
                selection,
                self._reason(
                    SelectionReasonCode.PROFILE_REQUIRED,
                    record_id,
                    message=f"Required {role.value} role for {request.profile.value}.",
                ),
            )

        self._apply_profile_requirements(request, selections, omissions)
        self._traverse(request, exact_ids, selections, omissions)
        self._select_related_records(request, selections)
        self._apply_requested_expansion(request, selections, omissions)
        self._apply_profile_caps(request, selections, omissions)

        removed_by_filter = self._apply_filters(request, selections)

        for record_id in request.include:
            record = self._records.get(record_id)
            if record is None:
                omissions.append(
                    (
                        OmissionReason.UNSUPPORTED,
                        None,
                        False,
                        [record_id],
                        "An explicitly included identity does not exist in this session.",
                        None,
                    )
                )
                continue
            reason = self._reason(
                SelectionReasonCode.EXPLICIT_INCLUDE,
                record_id,
                message="Included explicitly by the consumer.",
            )
            existing = [item for item in selections.values() if item.record.record_id == record_id]
            if existing:
                for selection in existing:
                    self._add_reason(selection, reason)
            else:
                self._select(
                    selections,
                    record,
                    role=self._role_for_record(record),
                    section=DossierSection.SUPPORTING_CONTEXT,
                    required=False,
                    distance=0,
                    reason=reason,
                )

        removed_by_exclusion = self._apply_exclusions(request, selections)
        for label, removed in (("request filters", removed_by_filter), ("explicit exclusion", removed_by_exclusion)):
            if removed:
                required = any(item.required for item in removed)
                omissions.append(
                    (
                        OmissionReason.SCOPE,
                        None,
                        required,
                        [item.record.record_id for item in removed],
                        f"Evidence was removed by {label}.",
                        None,
                    )
                )
                if required:
                    stop_unsatisfied.append(f"{label} removed required evidence")

        omissions.extend(self._kernel_omissions(selections))

        ordered = sorted(selections.values(), key=lambda item: self._selection_key(request.profile, item))
        cursor = request.expansion.cursor if request.expansion is not None else None
        if cursor is not None:
            ordered = [
                item for item in ordered if list(self._selection_key(request.profile, item)) > cursor.ordered_after
            ]
        if exact_ids and not stop_unsatisfied:
            stop_satisfied.append("bounded relationship frontier and omissions are explicit")

        return self._fit_and_build(
            request=request,
            resolutions=resolutions,
            selections=ordered,
            base_omissions=omissions,
            stop_satisfied=stop_satisfied,
            stop_unsatisfied=stop_unsatisfied,
        )

    def _apply_profile_requirements(
        self,
        request: DossierRequest,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
        omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
    ) -> None:
        repository_scope = request.profile is DossierProfile.ORIENTATION or any(
            isinstance(selection.record.value, RepositoryEntity) for selection in selections.values()
        )
        if not repository_scope:
            return
        current_state = self.context.source_state_ids[-1]
        state_record = self._records.get(current_state)
        if state_record is not None:
            self._select_profile_record(selections, state_record, EvidenceRole.STATE, required=True)
        candidates: dict[EvidenceRole, list[_Record]] = defaultdict(list)
        documentation_entities: list[_Record] = []
        for entity_id, entity in sorted(self._entities.items()):
            if entity.source_state_id != current_state:
                continue
            record = self._records[entity_id]
            if self._record_path(record).startswith("tests/fixtures/") and not isinstance(
                entity, (FileEntity, TestEntity)
            ):
                continue
            if isinstance(entity, SymbolEntity):
                if entity.public and not self._entity_has_file_role(entity, "test"):
                    candidates[EvidenceRole.PUBLIC_SURFACE].append(record)
                continue
            if isinstance(entity, DocumentationEntity):
                # File-level documentation is enough for repository orientation;
                # headings remain available through an explicit docs target.
                documentation_entities.append(record)
                continue
            candidates[self._orientation_role(record)].append(record)
            if isinstance(entity, WorkflowEntity):
                candidates[EvidenceRole.ENTRY_POINT].append(record)
            if isinstance(entity, FileEntity) and (
                entity.content_class is ContentClass.GENERATED or "generated" in entity.roles
            ):
                candidates[EvidenceRole.ARTIFACT].append(record)
            if isinstance(entity, FileEntity) and entity.path.rsplit("/", 1)[-1] in {
                "app.py",
                "cli.py",
                "main.py",
            }:
                candidates[EvidenceRole.ENTRY_POINT].append(record)
        if not candidates[EvidenceRole.DOCUMENTATION]:
            candidates[EvidenceRole.DOCUMENTATION].extend(documentation_entities)
        caps = {
            EvidenceRole.TOPOLOGY: 1,
            EvidenceRole.OWNERSHIP: 6,
            EvidenceRole.PUBLIC_SURFACE: 8,
            EvidenceRole.ENTRY_POINT: 4,
            EvidenceRole.DOCUMENTATION: 1,
            EvidenceRole.TEST: 4,
            EvidenceRole.CONFIGURATION: 6,
            EvidenceRole.WORKFLOW: 4,
            EvidenceRole.DATA: 4,
            EvidenceRole.ARTIFACT: 4,
        }
        for role, records in sorted(candidates.items(), key=lambda item: item[0].value):
            ranked = sorted(records, key=lambda record: self._repository_record_rank(record, role))
            cap = caps.get(role, 12)
            selected = self._representative_repository_records(ranked, role, cap)
            for record in selected:
                self._select_profile_record(selections, record, role, required=False)
            selected_ids = {record.record_id for record in selected}
            omitted = [record for record in ranked if record.record_id not in selected_ids]
            if omitted:
                omissions.append(
                    (
                        OmissionReason.SCOPE,
                        role,
                        False,
                        [record.record_id for record in omitted],
                        f"Repository summary capped the {role.value} role at {cap} representative records.",
                        _ActionSpec(
                            ExpansionKind.ROLE,
                            role=role,
                            maximum_incremental_bytes=request.budget.max_payload_bytes,
                        ),
                    )
                )

    def _select_profile_record(
        self,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
        record: _Record,
        role: EvidenceRole,
        *,
        required: bool,
    ) -> None:
        section = (
            DossierSection.CONTRADICTIONS_UNKNOWNS
            if role in {EvidenceRole.CONFLICT, EvidenceRole.UNKNOWN}
            else DossierSection.DECISION_CRITICAL
        )
        self._select(
            selections,
            record,
            role=role,
            section=section,
            required=required,
            distance=0,
            reason=self._reason(
                SelectionReasonCode.PROFILE_REQUIRED,
                record.record_id,
                message=f"Selected for the {role.value} orientation boundary.",
            ),
        )

    def _orientation_role(self, record: _Record) -> EvidenceRole:
        value = record.value
        if isinstance(value, FileEntity):
            if "test" in value.roles:
                return EvidenceRole.TEST
            if "documentation" in value.roles:
                return EvidenceRole.DOCUMENTATION
            if "configuration" in value.roles:
                return EvidenceRole.CONFIGURATION
            return EvidenceRole.OWNERSHIP
        if isinstance(value, RepositoryEntity):
            return EvidenceRole.TOPOLOGY
        return self._role_for_record(record)

    def _entity_has_file_role(self, entity: EntityRecord, role: str) -> bool:
        for location_id in entity.location_ids:
            location = self._locations.get(location_id)
            if location is None or location.file_id is None:
                continue
            file_entity = self._entities.get(location.file_id)
            if isinstance(file_entity, FileEntity) and role in file_entity.roles:
                return True
        return False

    def _repository_record_rank(self, record: _Record, role: EvidenceRole) -> tuple[int, str]:
        value = record.value
        path = value.path if isinstance(value, FileEntity) else self._record_path(record)
        if role is EvidenceRole.DOCUMENTATION:
            name = path.rsplit("/", 1)[-1].casefold()
            priority = 0 if name == "readme.md" else 1 if path.startswith("docs/") else 2
            return priority, path or record.record_id
        if role is EvidenceRole.OWNERSHIP:
            name = path.rsplit("/", 1)[-1]
            priority = 0 if name == "__init__.py" else 1 if name in {"cli.py", "main.py", "app.py"} else 2
            return priority, path or record.record_id
        if role is EvidenceRole.PUBLIC_SURFACE and isinstance(value, SymbolEntity):
            exported = any(
                self._is_package_export_edge(edge)
                for edge in self._edges_by_target.get(value.entity_id, [])
            )
            private_module = any(part.startswith("_") and part != "__init__.py" for part in path.split("/"))
            boundary_rank = 0 if exported else 2 if private_module else 1
            error_penalty = 3 if value.name.endswith("Error") else 0
            return boundary_rank + error_penalty, path or record.record_id
        if role is EvidenceRole.TEST:
            filename = path.rsplit("/", 1)[-1]
            if isinstance(value, FileEntity):
                priority = (
                    0
                    if filename.startswith("test_") or filename.endswith("_test.py")
                    else 1
                    if filename == "conftest.py"
                    else 2
                    if filename != "__init__.py"
                    else 3
                )
            else:
                priority = 1 if isinstance(value, TestEntity) else 2
            return priority, path or record.record_id
        if role is EvidenceRole.CONFIGURATION:
            name = path.rsplit("/", 1)[-1]
            priority = (
                0
                if name == "pyproject.toml"
                else 1
                if path == ".github/workflows/ci.yml"
                else 2
                if path == "agents/openai.yaml"
                else 3
                if name == "uv.lock"
                else 4
                if name == ".gitignore"
                else 5
                if name == ".gitattributes"
                else 6
                if path.startswith(".github/workflows/")
                else 8
                if path.startswith("tests/fixtures/")
                else 7
            )
            return priority, path or record.record_id
        return 0, path or record.record_id

    def _representative_repository_records(
        self,
        records: list[_Record],
        role: EvidenceRole,
        cap: int,
    ) -> list[_Record]:
        if role is not EvidenceRole.PUBLIC_SURFACE:
            return records[:cap]
        selected: list[_Record] = []
        deferred: list[_Record] = []
        seen_packages: set[str] = set()
        for record in records:
            path = self._record_path(record)
            parts = path.split("/")
            package = parts[2] if len(parts) > 3 and parts[0] == "src" else "/".join(parts[:-1])
            if package in seen_packages:
                deferred.append(record)
                continue
            seen_packages.add(package)
            selected.append(record)
            if len(selected) == cap:
                return selected
        return [*selected, *deferred[: cap - len(selected)]]

    def _is_package_export_edge(self, edge: EdgeRecord) -> bool:
        source = self._entities.get(edge.source_entity_id)
        return isinstance(source, FileEntity) and source.path.endswith("/__init__.py")

    def _record_path(self, record: _Record) -> str:
        return next(
            (
                location.path
                for location_id in getattr(record.value, "location_ids", [])
                if (location := self._locations.get(location_id)) is not None and location.path is not None
            ),
            "",
        )

    def _apply_profile_caps(
        self,
        request: DossierRequest,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
        omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
    ) -> None:
        """Keep the first page diverse while making every capped role recoverable."""
        for role, cap in _PROFILE_ROLE_CAPS.get(request.profile, {}).items():
            if (
                request.expansion is not None
                and request.expansion.kind is ExpansionKind.ROLE
                and request.expansion.role is role
            ):
                continue
            values = sorted(
                (item for item in selections.values() if item.role is role),
                key=lambda item: self._selection_key(request.profile, item),
            )
            required = [item for item in values if item.required]
            supporting = [item for item in values if not item.required]
            keep = {id(item) for item in [*required, *supporting[: max(0, cap - len(required))]]}
            removed = [item for item in values if id(item) not in keep]
            if not removed:
                continue
            removed_ids = {id(item) for item in removed}
            for key in [key for key, item in selections.items() if id(item) in removed_ids]:
                selections.pop(key)
            omissions.append(
                (
                    OmissionReason.SCOPE,
                    role,
                    False,
                    [item.record.record_id for item in removed],
                    f"The first page capped {role.value} evidence at {cap} high-priority records.",
                    _ActionSpec(
                        ExpansionKind.ROLE,
                        role=role,
                        maximum_incremental_bytes=request.budget.max_payload_bytes,
                    ),
                )
            )

    def _apply_requested_expansion(
        self,
        request: DossierRequest,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
        omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
    ) -> None:
        expansion = request.expansion
        if expansion is None or expansion.kind in {
            ExpansionKind.CURSOR,
            ExpansionKind.DEPTH,
            ExpansionKind.ADJACENT_CONTEXT,
            ExpansionKind.COMPLETE_FILE,
        }:
            return
        if expansion.kind is ExpansionKind.REFRESH:
            omissions.append(
                (
                    OmissionReason.SOURCE_DRIFT,
                    EvidenceRole.STATE,
                    True,
                    [],
                    "Refresh requires a separately authorized session rebuild; "
                    "the query engine cannot mutate source state.",
                    None,
                )
            )
            return

        record_ids: set[str] = set()
        if expansion.kind is ExpansionKind.ENTITY and expansion.target_id is not None:
            record_ids.add(expansion.target_id)
        elif expansion.kind is ExpansionKind.RELATIONSHIP and expansion.target_id is not None:
            edge = self._edges.get(expansion.target_id)
            if edge is not None:
                record_ids.update({edge.edge_id, edge.source_entity_id, edge.target_entity_id})
        elif expansion.kind is ExpansionKind.ROLE and expansion.role is not None:
            record_ids.update(
                item.record_id for item in self._records.values() if self._role_for_record(item) is expansion.role
            )
        elif expansion.kind is ExpansionKind.PROVIDER and expansion.target_id is not None:
            provider = expansion.target_id
            record_ids.update(
                item.record_id
                for item in self._records.values()
                if any(
                    run_id == provider or self._provider_ids_by_run.get(run_id) == provider
                    for run_id in self._provider_run_ids(item)
                )
            )
        elif expansion.kind is ExpansionKind.HISTORY:
            record_ids.update(self._lineage)
            record_ids.update(item.state_id for item in self._state_records())
            if expansion.target_id is not None:
                record_ids.add(expansion.target_id)
        elif expansion.kind is ExpansionKind.VALIDATION:
            record_ids.update(
                item.record_id
                for item in self._records.values()
                if self._role_for_record(item)
                in {EvidenceRole.VALIDATION, EvidenceRole.DIAGNOSTIC, EvidenceRole.RUNTIME, EvidenceRole.CONFLICT}
            )
        elif expansion.kind in {ExpansionKind.GROUP, ExpansionKind.OMISSION}:
            base = self._dossiers_by_id.get(expansion.base_dossier_id)
            if base is not None:
                item_by_id = {item.item_id: item for item in base.items}
                if expansion.kind is ExpansionKind.GROUP:
                    group = next((item for item in base.groups if item.group_id == expansion.target_id), None)
                    if group is not None:
                        record_ids.update(item_by_id[item_id].record_id for item_id in group.item_ids)
                else:
                    omission = next(
                        (item for item in base.omissions if item.omission_id == expansion.target_id),
                        None,
                    )
                    if omission is not None:
                        record_ids.update(
                            item_by_id[value].record_id if value in item_by_id else value
                            for value in omission.disclosed_ids
                        )
                    if expansion.target_id in self._records:
                        record_ids.add(expansion.target_id)

        added = 0
        for record_id in sorted(record_ids):
            record = self._records.get(record_id)
            if record is None:
                continue
            role = expansion.role or self._role_for_record(record)
            self._select(
                selections,
                record,
                role=role,
                section=DossierSection.SUPPORTING_CONTEXT,
                required=False,
                distance=0,
                reason=self._reason(
                    SelectionReasonCode.EXPLICIT_INCLUDE,
                    record_id,
                    object_id=expansion.base_dossier_id,
                    relationship_id=(
                        expansion.target_id if expansion.kind is ExpansionKind.RELATIONSHIP else None
                    ),
                    message=f"Selected by the explicit {expansion.kind.value} expansion.",
                ),
            )
            added += 1
        if added == 0:
            omissions.append(
                (
                    OmissionReason.UNSUPPORTED,
                    expansion.role,
                    False,
                    [expansion.target_id] if expansion.target_id else [],
                    f"The {expansion.kind.value} expansion matched no evidence in this session.",
                    None,
                )
            )

    def _traverse(
        self,
        request: DossierRequest,
        target_ids: set[str],
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
        omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
    ) -> None:
        max_depth = request.budget.max_depth
        if request.expansion is not None and request.expansion.kind is ExpansionKind.DEPTH:
            max_depth = min(32, max_depth + request.expansion.depth_increment)
        queue: deque[tuple[str, int]] = deque(
            (record_id, 0) for record_id in sorted(target_ids) if record_id in self._entities
        )
        visited: dict[str, int] = {record_id: 0 for record_id, _ in queue}
        beyond: set[str] = set()
        while queue:
            entity_id, distance = queue.popleft()
            entity = self._entities[entity_id]
            directional: list[tuple[EdgeRecord, str, bool]] = []
            if request.direction in {QueryDirection.OUTBOUND, QueryDirection.BOTH}:
                directional.extend((edge, edge.target_entity_id, True) for edge in self._edges_by_source[entity_id])
            if request.direction in {QueryDirection.INBOUND, QueryDirection.BOTH}:
                directional.extend((edge, edge.source_entity_id, False) for edge in self._edges_by_target[entity_id])
            for edge, neighbour_id, outbound in sorted(
                directional,
                key=lambda value: (value[0].category.value, value[0].predicate, value[1], value[0].edge_id),
            ):
                next_distance = distance + 1
                known_distance = visited.get(neighbour_id)
                if known_distance is not None and next_distance > known_distance:
                    continue
                if next_distance > max_depth:
                    beyond.add(neighbour_id)
                    continue
                record = self._records[neighbour_id]
                relationship_role = _relationship_role(edge.category, outbound=outbound)
                section = DossierSection.DECISION_CRITICAL
                enqueue = True
                if edge.category is RelationshipCategory.STRUCTURE:
                    section = DossierSection.SUPPORTING_CONTEXT
                    neighbour = record.value
                    if isinstance(entity, FileEntity) and isinstance(neighbour, SymbolEntity):
                        # A file target needs its module surface, not every
                        # method as an equal review item. Method-level work is
                        # reached by selecting the symbol itself or through a
                        # real caller/callee edge.
                        if (
                            entity_id not in target_ids
                            or not neighbour.public
                            or neighbour.symbol_kind == "method"
                        ):
                            continue
                        relationship_role = EvidenceRole.DEFINITION
                    elif isinstance(entity, SymbolEntity) and isinstance(neighbour, FileEntity):
                        # The containing file locates the target but traversing
                        # back through it would fan out to unrelated siblings.
                        relationship_role = EvidenceRole.DEFINITION
                        enqueue = False
                semantic_role = self._relationship_selection_role(record, relationship_role)
                reason = self._reason(
                    SelectionReasonCode.EXACT_RELATIONSHIP,
                    neighbour_id,
                    object_id=entity_id,
                    relationship_id=edge.edge_id,
                    message=f"Selected by {edge.category.value}:{edge.predicate} at distance {next_distance}.",
                )
                for role in dict.fromkeys((semantic_role, relationship_role)):
                    selection = self._select(
                        selections,
                        record,
                        role=role,
                        section=section,
                        required=False,
                        distance=next_distance,
                        reason=reason,
                    )
                    selection.relationship_ids.add(edge.edge_id)
                if enqueue and (known_distance is None or next_distance < known_distance):
                    visited[neighbour_id] = next_distance
                    queue.append((neighbour_id, next_distance))
        if beyond:
            omissions.append(
                (
                    OmissionReason.SCOPE,
                    None,
                    False,
                    sorted(beyond),
                    f"Typed relationships continue beyond depth {max_depth}.",
                    _ActionSpec(ExpansionKind.DEPTH, maximum_incremental_bytes=request.budget.max_payload_bytes),
                )
            )

    def _select_related_records(
        self,
        request: DossierRequest,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
    ) -> None:
        selected_entity_ids = {item.record.record_id for item in selections.values() if item.record.kind == "entity"}
        related_contracts = {
            item.contract_id: item
            for entity_id in selected_entity_ids
            for item in self._contracts_by_subject.get(entity_id, [])
        }
        for contract in sorted(related_contracts.values(), key=lambda item: item.contract_id):
            matched_subjects = sorted(selected_entity_ids.intersection(contract.subject_entity_ids))
            if matched_subjects:
                self._select(
                    selections,
                    self._records[contract.contract_id],
                    role=EvidenceRole.CONTRACT,
                    section=DossierSection.DECISION_CRITICAL,
                    required=False,
                    distance=1,
                    reason=self._reason(
                        SelectionReasonCode.ROLE_MATCH,
                        contract.contract_id,
                        object_id=matched_subjects[0],
                        message="Declared contract names a selected entity.",
                    ),
                )
        related_candidates = {
            item.candidate_id: item
            for entity_id in selected_entity_ids
            for item in self._candidates_by_member.get(entity_id, [])
        }
        for candidate in sorted(related_candidates.values(), key=lambda item: item.candidate_id):
            matched = sorted(selected_entity_ids.intersection(candidate.member_entity_ids))
            if not matched:
                continue
            role = (
                EvidenceRole.DUPLICATE_CANDIDATE
                if candidate.kind is CandidateKind.DUPLICATION
                else EvidenceRole.ALTERNATIVE
            )
            self._select(
                selections,
                self._records[candidate.candidate_id],
                role=role,
                section=DossierSection.DECISION_CRITICAL,
                required=False,
                distance=1,
                reason=self._reason(
                    SelectionReasonCode.CONSERVATIVE_CANDIDATE,
                    candidate.candidate_id,
                    object_id=matched[0],
                    message="These items look similar; Anatomize has not decided that they serve the same purpose.",
                ),
            )
            for member_id in candidate.member_entity_ids:
                if member_id in selected_entity_ids:
                    continue
                self._select(
                    selections,
                    self._records[member_id],
                    role=role,
                    section=DossierSection.SUPPORTING_CONTEXT,
                    required=False,
                    distance=1,
                    reason=self._reason(
                        SelectionReasonCode.CONSERVATIVE_CANDIDATE,
                        member_id,
                        object_id=candidate.candidate_id,
                        message="Included because it belongs to the same possible-duplicate group.",
                    ),
                )
        related_observations = {
            item.observation_id: item
            for entity_id in selected_entity_ids
            for item in self._observations_by_record.get(entity_id, [])
        }
        for observation in sorted(related_observations.values(), key=lambda item: item.observation_id):
            targets = set(_observation_target_ids(observation))
            if not targets.intersection(selected_entity_ids):
                continue
            for related_id in _observation_related_entity_ids(observation):
                if related_id not in self._entities or related_id in selected_entity_ids:
                    continue
                record = self._records[related_id]
                self._select(
                    selections,
                    record,
                    role=self._role_for_record(record),
                    section=DossierSection.DECISION_CRITICAL,
                    required=False,
                    distance=1,
                    reason=self._reason(
                        SelectionReasonCode.ROLE_MATCH,
                        related_id,
                        object_id=observation.observation_id,
                        message="Selected through an independent provider observation.",
                    ),
                )
        selected_record_ids = {item.record.record_id for item in selections.values()}
        selected_relationship_ids = {
            relationship_id for item in selections.values() for relationship_id in item.relationship_ids
        }
        related_conflicts = {
            item.conflict_id: item
            for target_id in selected_record_ids | selected_relationship_ids
            for item in self._conflicts_by_target.get(target_id, [])
        }
        for conflict in sorted(related_conflicts.values(), key=lambda item: item.conflict_id):
            for selection in selections.values():
                if (
                    selection.record.record_id == conflict.target_id
                    or conflict.target_id in selection.relationship_ids
                ):
                    selection.conflict_ids.add(conflict.conflict_id)
            selection = self._select(
                selections,
                self._records[conflict.conflict_id],
                role=EvidenceRole.CONFLICT,
                section=DossierSection.CONTRADICTIONS_UNKNOWNS,
                required=True,
                distance=0,
                reason=self._reason(
                    SelectionReasonCode.CONFLICT_SURFACE,
                    conflict.conflict_id,
                    object_id=conflict.target_id,
                    message="Conflicting reports are retained and remain unresolved.",
                ),
            )
            selection.conflict_ids.add(conflict.conflict_id)
        if request.profile in {
            DossierProfile.LOCALISATION,
            DossierProfile.IMPLEMENTATION,
            DossierProfile.CHANGE_REVIEW,
            DossierProfile.CLOSURE,
        }:
            related_lineage = {
                item.lineage_id: item
                for entity_id in selected_entity_ids
                for item in self._lineage_by_endpoint.get(entity_id, [])
            }
            for lineage in sorted(related_lineage.values(), key=lambda item: item.lineage_id):
                endpoints = set(lineage.predecessor_entity_ids).union(lineage.successor_entity_ids)
                if endpoints.intersection(selected_entity_ids):
                    self._select(
                        selections,
                        self._records[lineage.lineage_id],
                        role=EvidenceRole.CHANGE,
                        section=DossierSection.DECISION_CRITICAL,
                        required=False,
                        distance=0,
                        reason=self._reason(
                            SelectionReasonCode.CURRENT_CHANGE,
                            lineage.lineage_id,
                            object_id=sorted(endpoints.intersection(selected_entity_ids))[0],
                            message="Cross-state lineage touches a selected entity.",
                        ),
                    )
                    if request.profile in {DossierProfile.CHANGE_REVIEW, DossierProfile.CLOSURE}:
                        for endpoint_id in sorted(endpoints):
                            endpoint = self._records.get(endpoint_id)
                            if endpoint is not None:
                                self._select(
                                    selections,
                                    endpoint,
                                    role=EvidenceRole.CHANGE,
                                    section=DossierSection.DECISION_CRITICAL,
                                    required=False,
                                    distance=0,
                                    reason=self._reason(
                                        SelectionReasonCode.CURRENT_CHANGE,
                                        endpoint_id,
                                        object_id=lineage.lineage_id,
                                        message="Selected as an exact before/after lineage endpoint.",
                                    ),
                                )
                        for state_id in (lineage.predecessor_state_id, lineage.successor_state_id):
                            state = self._records.get(state_id)
                            if state is not None:
                                self._select(
                                    selections,
                                    state,
                                    role=EvidenceRole.STATE,
                                    section=DossierSection.DECISION_CRITICAL,
                                    required=False,
                                    distance=0,
                                    reason=self._reason(
                                        SelectionReasonCode.CURRENT_CHANGE,
                                        state_id,
                                        object_id=lineage.lineage_id,
                                        message="Selected to bind an exact lineage source state.",
                                    ),
                                )

    def _apply_filters(
        self,
        request: DossierRequest,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
    ) -> list[_Selection]:
        filters = request.filters
        removed: list[_Selection] = []
        for key, selection in list(selections.items()):
            if filters.evidence_kinds and self._evidence_kind(selection.record) not in filters.evidence_kinds:
                removed.append(selections.pop(key))
                continue
            if filters.roles and selection.role not in filters.roles:
                removed.append(selections.pop(key))
                continue
            provider_runs = set(self._provider_run_ids(selection.record))
            providers = provider_runs | {
                self._provider_ids_by_run[item] for item in provider_runs if item in self._provider_ids_by_run
            }
            if filters.provider_ids and not providers.intersection(filters.provider_ids):
                removed.append(selections.pop(key))
                continue
            if filters.source_state_ids and selection.record.source_state_id not in filters.source_state_ids:
                removed.append(selections.pop(key))
                continue
            if filters.relationship_categories:
                categories = {self._edges[item].category.value for item in selection.relationship_ids}
                if not categories.intersection(filters.relationship_categories):
                    removed.append(selections.pop(key))
                    continue
            if filters.content_classes:
                entity = selection.record.value
                content_class = getattr(entity, "content_class", None)
                if content_class is None or content_class not in filters.content_classes:
                    removed.append(selections.pop(key))
        return sorted(removed, key=lambda item: item.record.record_id)

    def _apply_exclusions(
        self,
        request: DossierRequest,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
    ) -> list[_Selection]:
        excluded = set(request.exclude)
        removed = [selection for selection in selections.values() if selection.record.record_id in excluded]
        for key in [key for key, value in selections.items() if value.record.record_id in excluded]:
            selections.pop(key)
        return sorted(removed, key=lambda item: item.record.record_id)

    def _fit_and_build(
        self,
        *,
        request: DossierRequest,
        resolutions: list[ResolvedTarget],
        selections: list[_Selection],
        base_omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
        stop_satisfied: list[str],
        stop_unsatisfied: list[str],
    ) -> Dossier:
        maximum = min(len(selections), request.budget.max_items)

        def assemble(limit: int) -> tuple[Dossier, int]:
            selected = selections[:limit]
            omissions = list(base_omissions)
            if limit < len(selections):
                omitted = selections[limit:]
                omissions.append(
                    (
                        OmissionReason.BUDGET,
                        None,
                        any(item.required for item in omitted),
                        [self._build_item(item).item_id for item in omitted],
                        "The item or payload budget ended deterministic selection.",
                        _ActionSpec(
                            ExpansionKind.CURSOR,
                            maximum_incremental_bytes=request.budget.max_payload_bytes,
                        ),
                    )
                )
            result = self._assemble(
                request=request,
                resolutions=resolutions,
                selected=selected,
                raw_omissions=omissions,
                stop_satisfied=stop_satisfied,
                stop_unsatisfied=stop_unsatisfied,
                has_more=limit < len(selections),
            )
            return result, len(canonical_dossier_bytes(result))

        result, size = assemble(maximum)
        if size + 32 <= request.budget.max_payload_bytes:
            return self._with_payload_use(result, size)

        empty, empty_size = assemble(0)
        if empty_size + 32 > request.budget.max_payload_bytes:
            raise DossierQueryError(
                "payload_budget_too_small",
                f"Dossier envelope is {empty_size} bytes; limit is {request.budget.max_payload_bytes}",
                remediation="Raise max_payload_bytes explicitly; evidence was not silently truncated.",
            )

        best_result, best_size = empty, empty_size
        lower, upper = 1, maximum - 1
        while lower <= upper:
            limit = (lower + upper) // 2
            candidate, candidate_size = assemble(limit)
            if candidate_size + 32 <= request.budget.max_payload_bytes:
                best_result, best_size = candidate, candidate_size
                lower = limit + 1
            else:
                upper = limit - 1
        return self._with_payload_use(best_result, best_size)

    def _assemble(
        self,
        *,
        request: DossierRequest,
        resolutions: list[ResolvedTarget],
        selected: list[_Selection],
        raw_omissions: list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
        stop_satisfied: list[str],
        stop_unsatisfied: list[str],
        has_more: bool,
        force_status: DossierStatus | None = None,
    ) -> Dossier:
        content_ids, content, content_omissions, inline_content_bytes = self._build_contents(request, selected)
        raw_omissions = [*raw_omissions, *content_omissions]
        items = [self._build_item(item, content_id=content_ids.get(item.record.record_id)) for item in selected]
        groups = []
        grouped: dict[tuple[DossierSection, EvidenceRole], list[DossierItem]] = defaultdict(list)
        for item in items:
            grouped[(item.section, item.role)].append(item)
        for section, role in sorted(
            grouped,
            key=lambda key: (_SECTION_ORDER[key[0]], self._role_position(request.profile, key[1]), key[1].value),
        ):
            group_items = grouped[(section, role)]
            groups.append(
                build_dossier_group(
                    section=section,
                    role=role,
                    proof_purpose=_PROOF_PURPOSE[role],
                    required=any(item.required for item in group_items),
                    item_ids=[item.item_id for item in group_items],
                )
            )
        items_by_id = {item.item_id: item for item in items}
        items = [items_by_id[item_id] for group in groups for item_id in group.item_ids]

        action_specs = [entry[5] for entry in raw_omissions if entry[5] is not None]
        if request.profile in {DossierProfile.CHANGE_REVIEW, DossierProfile.CLOSURE} and any(
            item.record.kind == "lineage" for item in selected
        ):
            action_specs.extend(
                [
                    _ActionSpec(ExpansionKind.HISTORY, maximum_incremental_bytes=request.budget.max_payload_bytes),
                    _ActionSpec(
                        ExpansionKind.VALIDATION,
                        maximum_incremental_bytes=request.budget.max_payload_bytes,
                    ),
                ]
            )
        unique_specs: dict[tuple[str, str, str], _ActionSpec] = {}
        for spec in action_specs:
            assert spec is not None
            key = (spec.kind.value, spec.target_id or "", spec.role.value if spec.role else "")
            unique_specs[key] = spec
        action_ids = {
            key: expansion_action_core_id(
                kind=spec.kind,
                target_id=spec.target_id,
                role=spec.role,
                base_dossier_id="pending",
                session_id=self.context.session_id,
                session_manifest_digest=self.context.session_manifest_digest,
                maximum_incremental_bytes=spec.maximum_incremental_bytes,
                cursor=None,
            )
            for key, spec in unique_specs.items()
        }
        omissions: list[DossierOmission] = []
        for reason, omission_role, required, identities, message, action_spec in raw_omissions:
            disclosed = sorted(set(identities))[: request.budget.max_disclosed_omission_ids]
            recovery_ids: list[str] = []
            if action_spec is not None:
                key = (
                    action_spec.kind.value,
                    action_spec.target_id or "",
                    action_spec.role.value if action_spec.role else "",
                )
                recovery_ids.append(action_ids[key])
            omissions.append(
                build_dossier_omission(
                    reason=reason,
                    role=omission_role,
                    required=required,
                    total_count=len(set(identities)),
                    disclosed_ids=disclosed,
                    message=message,
                    recovery_action_ids=recovery_ids,
                )
            )
        omissions = sorted(omissions, key=lambda item: (not item.required, item.reason.value, item.omission_id))

        unsatisfied = list(dict.fromkeys(stop_unsatisfied))
        if any(item.required for item in omissions):
            unsatisfied.append("required evidence remains omitted")
        status = force_status or DossierStatus.COMPLETE
        if force_status is None:
            if unsatisfied or any(item.required for item in omissions) or has_more:
                status = DossierStatus.PARTIAL
            if resolutions and all(
                item.status in {TargetResolutionStatus.UNRESOLVED, TargetResolutionStatus.UNSUPPORTED}
                for item in resolutions
            ):
                status = DossierStatus.BLOCKED
        boundary = DossierBoundary(
            profile=request.profile,
            question=request.question,
            targets=resolutions,
            authority=_AUTHORITY,
            satisfied_stop_conditions=list(dict.fromkeys(stop_satisfied)),
            unsatisfied_stop_conditions=list(dict.fromkeys(unsatisfied)),
        )
        base_dossier_id = request.expansion.base_dossier_id if request.expansion is not None else None
        core = {
            "request_id": request.request_id,
            "base_dossier_id": base_dossier_id,
            "session_id": self.context.session_id,
            "session_manifest_digest": self.context.session_manifest_digest,
            "repository_id": self.context.repository_id,
            "source_state_ids": list(self.context.source_state_ids),
            "provider_run_ids": list(self.context.provider_run_ids),
            "policy_digest": self.context.policy_digest,
            "status": status,
            "boundary": boundary,
            "groups": groups,
            "items": items,
            "content": content,
            "omissions": omissions,
            "limitations": self._limitation_messages(),
        }
        identity = dossier_core_id(**core)
        cursor: DossierCursor | None = None
        if has_more:
            ordered_after = list(self._selection_key(request.profile, selected[-1])) if selected else [""] * 9
            cursor = build_dossier_cursor(
                base_dossier_id=identity,
                base_dossier_digest=_identity_digest(identity),
                base_request_id=request.request_id,
                query_digest=dossier_query_digest(request),
                session_id=self.context.session_id,
                session_manifest_digest=self.context.session_manifest_digest,
                policy_digest=self.context.policy_digest,
                expansion_kind=ExpansionKind.CURSOR,
                ordered_after=ordered_after,
            )
        actions: list[ExpansionAction] = []
        for key, spec in sorted(unique_specs.items()):
            action_cursor = cursor if spec.kind is ExpansionKind.CURSOR else None
            if spec.kind is ExpansionKind.CURSOR and action_cursor is None:
                continue
            actions.append(
                build_expansion_action(
                    kind=spec.kind,
                    target_id=spec.target_id,
                    role=spec.role,
                    base_dossier_id=identity,
                    session_id=self.context.session_id,
                    session_manifest_digest=self.context.session_manifest_digest,
                    maximum_incremental_bytes=spec.maximum_incremental_bytes,
                    cursor=action_cursor,
                )
            )
        # If a cursor action was removed because no page remains, remove its now-invalid
        # recovery reference and rebuild the affected omission and dossier identity.
        valid_action_ids = {item.action_id for item in actions}
        if any(set(item.recovery_action_ids).difference(valid_action_ids) for item in omissions):
            omissions = [
                build_dossier_omission(
                    reason=item.reason,
                    role=item.role,
                    required=item.required,
                    total_count=item.total_count,
                    disclosed_ids=item.disclosed_ids,
                    message=item.message,
                    recovery_action_ids=[value for value in item.recovery_action_ids if value in valid_action_ids],
                )
                for item in omissions
            ]
            core["omissions"] = omissions
            identity = dossier_core_id(**core)
            actions = [
                build_expansion_action(
                    kind=item.kind,
                    target_id=item.target_id,
                    role=item.role,
                    base_dossier_id=identity,
                    session_id=item.session_id,
                    session_manifest_digest=item.session_manifest_digest,
                    maximum_incremental_bytes=item.maximum_incremental_bytes,
                    cursor=item.cursor,
                )
                for item in actions
            ]
        use = DossierBudgetUse(
            items=BudgetCounter(limit=request.budget.max_items, used=len(items)),
            payload_bytes=BudgetCounter(limit=request.budget.max_payload_bytes, used=0),
            inline_content_bytes=BudgetCounter(
                limit=request.budget.max_inline_content_bytes,
                used=inline_content_bytes,
            ),
            depth=BudgetCounter(
                limit=request.budget.max_depth
                + (
                    request.expansion.depth_increment
                    if request.expansion and request.expansion.kind is ExpansionKind.DEPTH
                    else 0
                ),
                used=max((item.distance for item in items), default=0),
            ),
        )
        return Dossier(dossier_id=identity, expansions=actions, budget_use=use, **core)

    def _build_contents(
        self,
        request: DossierRequest,
        selections: list[_Selection],
    ) -> tuple[
        dict[str, str],
        list[DossierContent],
        list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]],
        int,
    ]:
        if not self._sources:
            return {}, [], [], 0
        content_ids: dict[str, str] = {}
        content_by_id: dict[str, DossierContent] = {}
        omissions: list[
            tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]
        ] = []
        inline_used = 0
        expansion = request.expansion
        for selection in selections:
            record = selection.record
            file_expansion = expansion is not None and expansion.kind is ExpansionKind.COMPLETE_FILE and (
                expansion.target_id is None or expansion.target_id == record.record_id
            )
            if (
                isinstance(record.value, FileEntity)
                and not file_expansion
                and not any(item.code is SelectionReasonCode.EXACT_TARGET for item in selection.reasons.values())
            ):
                continue
            locators = [item for item in self._locators_for(record) if item.path is not None]
            if not locators and isinstance(record.value, FileEntity):
                locators = [EvidenceLocator(path=record.value.path)]
            if not locators:
                continue
            locator = locators[0]
            assert locator.path is not None
            source = self._sources.get((record.source_state_id, locator.path))
            if source is None:
                omissions.append(
                    (
                        OmissionReason.UNSUPPORTED,
                        selection.role,
                        False,
                        [record.record_id],
                        "No authorized source snapshot is bound to the selected evidence location.",
                        None,
                    )
                )
                continue
            if source.content_class not in request.slice_policy.allowed_content_classes:
                omissions.append(
                    (
                        OmissionReason.POLICY,
                        selection.role,
                        False,
                        [record.record_id],
                        f"Source content class {source.content_class.value} is excluded by slice policy.",
                        None,
                    )
                )
                continue
            applies_to_target = expansion is not None and (
                expansion.target_id is None
                or expansion.target_id in {record.record_id, locator.path}
            )
            complete_file = bool(
                applies_to_target and expansion is not None and expansion.kind is ExpansionKind.COMPLETE_FILE
            )
            extra_context = (
                expansion.context_lines
                if applies_to_target
                and expansion is not None
                and expansion.kind is ExpansionKind.ADJACENT_CONTEXT
                else 0
            )
            item = slice_dossier_source(
                source,
                start_line=locator.start_line,
                start_column=locator.start_column,
                end_line=locator.end_line,
                end_column=locator.end_column,
                enclosing_entity_id=(
                    self._enclosing_entity_id(record, locator)
                    if request.slice_policy.include_enclosing_structure
                    else None
                ),
                policy=request.slice_policy,
                extra_context=extra_context,
                complete_file=complete_file,
                max_text_bytes=request.budget.max_content_block_bytes,
            )
            if item.content_id in content_by_id:
                content_ids[record.record_id] = item.content_id
                continue
            text_bytes = len(item.text.encode("utf-8")) if item.text is not None else 0
            if text_bytes and inline_used + text_bytes > request.budget.max_inline_content_bytes:
                values = item.model_dump(mode="python", exclude={"content_id"})
                values.update(text=None, truncated=True)
                item = build_dossier_content(**values)
                omissions.append(
                    (
                        OmissionReason.BUDGET,
                        selection.role,
                        False,
                        [record.record_id],
                        "Inline source content exceeded the aggregate content budget; metadata and digest remain.",
                        _ActionSpec(ExpansionKind.OMISSION, target_id=record.record_id),
                    )
                )
            else:
                inline_used += text_bytes
            if item.truncated:
                omissions.append(
                    (
                        OmissionReason.BUDGET,
                        selection.role,
                        False,
                        [record.record_id],
                        "Source content exceeded the per-block budget; the exact full-content digest remains.",
                        _ActionSpec(ExpansionKind.COMPLETE_FILE, target_id=record.record_id),
                    )
                )
            if item.text is None and source.text is None:
                omissions.append(
                    (
                        OmissionReason.POLICY,
                        selection.role,
                        False,
                        [record.record_id],
                        "The authorized source is content-free; only size, class, and digest are available.",
                        None,
                    )
                )
            content_by_id[item.content_id] = item
            content_ids[record.record_id] = item.content_id
        return content_ids, [content_by_id[key] for key in sorted(content_by_id)], omissions, inline_used

    def _enclosing_entity_id(self, record: _Record, locator: EvidenceLocator) -> str | None:
        if isinstance(record.value, RangeEntity) and record.value.parent_entity_id is not None:
            return record.value.parent_entity_id
        if isinstance(record.value, SymbolEntity):
            return record.record_id
        candidates: list[tuple[int, str]] = []
        for entity_id, entity in self._entities.items():
            if not isinstance(entity, SymbolEntity) or entity.source_state_id != record.source_state_id:
                continue
            for item in self._locators_for(self._records[entity_id]):
                if item.path != locator.path or item.start_line is None or locator.start_line is None:
                    continue
                item_end = item.end_line or item.start_line
                locator_end = locator.end_line or locator.start_line
                if item.start_line <= locator.start_line and item_end >= locator_end:
                    candidates.append((item_end - item.start_line, entity_id))
        return min(candidates)[1] if candidates else None

    def _with_payload_use(self, dossier: Dossier, initial_size: int) -> Dossier:
        size = initial_size
        result = dossier
        for _ in range(4):
            use = result.budget_use.model_copy(
                update={
                    "payload_bytes": BudgetCounter(
                        limit=result.budget_use.payload_bytes.limit,
                        used=size,
                    )
                }
            )
            result = result.model_copy(update={"budget_use": use})
            measured = len(canonical_dossier_bytes(result))
            if measured == size:
                return result
            size = measured
        raise RuntimeError("dossier payload byte accounting did not converge")

    def _build_item(self, selection: _Selection, *, content_id: str | None = None) -> DossierItem:
        record = selection.record
        observations = self._observations_for(record)
        for relationship_id in sorted(selection.relationship_ids):
            observations.extend(self._observations_for(self._records[relationship_id]))
        observations = list({item.observation_id: item for item in observations}.values())
        observations.sort(key=lambda item: item.observation_id)
        return build_dossier_item(
            record_id=record.record_id,
            record_kind=record.kind,
            entity_id=record.record_id if record.kind == "entity" else None,
            relationship_ids=sorted(selection.relationship_ids),
            observations=[
                DossierObservation(
                    observation_id=item.observation_id,
                    provider_run_id=item.provider_run_id,
                    method=item.method,
                    method_version=item.method_version,
                    strength=item.strength,
                    stance=item.stance.value,
                    limitation_ids=item.limitation_ids,
                    rationale=item.rationale,
                )
                for item in observations
            ],
            role=selection.role,
            section=selection.section,
            evidence_kind=self._evidence_kind(record),
            strength=self._strength(record, observations),
            locators=self._locators_for(record),
            provider_run_ids=sorted({item.provider_run_id for item in observations}),
            selection_reasons=sorted(
                selection.reasons.values(),
                key=lambda item: (
                    _REASON_ORDER[item.code],
                    item.relationship_id or "",
                    item.subject_id,
                    item.object_id or "",
                    item.message,
                ),
            ),
            conflict_ids=sorted(selection.conflict_ids),
            unknown_ids=sorted(selection.unknown_ids),
            content_id=content_id,
            required=selection.required,
            distance=selection.distance,
        )

    def _select(
        self,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
        record: _Record,
        *,
        role: EvidenceRole,
        section: DossierSection,
        required: bool,
        distance: int,
        reason: SelectionReason,
    ) -> _Selection:
        key = (record.record_id, role, section)
        selection = selections.get(key)
        if selection is None:
            selection = _Selection(record, role, section, required, distance)
            selections[key] = selection
        else:
            selection.required = selection.required or required
            selection.distance = min(selection.distance, distance)
        self._add_reason(selection, reason)
        return selection

    @staticmethod
    def _add_reason(selection: _Selection, reason: SelectionReason) -> None:
        key = (reason.code.value, reason.subject_id, reason.object_id, reason.relationship_id)
        selection.reasons[key] = reason

    @staticmethod
    def _reason(
        code: SelectionReasonCode,
        subject_id: str,
        *,
        object_id: str | None = None,
        relationship_id: str | None = None,
        message: str,
    ) -> SelectionReason:
        return SelectionReason(
            code=code,
            subject_id=subject_id,
            object_id=object_id,
            relationship_id=relationship_id,
            message=message,
        )

    def _resolve_target(self, selector: Any) -> ResolvedTarget:
        if selector.identity is not None:
            matches = self._identity_matches(selector.kind, selector.identity, selector.source_state_id)
        else:
            matches = self._locator_matches(selector.kind, selector.locator, selector.source_state_id)
        if matches:
            status = TargetResolutionStatus.EXACT if len(matches) == 1 else TargetResolutionStatus.AMBIGUOUS
            return ResolvedTarget(
                selector=selector,
                status=status,
                resolved_ids=matches,
                message=(
                    "Target resolved exactly."
                    if status is TargetResolutionStatus.EXACT
                    else f"Target has {len(matches)} candidates; none was selected."
                ),
            )
        alias = self._alias_match(selector)
        if alias is not None:
            status = {
                IdentityResolutionStatus.EXACT: TargetResolutionStatus.EXACT,
                IdentityResolutionStatus.AMBIGUOUS: TargetResolutionStatus.AMBIGUOUS,
                IdentityResolutionStatus.UNRESOLVED: TargetResolutionStatus.UNRESOLVED,
                IdentityResolutionStatus.CONFLICTING: TargetResolutionStatus.CONFLICTING,
            }[alias.resolution]
            return ResolvedTarget(
                selector=selector,
                status=status,
                resolved_ids=sorted(item.entity_id for item in alias.candidates),
                message=alias.rationale,
            )
        return ResolvedTarget(
            selector=selector,
            status=TargetResolutionStatus.UNRESOLVED,
            resolved_ids=[],
            message=f"No {selector.kind.value} target matched the supplied identity or locator.",
        )

    def _repository_resolution(self) -> ResolvedTarget:
        current_state = self.context.source_state_ids[-1]
        selector = next(
            (
                ResolvedTarget(
                    selector=_repository_selector(self.context.repository_id),
                    status=TargetResolutionStatus.EXACT,
                    resolved_ids=[record.entity_id],
                    message="The review covers this repository checkout.",
                )
                for record in self._entities.values()
                if isinstance(record, RepositoryEntity) and record.source_state_id == current_state
            ),
            None,
        )
        if selector is not None:
            return selector
        return ResolvedTarget(
            selector=_repository_selector(self.context.repository_id),
            status=TargetResolutionStatus.EXACT,
            resolved_ids=[current_state],
            message="Repository orientation selected the exact source-state record.",
        )

    def _identity_matches(self, kind: TargetKind, identity: str, source_state_id: str | None) -> list[str]:
        direct = self._records.get(identity)
        matches: set[str] = set()
        if direct is not None and self._kind_matches(kind, direct):
            matches.add(identity)
        if kind is TargetKind.REPOSITORY:
            matches.update(
                entity.entity_id
                for entity in self._entities.values()
                if isinstance(entity, RepositoryEntity) and entity.repository_id == identity
            )
        if kind is TargetKind.REVISION:
            matches.update(
                state.state_id
                for state in self._state_records()
                if state.revision == identity or state.state_id == identity
            )
        return self._state_filter(matches, source_state_id)

    def _locator_matches(self, kind: TargetKind, locator: Any, source_state_id: str | None) -> list[str]:
        if locator is None:
            return []
        matches: set[str] = set()
        test_files: set[str] = set()
        for record in self._records.values():
            if not self._kind_matches(kind, record):
                continue
            value = record.value
            if kind is TargetKind.TEST and isinstance(value, FileEntity) and locator.path == value.path:
                test_files.add(record.record_id)
            elif kind is TargetKind.FILE and isinstance(value, FileEntity) and locator.path == value.path:
                matches.add(record.record_id)
            elif kind is TargetKind.SYMBOL and isinstance(value, SymbolEntity):
                if locator.qualified_name == value.qualified_name or (
                    locator.qualified_name is None and locator.name in {value.name, value.display_name}
                ):
                    matches.add(record.record_id)
            elif kind is TargetKind.DOCUMENTATION_SECTION and isinstance(value, DocumentationEntity):
                if locator.heading is not None and locator.heading == value.heading and self._record_has_path(
                    record, locator.path
                ):
                    matches.add(record.record_id)
            elif kind is TargetKind.DOCUMENTATION_SECTION and isinstance(value, FileEntity):
                if locator.heading is None and locator.path == value.path:
                    matches.add(record.record_id)
            elif kind is TargetKind.DIAGNOSTIC and isinstance(value, DiagnosticEntity):
                if locator.rule_id == value.rule_id or locator.name == value.display_name:
                    matches.add(record.record_id)
            elif kind is TargetKind.REVISION and isinstance(value, SourceStateRecord):
                if locator.revision in {value.revision, value.state_id}:
                    matches.add(record.record_id)
            elif kind is TargetKind.EXTERNAL_DEPENDENCY:
                if isinstance(value, DependencyEntity) and locator.name == value.package:
                    matches.add(record.record_id)
                elif isinstance(value, ExternalEntity) and locator.external_identity == value.external_identity:
                    matches.add(record.record_id)
            elif kind is TargetKind.RANGE and record.kind in {"entity", "location"}:
                if self._record_intersects(record, locator):
                    matches.add(record.record_id)
            elif self._generic_locator_match(value, locator):
                matches.add(record.record_id)
        # A path-only test target names the test file as a review boundary;
        # test-intent records inside it remain reachable supporting evidence.
        if kind is TargetKind.TEST and test_files and locator.name is None:
            matches = test_files
        return self._state_filter(matches, source_state_id)

    def _alias_match(self, selector: Any) -> AliasRecord | None:
        values = {selector.identity} if selector.identity is not None else set()
        if selector.locator is not None:
            values.update(
                value
                for value in (
                    selector.locator.name,
                    selector.locator.qualified_name,
                    selector.locator.path,
                    selector.locator.external_identity,
                )
                if value is not None
            )
        candidates = [
            alias
            for alias in self._aliases.values()
            if alias.value in values
            and (selector.source_state_id is None or alias.source_state_id == selector.source_state_id)
        ]
        return sorted(candidates, key=lambda item: item.alias_id)[0] if candidates else None

    def _kind_matches(self, kind: TargetKind, record: _Record) -> bool:
        value = record.value
        mapping: dict[TargetKind, tuple[type[Any], ...]] = {
            TargetKind.REPOSITORY: (RepositoryEntity,),
            TargetKind.FILE: (FileEntity,),
            TargetKind.SYMBOL: (SymbolEntity,),
            TargetKind.RANGE: (RangeEntity,),
            TargetKind.DOCUMENTATION_SECTION: (DocumentationEntity,),
            TargetKind.TEST: (TestEntity,),
            TargetKind.CONFIGURATION: (ConfigurationEntity,),
            TargetKind.DATA: (DataEntity,),
            TargetKind.WORKFLOW: (WorkflowEntity,),
            TargetKind.ARTIFACT: (ArtifactEntity,),
            TargetKind.DIAGNOSTIC: (DiagnosticEntity,),
            TargetKind.DUPLICATE_CANDIDATE: (CandidateRecord, SimilarityEntity),
            TargetKind.REVISION: (SourceStateRecord,),
            TargetKind.EXTERNAL_DEPENDENCY: (DependencyEntity, ExternalEntity),
        }
        if isinstance(value, mapping[kind]):
            if kind is TargetKind.DUPLICATE_CANDIDATE and isinstance(value, CandidateRecord):
                return value.kind is CandidateKind.DUPLICATION
            return True
        if isinstance(value, FileEntity):
            roles = set(value.roles)
            return (
                (kind is TargetKind.TEST and "test" in roles)
                or (kind is TargetKind.CONFIGURATION and "configuration" in roles)
                or (kind is TargetKind.DOCUMENTATION_SECTION and "documentation" in roles)
            )
        return False

    def _role_for_record(self, record: _Record) -> EvidenceRole:
        value = record.value
        if isinstance(value, SourceStateRecord):
            return EvidenceRole.STATE
        if isinstance(value, RepositoryEntity):
            return EvidenceRole.TOPOLOGY
        if isinstance(value, (SymbolEntity, RangeEntity, FileEntity)):
            if isinstance(value, FileEntity):
                roles = set(value.roles)
                if "test" in roles:
                    return EvidenceRole.TEST
                if "documentation" in roles:
                    return EvidenceRole.DOCUMENTATION
                if "configuration" in roles:
                    return EvidenceRole.CONFIGURATION
            return EvidenceRole.DEFINITION
        if isinstance(value, DocumentationEntity):
            return EvidenceRole.DOCUMENTATION
        if isinstance(value, ConfigurationEntity):
            return EvidenceRole.CONFIGURATION
        if isinstance(value, TestEntity):
            return EvidenceRole.TEST
        if isinstance(value, DataEntity):
            return EvidenceRole.DATA
        if isinstance(value, WorkflowEntity):
            return EvidenceRole.WORKFLOW
        if isinstance(value, ArtifactEntity):
            return EvidenceRole.ARTIFACT
        if isinstance(value, (DependencyEntity, ExternalEntity)):
            return EvidenceRole.DEPENDENCY
        if isinstance(value, DiagnosticEntity):
            return EvidenceRole.DIAGNOSTIC
        if isinstance(value, RuntimeEntity):
            return EvidenceRole.RUNTIME
        if isinstance(value, SimilarityEntity):
            return EvidenceRole.DUPLICATE_CANDIDATE
        if isinstance(value, ContractRecord):
            return EvidenceRole.CONTRACT
        if isinstance(value, CandidateRecord):
            return (
                EvidenceRole.DUPLICATE_CANDIDATE
                if value.kind is CandidateKind.DUPLICATION
                else EvidenceRole.ALTERNATIVE
            )
        if isinstance(value, LineageRecord):
            return EvidenceRole.CHANGE
        if record.kind == "conflict":
            return EvidenceRole.CONFLICT
        return EvidenceRole.SUPPORTING_CONTEXT

    def _relationship_selection_role(
        self,
        record: _Record,
        relationship_role: EvidenceRole,
    ) -> EvidenceRole:
        """Keep a neighbour's evidence purpose when a generic edge would erase it."""
        semantic_role = self._role_for_record(record)
        if semantic_role in {
            EvidenceRole.TEST,
            EvidenceRole.DOCUMENTATION,
            EvidenceRole.CONFIGURATION,
            EvidenceRole.DATA,
            EvidenceRole.WORKFLOW,
            EvidenceRole.ARTIFACT,
            EvidenceRole.DIAGNOSTIC,
            EvidenceRole.RUNTIME,
        }:
            return semantic_role
        return relationship_role

    def _selection_key(self, profile: DossierProfile, selection: _Selection) -> tuple[str, ...]:
        reason_rank = min(_REASON_ORDER[item.code] for item in selection.reasons.values())
        strength = self._strength(selection.record, self._observations_for(selection.record))
        repository_rank, repository_path = self._repository_record_rank(
            selection.record,
            selection.role,
        )
        return (
            f"{_SECTION_ORDER[selection.section]:02d}",
            f"{self._role_position(profile, selection.role):03d}",
            "0" if selection.required else "1",
            f"{reason_rank:02d}",
            f"{_STRENGTH_ORDER[strength]:02d}",
            f"{selection.distance:04d}",
            f"{self._relationship_rank(selection):02d}",
            f"{repository_rank:02d}",
            repository_path,
            selection.record.record_id,
            selection.role.value,
        )

    def _relationship_rank(self, selection: _Selection) -> int:
        categories = {
            self._edges[item].category
            for item in selection.relationship_ids
            if item in self._edges
        }
        if categories.intersection(
            {
                RelationshipCategory.CALL,
                RelationshipCategory.DOCUMENTATION,
                RelationshipCategory.REFERENCE,
                RelationshipCategory.TEST,
            }
        ):
            return 0
        if RelationshipCategory.DEPENDENCY in categories:
            return 1
        if RelationshipCategory.STRUCTURE in categories:
            return 2
        return 3

    @staticmethod
    def _role_position(profile: DossierProfile, role: EvidenceRole) -> int:
        try:
            return _PROFILE_ROLE_ORDER[profile].index(role)
        except ValueError:
            return len(_PROFILE_ROLE_ORDER[profile])

    def _evidence_kind(self, record: _Record) -> str:
        value = record.value
        for attribute in ("entity_type", "record_type", "kind"):
            result = getattr(value, attribute, None)
            if result is not None:
                return result.value if isinstance(result, Enum) else str(result)
        return record.kind

    def _observations_for(self, record: _Record) -> list[ObservationRecord]:
        ids: set[str] = set()
        observations = list(self._observations_by_record.get(record.record_id, []))
        if isinstance(record.value, CandidateRecord):
            observations.extend(self._observations[item] for item in record.value.observation_ids)
        unique = []
        for observation in sorted(observations, key=lambda item: item.observation_id):
            if observation.observation_id not in ids:
                ids.add(observation.observation_id)
                unique.append(observation)
        return unique

    @staticmethod
    def _strength(record: _Record, observations: list[ObservationRecord]) -> EvidenceStrength:
        value = record.value
        declared = getattr(value, "strength", None)
        strengths = [item.strength for item in observations]
        if isinstance(declared, EvidenceStrength):
            strengths.append(declared)
        return min(strengths, key=lambda item: _STRENGTH_ORDER[item]) if strengths else EvidenceStrength.UNKNOWN

    def _provider_run_ids(self, record: _Record) -> list[str]:
        direct = set(getattr(record.value, "provider_run_ids", []))
        return sorted(direct | {item.provider_run_id for item in self._observations_for(record)})

    def _locators_for(self, record: _Record) -> list[EvidenceLocator]:
        location_ids: list[str] = list(getattr(record.value, "location_ids", []))
        if isinstance(record.value, ContractRecord):
            location_ids.extend(record.value.declaration_location_ids)
        if record.kind == "observation":
            location_ids.extend(record.value.location_ids)
        if isinstance(record.value, LocationRecord):
            location_ids.append(record.value.location_id)
        locators = []
        for location_id in sorted(set(location_ids)):
            location = self._locations[location_id]
            source_range = location.source_range
            locators.append(
                EvidenceLocator(
                    location_id=location.location_id,
                    path=location.path,
                    external_locator=location.opaque_locator,
                    start_line=source_range.start.line if source_range else None,
                    start_column=source_range.start.column if source_range else None,
                    end_line=source_range.end.line if source_range else None,
                    end_column=source_range.end.column if source_range else None,
                )
            )
        return locators

    def _record_has_path(self, record: _Record, path: str | None) -> bool:
        if path is None:
            return True
        return any(item.path == path for item in self._locators_for(record))

    def _record_intersects(self, record: _Record, locator: Any) -> bool:
        if locator.path is None or locator.start_line is None:
            return False
        query_start = (locator.start_line, locator.start_column or 0)
        query_end = (
            (locator.end_line, locator.end_column or 0)
            if locator.end_line is not None
            else (locator.start_line + 1, 0)
        )
        for item in self._locators_for(record):
            if item.path != locator.path or item.start_line is None:
                continue
            item_start = (item.start_line, item.start_column or 0)
            item_end = (item.end_line or item.start_line, item.end_column or 0)
            if item_start < query_end and query_start < item_end:
                return True
        return False

    def _generic_locator_match(self, value: Any, locator: Any) -> bool:
        names = {
            getattr(value, attribute, None)
            for attribute in (
                "display_name",
                "name",
                "test_name",
                "rule_name",
                "key",
                "package",
                "heading",
            )
        }
        if locator.name is not None and locator.name not in names:
            return False
        return self._record_has_path(self._records[_record_identity(value)], locator.path)

    def _state_filter(self, matches: set[str], source_state_id: str | None) -> list[str]:
        return sorted(
            record_id
            for record_id in matches
            if source_state_id is None or self._records[record_id].source_state_id == source_state_id
        )

    def _state_records(self) -> list[SourceStateRecord]:
        return [record.value for record in self._records.values() if isinstance(record.value, SourceStateRecord)]

    def _limitation_messages(self) -> list[str]:
        messages = {item.summary for item in self._limitations.values()}
        messages.update(
            item.rationale for item in self.context.session_omissions if item.scope.value != "report"
        )
        return sorted(messages)

    def _kernel_omissions(
        self,
        selections: dict[tuple[str, EvidenceRole, DossierSection], _Selection],
    ) -> list[tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]]:
        selected_ids = {item.record.record_id for item in selections.values()}
        result: list[
            tuple[OmissionReason, EvidenceRole | None, bool, list[str], str, _ActionSpec | None]
        ] = []
        for omission in sorted(self._evidence_omissions.values(), key=lambda item: item.omission_id):
            if omission.scope_id is not None and omission.scope_id not in selected_ids:
                continue
            action = (
                _ActionSpec(ExpansionKind.OMISSION, target_id=omission.omission_id) if omission.recoverable else None
            )
            result.append(
                (
                    _omission_reason(omission.reason),
                    None,
                    False,
                    [omission.omission_id],
                    omission.reason,
                    action,
                )
            )
        for session_omission in self.context.session_omissions:
            if session_omission.scope.value == "report":
                continue
            action = (
                _ActionSpec(ExpansionKind.PROVIDER, target_id=session_omission.provider_id)
                if session_omission.provider_id is not None
                else None
            )
            result.append(
                (
                    _session_omission_reason(session_omission.scope.value, session_omission.code),
                    None,
                    False,
                    [session_omission.omission_id],
                    session_omission.rationale,
                    action,
                )
            )
        return result

    def _expansion_mismatch(self, request: DossierRequest) -> str | None:
        expansion = request.expansion
        if expansion is None:
            return "expansion payload is absent"
        base = self._dossiers_by_id.get(expansion.base_dossier_id)
        if base is None:
            return "expansion base dossier is not registered in this query context"
        if (
            base.session_id != self.context.session_id
            or base.session_manifest_digest != self.context.session_manifest_digest
        ):
            return "expansion base dossier belongs to another source session"
        issued = next((item for item in base.expansions if item.action_id == expansion.action_id), None)
        if issued is not None:
            if (issued.kind, issued.target_id, issued.role) != (
                expansion.kind,
                expansion.target_id,
                expansion.role,
            ):
                return "expansion semantics differ from the issued action"
        else:
            expected = expansion_action_core_id(
                kind=expansion.kind,
                target_id=expansion.target_id,
                role=expansion.role,
                base_dossier_id=base.dossier_id,
                session_id=self.context.session_id,
                session_manifest_digest=self.context.session_manifest_digest,
                maximum_incremental_bytes=base.budget_use.payload_bytes.limit,
                cursor=None,
            )
            if expansion.action_id != expected:
                return "expansion action identity is not valid for the base dossier"
        cursor = expansion.cursor
        if expansion.kind is ExpansionKind.CURSOR and cursor is None:
            return "cursor expansion is missing its checked cursor"
        if cursor is None:
            return None
        if issued is None or issued.cursor != cursor:
            return "cursor was not issued by the base dossier"
        if cursor.base_dossier_id != expansion.base_dossier_id:
            return "cursor base dossier differs from the expansion request"
        if cursor.session_id != self.context.session_id:
            return "cursor session differs from the query context"
        if cursor.session_manifest_digest != self.context.session_manifest_digest:
            return "cursor manifest differs from the query context"
        if cursor.policy_digest != self.context.policy_digest:
            return "cursor policy differs from the query context"
        if cursor.query_digest != dossier_query_digest(request):
            return "cursor query semantics differ from the expansion request"
        return None

    def _binding_failure(self, request: DossierRequest, message: str) -> Dossier:
        refresh_spec = _ActionSpec(ExpansionKind.REFRESH, maximum_incremental_bytes=request.budget.max_payload_bytes)
        resolutions = [
            ResolvedTarget(
                selector=selector,
                status=TargetResolutionStatus.UNRESOLVED,
                resolved_ids=[],
                message=f"Target was not evaluated because {message}.",
            )
            for selector in request.targets
        ]
        result = self._assemble(
            request=request,
            resolutions=resolutions,
            selected=[],
            raw_omissions=[
                (
                    OmissionReason.SOURCE_DRIFT,
                    EvidenceRole.STATE,
                    True,
                    [],
                    message,
                    refresh_spec,
                )
            ],
            stop_satisfied=[],
            stop_unsatisfied=[message],
            has_more=False,
            force_status=DossierStatus.BLOCKED,
        )
        size = len(canonical_dossier_bytes(result))
        if size + 32 > request.budget.max_payload_bytes:
            raise DossierQueryError(
                "payload_budget_too_small",
                f"Blocked dossier envelope is {size} bytes; limit is {request.budget.max_payload_bytes}",
                remediation="Raise max_payload_bytes to inspect the failure and refresh action.",
            )
        return self._with_payload_use(result, size)


def build_dossier(context: DossierContext, request: DossierRequest) -> Dossier:
    """Convenience operation for one query; reuse DossierEngine for repeated calls."""
    return DossierEngine(context).query(request)


def _record_identity(value: Any) -> str:
    for name in (
        "entity_id",
        "location_id",
        "edge_id",
        "contract_id",
        "candidate_id",
        "observation_id",
        "alias_id",
        "lineage_id",
        "omission_id",
        "conflict_id",
        "state_id",
    ):
        identity = getattr(value, name, None)
        if identity is not None:
            return str(identity)
    raise ValueError(f"unsupported dossier record: {type(value).__name__}")


def _omission_reason(reason: str) -> OmissionReason:
    normalized = reason.casefold()
    for marker, value in (
        ("ambig", OmissionReason.AMBIGUITY),
        ("stale", OmissionReason.SOURCE_DRIFT),
        ("drift", OmissionReason.SOURCE_DRIFT),
        ("budget", OmissionReason.BUDGET),
        ("policy", OmissionReason.POLICY),
        ("unsupported", OmissionReason.UNSUPPORTED),
        ("failure", OmissionReason.FAILURE),
        ("failed", OmissionReason.FAILURE),
        ("scope", OmissionReason.SCOPE),
    ):
        if marker in normalized:
            return value
    return OmissionReason.PROVIDER_LIMITATION


def _session_omission_reason(scope: str, code: str) -> OmissionReason:
    if scope == "budget":
        return OmissionReason.BUDGET
    if scope == "slice":
        return OmissionReason.POLICY
    if scope == "source" and any(marker in code.casefold() for marker in ("stale", "drift")):
        return OmissionReason.SOURCE_DRIFT
    return _omission_reason(code)


def _observation_target_ids(observation: ObservationRecord) -> list[str]:
    if isinstance(observation, StructuralObservation):
        return [observation.target_id]
    if isinstance(observation, DiagnosticObservation):
        return [observation.diagnostic_entity_id, *observation.subject_entity_ids]
    if isinstance(observation, RuntimeObservation):
        return [observation.runtime_entity_id, *observation.subject_entity_ids]
    if isinstance(observation, SimilarityObservation):
        return [observation.similarity_entity_id, *observation.member_entity_ids]
    return []


def _observation_related_entity_ids(observation: ObservationRecord) -> list[str]:
    if isinstance(observation, DiagnosticObservation):
        return [observation.diagnostic_entity_id]
    if isinstance(observation, RuntimeObservation):
        return [observation.runtime_entity_id]
    if isinstance(observation, SimilarityObservation):
        return [observation.similarity_entity_id, *observation.member_entity_ids]
    return []


def _relationship_role(category: RelationshipCategory, *, outbound: bool) -> EvidenceRole:
    if category is RelationshipCategory.CALL:
        return EvidenceRole.CALLEE if outbound else EvidenceRole.CALLER
    if category in {RelationshipCategory.REFERENCE, RelationshipCategory.DECLARATION}:
        return EvidenceRole.REFERENCE if outbound else EvidenceRole.CONSUMER
    if category in {
        RelationshipCategory.DEPENDENCY,
        RelationshipCategory.IMPLEMENTATION,
        RelationshipCategory.OVERRIDE,
    }:
        return EvidenceRole.DEPENDENCY if outbound else EvidenceRole.CONSUMER
    return {
        RelationshipCategory.TEST: EvidenceRole.TEST,
        RelationshipCategory.DOCUMENTATION: EvidenceRole.DOCUMENTATION,
        RelationshipCategory.CONFIGURATION: EvidenceRole.CONFIGURATION,
        RelationshipCategory.DATA: EvidenceRole.DATA,
        RelationshipCategory.WORKFLOW: EvidenceRole.WORKFLOW,
        RelationshipCategory.ARTIFACT: EvidenceRole.ARTIFACT,
        RelationshipCategory.PROVENANCE: EvidenceRole.PROVENANCE,
        RelationshipCategory.GENERATED: EvidenceRole.ARTIFACT,
        RelationshipCategory.STRUCTURE: EvidenceRole.TOPOLOGY,
    }.get(category, EvidenceRole.SUPPORTING_CONTEXT)


def _identity_digest(identity: str) -> str:
    marker = "sha256:"
    if marker in identity:
        return marker + identity.rsplit(marker, 1)[1]
    return sha256_digest(identity.encode("utf-8"))


def _repository_selector(repository_id: str) -> Any:
    from anatomize.dossiers.models import TargetSelector

    return TargetSelector(kind=TargetKind.REPOSITORY, identity=repository_id)
