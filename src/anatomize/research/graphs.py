"""Portable research workflow, resource, environment, and provenance graph."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from anatomize._artifacts import content_id, sha256_digest
from anatomize.evidence import (
    ContentClass,
    EntityRecord,
    EvidenceModel,
    FileCoordinateSpace,
    FileEntity,
    LocationOrigin,
    LocationRecord,
    RepositoryEvidence,
    SourceStateRecord,
    validate_repository_path,
)
from anatomize.providers import ProviderEnvelope

RESEARCH_GRAPH_TYPE: Literal["anatomize.research-graph"] = "anatomize.research-graph"
RESEARCH_GRAPH_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class ResearchGraphStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ResourceRole(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    DATA = "data"
    REPORT = "report"
    SCRIPT = "script"
    ARCHIVE = "archive"
    OTHER = "other"


class ResourceAvailability(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    REMOTE = "remote"
    OPAQUE = "opaque"
    IGNORED = "ignored"


class ResearchResource(EvidenceModel):
    node_type: Literal["resource"] = "resource"
    node_id: str
    source_state_id: str
    locator_kind: Literal["repository_path", "remote_uri", "opaque"]
    locator: str
    roles: list[ResourceRole] = Field(min_length=1)
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    digest: str | None = None
    generated: bool = False
    content_class: ContentClass = ContentClass.OPAQUE
    availability: ResourceAvailability = ResourceAvailability.OPAQUE
    schema_identity: str | None = None
    content_included: Literal[False] = False

    @model_validator(mode="after")
    def validate_resource(self) -> ResearchResource:
        if self.locator_kind == "repository_path":
            validate_repository_path(self.locator)
        if self.node_id != _node_id(self):
            raise ValueError("research resource identity does not match its stable locator")
        return self


class WorkflowStep(EvidenceModel):
    node_type: Literal["workflow_step"] = "workflow_step"
    node_id: str
    source_state_id: str
    workflow_id: str
    workflow_kind: str
    name: str
    script_locator: str | None = None
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    resources: dict[str, str | int | float | bool] = Field(default_factory=dict)
    dynamic: bool = False

    @field_validator("script_locator")
    @classmethod
    def validate_script(cls, value: str | None) -> str | None:
        if value is not None:
            validate_repository_path(value)
        return value

    @model_validator(mode="after")
    def validate_step(self) -> WorkflowStep:
        if self.node_id != _node_id(self):
            raise ValueError("workflow step identity does not match native workflow identity")
        return self


class EnvironmentDeclaration(EvidenceModel):
    node_type: Literal["environment"] = "environment"
    node_id: str
    source_state_id: str
    environment_kind: str
    name: str
    version: str | None = None
    locator: str | None = None
    digest: str | None = None

    @model_validator(mode="after")
    def validate_environment(self) -> EnvironmentDeclaration:
        if self.locator is not None and "://" not in self.locator:
            validate_repository_path(self.locator)
        if self.node_id != _node_id(self):
            raise ValueError("environment identity does not match its declaration")
        return self


class ResearchDependency(EvidenceModel):
    node_type: Literal["dependency"] = "dependency"
    node_id: str
    source_state_id: str
    ecosystem: str
    package: str
    version: str | None = None
    scope: str | None = None
    source: str | None = None
    digest: str | None = None

    @model_validator(mode="after")
    def validate_dependency(self) -> ResearchDependency:
        if self.node_id != _node_id(self):
            raise ValueError("research dependency identity does not match package coordinates")
        return self


class SupplyChainFinding(EvidenceModel):
    node_type: Literal["supply_chain_finding"] = "supply_chain_finding"
    node_id: str
    source_state_id: str
    finding_identity: str
    severity: str
    status: str | None = None
    description_digest: str | None = None

    @model_validator(mode="after")
    def validate_finding(self) -> SupplyChainFinding:
        if self.node_id != _node_id(self):
            raise ValueError("supply-chain finding identity does not match provider identity")
        return self


class ProvenanceContext(EvidenceModel):
    node_type: Literal["provenance_context"] = "provenance_context"
    node_id: str
    source_state_id: str
    identity_scheme: str
    external_identity: str
    context_kind: str
    display_name: str

    @model_validator(mode="after")
    def validate_context(self) -> ProvenanceContext:
        if self.node_id != _node_id(self):
            raise ValueError("provenance context identity does not match external identity")
        return self


ResearchNode = Annotated[
    ResearchResource
    | WorkflowStep
    | EnvironmentDeclaration
    | ResearchDependency
    | SupplyChainFinding
    | ProvenanceContext,
    Field(discriminator="node_type"),
]


class ResearchEdge(EvidenceModel):
    edge_id: str
    source_state_id: str
    source_node_id: str
    target_node_id: str
    predicate: str
    evidence_kind: Literal["declared", "observed"]

    @model_validator(mode="after")
    def validate_edge(self) -> ResearchEdge:
        if self.edge_id != content_id(
            "research-edge",
            self.model_dump(mode="json", exclude={"edge_id"}),
        ):
            raise ValueError("research edge identity does not match its relationship")
        return self


class ResearchGraphArtifact(EvidenceModel):
    artifact_type: Literal["anatomize.research-graph"] = RESEARCH_GRAPH_TYPE
    schema_version: Literal["1.0.0"] = RESEARCH_GRAPH_SCHEMA_VERSION
    repository_id: str
    source_state_id: str
    provider_run_id: str
    provider_id: str
    provider_version: str
    configuration_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence_families: list[str] = Field(min_length=1)
    status: ResearchGraphStatus
    nodes: list[ResearchNode]
    edges: list[ResearchEdge]
    limitations: list[str] = Field(default_factory=list)
    source_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_graph(self) -> ResearchGraphArtifact:
        node_ids = [item.node_id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("research graph node identities must be unique")
        edge_ids = [item.edge_id for item in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("research graph edge identities must be unique")
        unknown = sorted(
            {
                endpoint
                for edge in self.edges
                for endpoint in (edge.source_node_id, edge.target_node_id)
                if endpoint not in node_ids
            }
        )
        if unknown:
            raise ValueError(f"research graph edges reference unknown nodes: {unknown}")
        if self.status is ResearchGraphStatus.UNAVAILABLE and (self.nodes or self.edges):
            raise ValueError("unavailable research graph cannot claim nodes or edges")
        if self.status is ResearchGraphStatus.UNAVAILABLE and not self.limitations:
            raise ValueError("unavailable research graph must explain the unavailable evidence")
        if self.status is ResearchGraphStatus.COMPLETE and self.limitations:
            raise ValueError("complete research graph cannot carry unresolved limitations")
        if self.evidence_families != sorted(set(self.evidence_families)):
            raise ValueError("research graph evidence families must be sorted and unique")
        return self


class ResearchLineageDossier(EvidenceModel):
    """Smallest auditable upstream slice for one workflow-produced resource."""

    dossier_id: str
    repository_id: str
    source_state_id: str
    target_node_id: str
    node_ids: list[str]
    edge_ids: list[str]
    status: ResearchGraphStatus
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dossier(self) -> ResearchLineageDossier:
        if self.target_node_id not in self.node_ids:
            raise ValueError("research lineage dossier must contain its target")
        expected = content_id(
            "research-lineage-dossier",
            {
                "repository": self.repository_id,
                "state": self.source_state_id,
                "target": self.target_node_id,
                "nodes": self.node_ids,
                "edges": self.edge_ids,
            },
        )
        if self.dossier_id != expected:
            raise ValueError("research lineage dossier identity does not match its graph slice")
        return self


_ResearchNodeT = TypeVar(
    "_ResearchNodeT",
    ResearchResource,
    WorkflowStep,
    EnvironmentDeclaration,
    ResearchDependency,
    SupplyChainFinding,
    ProvenanceContext,
)


def build_research_node(model: type[_ResearchNodeT], **values: Any) -> _ResearchNodeT:
    provisional = model.model_construct(node_id="pending", **values)
    return model(node_id=_node_id(provisional), **values)


def build_research_edge(**values: Any) -> ResearchEdge:
    provisional = ResearchEdge.model_construct(edge_id="pending", **values)
    return ResearchEdge(
        edge_id=content_id("research-edge", provisional.model_dump(mode="json", exclude={"edge_id"})),
        **values,
    )


def build_research_graph(
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_id: str,
    provider_version: str,
    source_artifact: bytes,
    nodes: list[ResearchNode],
    edges: list[ResearchEdge],
    limitations: set[str] | list[str],
    evidence_families: list[str],
) -> ResearchGraphArtifact:
    """Seal an imported research artifact into one deterministic graph."""
    normalized_limitations = sorted(set(limitations))
    return ResearchGraphArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id=provider_id,
        provider_version=provider_version,
        configuration_digest=sha256_digest(f"{provider_id}:1".encode()),
        evidence_families=sorted(set(evidence_families)),
        status=ResearchGraphStatus.PARTIAL if normalized_limitations else ResearchGraphStatus.COMPLETE,
        nodes=sorted(nodes, key=lambda item: item.node_id),
        edges=sorted(edges, key=lambda item: item.edge_id),
        limitations=normalized_limitations,
        source_artifact_digest=sha256_digest(source_artifact),
    )


def unavailable_research_graph(
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_id: str,
    provider_version: str,
    evidence_families: list[str],
    reason: str,
) -> ResearchGraphArtifact:
    """Represent an absent tool or artifact without erasing the attempted evidence family."""
    return ResearchGraphArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id=provider_id,
        provider_version=provider_version,
        configuration_digest=sha256_digest(f"{provider_id}:unavailable:1".encode()),
        evidence_families=sorted(set(evidence_families)),
        status=ResearchGraphStatus.UNAVAILABLE,
        nodes=[],
        edges=[],
        limitations=[reason],
        source_artifact_digest=sha256_digest(b""),
    )


def workflow_lineage_dossier(
    artifact: ResearchGraphArtifact,
    *,
    target_node_id: str,
) -> ResearchLineageDossier:
    """Return a deterministic upstream review slice without claiming runtime causality."""
    nodes = {node.node_id: node for node in artifact.nodes}
    if target_node_id not in nodes:
        raise ValueError(f"research lineage target is absent: {target_node_id}")
    selected_nodes = {target_node_id}
    selected_edges: set[str] = set()
    pending = [target_node_id]
    while pending:
        current = pending.pop()
        current_node = nodes[current]
        candidates = []
        for edge in artifact.edges:
            include = False
            next_node: str | None = None
            if edge.predicate == "produces" and edge.target_node_id == current:
                include, next_node = True, edge.source_node_id
            elif isinstance(current_node, WorkflowStep) and edge.source_node_id == current:
                if edge.predicate in {"consumes", "depends_on", "implemented_by", "uses_environment"}:
                    include, next_node = True, edge.target_node_id
            elif edge.predicate == "generated_from" and edge.source_node_id == current:
                include, next_node = True, edge.target_node_id
            if include and next_node is not None:
                candidates.append((edge.edge_id, next_node))
        for edge_id, next_node in sorted(candidates):
            selected_edges.add(edge_id)
            if next_node not in selected_nodes:
                selected_nodes.add(next_node)
                pending.append(next_node)
    limitations = list(artifact.limitations)
    selected_resources: list[ResearchResource] = []
    for node_id in selected_nodes:
        selected = nodes[node_id]
        if isinstance(selected, ResearchResource):
            selected_resources.append(selected)
    unavailable = sorted(
        resource.locator
        for resource in selected_resources
        if resource.availability is not ResourceAvailability.AVAILABLE
    )
    if unavailable:
        limitations.append(f"Lineage includes unavailable resources: {', '.join(unavailable)}")
    limitations = sorted(set(limitations))
    status = ResearchGraphStatus.COMPLETE if not limitations else ResearchGraphStatus.PARTIAL
    node_ids = sorted(selected_nodes)
    edge_ids = sorted(selected_edges)
    identity = {
        "repository": artifact.repository_id,
        "state": artifact.source_state_id,
        "target": target_node_id,
        "nodes": node_ids,
        "edges": edge_ids,
    }
    return ResearchLineageDossier(
        dossier_id=content_id("research-lineage-dossier", identity),
        repository_id=artifact.repository_id,
        source_state_id=artifact.source_state_id,
        target_node_id=target_node_id,
        node_ids=node_ids,
        edge_ids=edge_ids,
        status=status,
        limitations=limitations,
    )


def research_graph_provider_envelope(
    artifact: ResearchGraphArtifact,
    *,
    baseline: RepositoryEvidence,
    policy_digest: str,
) -> ProviderEnvelope:
    """Normalize a research graph into canonical entities, edges, and observations."""
    from anatomize.evidence import (
        CompletenessRecord,
        CompletenessStatus,
        EdgeRecord,
        EvidenceStrength,
        ObservationStance,
        ProviderRunStatus,
        RelationshipCategory,
        StructuralObservation,
    )
    from anatomize.providers import (
        AuthorityLevel,
        InvocationAuthority,
        InvocationMode,
        ProviderBatchBuilder,
        ProviderScope,
        ProviderToolIdentity,
        build_provider_envelope,
    )

    state = next(item for item in baseline.states if item.state_id == artifact.source_state_id)
    if state.repository_id != artifact.repository_id:
        raise ValueError("research graph repository does not match baseline state")
    builder = ProviderBatchBuilder(baseline)
    repository = builder.repository_entity(state)
    completeness_id = content_id(
        "completeness:research-graph",
        {"run": artifact.provider_run_id},
    )
    for node in artifact.nodes:
        entity = _evidence_entity(
            node,
            location_ids=_research_location_ids(node, builder, state),
        )
        builder.entities[entity.entity_id] = entity
        if isinstance(node, ResearchResource) and (
            node.availability is not ResourceAvailability.AVAILABLE or node.content_class is ContentClass.SENSITIVE
        ):
            reason = (
                f"Resource {node.locator} is {node.availability.value}; content was not imported."
                if node.availability is not ResourceAvailability.AVAILABLE
                else f"Resource {node.locator} is sensitive; content was excluded by policy."
            )
            builder.add_degradation(
                run_id=artifact.provider_run_id,
                state_id=state.state_id,
                code=f"research_resource_{node.availability.value}",
                summary=reason,
                scope_type="entity",
                scope_id=entity.entity_id,
                remediation="Supply separately authorized metadata or content only when the review requires it.",
                discriminator=node.node_id,
            )
        observation = StructuralObservation(
            observation_id=content_id(
                "observation:research-node",
                {"run": artifact.provider_run_id, "node": node.node_id},
            ),
            source_state_id=state.state_id,
            provider_run_id=artifact.provider_run_id,
            method=artifact.provider_id,
            method_version=artifact.provider_version,
            strength=EvidenceStrength.DECLARED,
            stance=ObservationStance.SUPPORTS,
            completeness_id=completeness_id,
            rationale="Provider artifact declares this research entity; execution is not inferred.",
            target_type="entity",
            target_id=entity.entity_id,
        )
        builder.observations[observation.observation_id] = observation
    categories = {
        "consumes": RelationshipCategory.DATA,
        "produces": RelationshipCategory.ARTIFACT,
        "depends_on": RelationshipCategory.WORKFLOW,
        "uses_environment": RelationshipCategory.CONFIGURATION,
        "generated_from": RelationshipCategory.GENERATED,
        "has_part": RelationshipCategory.STRUCTURE,
        "is_part_of": RelationshipCategory.STRUCTURE,
        "declares_dependency": RelationshipCategory.DEPENDENCY,
        "implemented_by": RelationshipCategory.IMPLEMENTATION,
        "affects": RelationshipCategory.DEPENDENCY,
    }
    for item in artifact.edges:
        edge = EdgeRecord(
            edge_id=item.edge_id,
            source_state_id=state.state_id,
            source_entity_id=item.source_node_id,
            target_entity_id=item.target_node_id,
            category=categories.get(item.predicate, RelationshipCategory.PROVENANCE),
            predicate=item.predicate,
        )
        builder.edges[edge.edge_id] = edge
        observation = StructuralObservation(
            observation_id=content_id(
                "observation:research-edge",
                {"run": artifact.provider_run_id, "edge": edge.edge_id},
            ),
            source_state_id=state.state_id,
            provider_run_id=artifact.provider_run_id,
            method=artifact.provider_id,
            method_version=artifact.provider_version,
            strength=(EvidenceStrength.DECLARED if item.evidence_kind == "declared" else EvidenceStrength.EXACT),
            stance=ObservationStance.SUPPORTS,
            completeness_id=completeness_id,
            rationale=f"{item.evidence_kind.title()} research relationship from captured artifact.",
            target_type="edge",
            target_id=edge.edge_id,
        )
        builder.observations[observation.observation_id] = observation
    for index, limitation in enumerate(artifact.limitations):
        builder.add_degradation(
            run_id=artifact.provider_run_id,
            state_id=state.state_id,
            code="research_provider_limitation",
            summary=limitation,
            scope_type="repository",
            scope_id=repository.entity_id,
            remediation="Regenerate a complete provider artifact or narrow the review claim.",
            discriminator=str(index),
        )
    status_map = {
        ResearchGraphStatus.COMPLETE: CompletenessStatus.COMPLETE,
        ResearchGraphStatus.PARTIAL: CompletenessStatus.PARTIAL,
        ResearchGraphStatus.UNAVAILABLE: CompletenessStatus.UNAVAILABLE,
        ResearchGraphStatus.UNKNOWN: CompletenessStatus.UNKNOWN,
    }
    completeness = CompletenessRecord(
        completeness_id=completeness_id,
        source_state_id=state.state_id,
        provider_run_id=artifact.provider_run_id,
        scope_type="repository",
        scope_id=repository.entity_id,
        evidence_families=artifact.evidence_families,
        status=status_map[artifact.status],
        omission_ids=sorted(builder.omissions),
    )
    run_status = {
        ResearchGraphStatus.COMPLETE: ProviderRunStatus.COMPLETE,
        ResearchGraphStatus.PARTIAL: ProviderRunStatus.PARTIAL,
        ResearchGraphStatus.UNAVAILABLE: ProviderRunStatus.UNAVAILABLE,
        ResearchGraphStatus.UNKNOWN: ProviderRunStatus.PARTIAL,
    }[artifact.status]
    return build_provider_envelope(
        provider_run_id=artifact.provider_run_id,
        provider_id=artifact.provider_id,
        provider_version=artifact.provider_version,
        tool=ProviderToolIdentity(name=artifact.provider_id, version=artifact.provider_version),
        capabilities=artifact.evidence_families,
        languages=[],
        repository_id=artifact.repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=artifact.configuration_digest,
        scope=ProviderScope(
            scope_id=content_id("provider-scope:research", {"run": artifact.provider_run_id}),
            source_state_ids=[state.state_id],
            paths=sorted(builder.paths),
            entity_ids=sorted(builder.entities),
            evidence_families=artifact.evidence_families,
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.ARTIFACT_IMPORT,
            level=AuthorityLevel.A1_ARTIFACT,
            policy_digest=policy_digest,
        ),
        status=run_status,
        payload=builder.build_payload([completeness]),
    )


def _evidence_entity(node: ResearchNode, *, location_ids: list[str]) -> EntityRecord:
    from anatomize.evidence import (
        ArtifactEntity,
        ConfigurationEntity,
        DataEntity,
        DependencyEntity,
        DiagnosticEntity,
        ExternalEntity,
        WorkflowEntity,
    )

    common = {
        "entity_id": node.node_id,
        "source_state_id": node.source_state_id,
        "location_ids": location_ids,
    }
    if isinstance(node, WorkflowStep):
        return WorkflowEntity(
            **common,
            display_name=node.name,
            workflow_kind=node.workflow_kind,
            rule_name=node.name,
        )
    if isinstance(node, ResearchResource):
        if not node.generated and set(node.roles).issubset({ResourceRole.INPUT, ResourceRole.DATA}):
            return DataEntity(
                **common,
                display_name=node.locator,
                data_kind="/".join(sorted(role.value for role in node.roles)),
                media_type=node.media_type,
                content_class=node.content_class,
                schema_identity=node.schema_identity,
            )
        return ArtifactEntity(
            **common,
            display_name=node.locator,
            artifact_kind="/".join(sorted(role.value for role in node.roles)),
            digest=node.digest,
            media_type=node.media_type,
            generated=node.generated,
        )
    if isinstance(node, EnvironmentDeclaration):
        return ConfigurationEntity(
            **common,
            display_name=node.name,
            configuration_kind=node.environment_kind,
            key=node.locator or node.name,
        )
    if isinstance(node, ResearchDependency):
        return DependencyEntity(
            **common,
            display_name=node.package,
            ecosystem=node.ecosystem,
            package=node.package,
            version=node.version,
            scope=node.scope,
        )
    if isinstance(node, SupplyChainFinding):
        return DiagnosticEntity(
            **common,
            display_name=node.finding_identity,
            rule_id=node.finding_identity,
            severity=node.severity,
            message=f"Captured supply-chain finding {node.finding_identity}",
        )
    return ExternalEntity(
        **common,
        display_name=node.display_name,
        identity_scheme=node.identity_scheme,
        external_identity=node.external_identity,
        entity_kind=node.context_kind,
    )


def _research_location_ids(
    node: ResearchNode,
    builder: Any,
    state: SourceStateRecord,
) -> list[str]:
    paths: list[str] = []
    if isinstance(node, ResearchResource) and node.locator_kind == "repository_path":
        paths.append(node.locator)
    elif isinstance(node, WorkflowStep) and node.script_locator is not None:
        paths.append(node.script_locator)
    elif isinstance(node, EnvironmentDeclaration) and node.locator is not None and "://" not in node.locator:
        paths.append(node.locator)
    location_ids: list[str] = []
    for path in sorted(set(paths)):
        builder.paths.add(path)
        existing = next(
            (
                entity
                for entity in builder.baseline.entities
                if isinstance(entity, FileEntity)
                and entity.source_state_id == state.state_id
                and entity.path == path
            ),
            None,
        )
        if existing is not None:
            builder.include_baseline_entity(existing)
            location_ids.extend(existing.location_ids)
            continue
        file_id = content_id(
            "entity:research-file",
            {"state": state.state_id, "path": path, "claim": node.node_id},
        )
        location_id = content_id(
            "location:research-file",
            {"state": state.state_id, "path": path, "claim": node.node_id},
        )
        if isinstance(node, ResearchResource):
            roles = sorted(role.value for role in node.roles)
            size_bytes = node.size_bytes or 0
            digest = node.digest
            content_class = node.content_class
        elif isinstance(node, EnvironmentDeclaration):
            roles = ["configuration"]
            size_bytes = 0
            digest = node.digest
            content_class = ContentClass.ORDINARY
        else:
            roles = ["workflow"]
            size_bytes = 0
            digest = None
            content_class = ContentClass.ORDINARY
        builder.locations[location_id] = LocationRecord(
            location_id=location_id,
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id=file_id,
            path=path,
            coordinate_space=FileCoordinateSpace(),
        )
        # This file is a provider claim (not a baseline fact), so its identity
        # includes the exact research node that asserted it. Conflicting roles
        # or digests remain separate evidence instead of colliding on path.
        builder.entities[file_id] = FileEntity(
            entity_id=file_id,
            source_state_id=state.state_id,
            display_name=path,
            location_ids=[location_id],
            path=path,
            digest=digest,
            size_bytes=size_bytes,
            roles=roles,
            content_class=content_class,
        )
        location_ids.append(location_id)
    return sorted(set(location_ids))


def _node_id(node: ResearchNode) -> str:
    identity: dict[str, object]
    if isinstance(node, ResearchResource):
        identity = {
            "state": node.source_state_id,
            "type": node.node_type,
            "locator_kind": node.locator_kind,
            "locator": node.locator,
            "roles": sorted(role.value for role in node.roles),
            "media_type": node.media_type,
            "digest": node.digest,
            "generated": node.generated,
            "content_class": node.content_class.value,
            "schema_identity": node.schema_identity,
        }
    elif isinstance(node, WorkflowStep):
        identity = {
            "state": node.source_state_id,
            "type": node.node_type,
            "workflow": node.workflow_id,
            "name": node.name,
        }
    elif isinstance(node, EnvironmentDeclaration):
        identity = {
            "state": node.source_state_id,
            "type": node.node_type,
            "kind": node.environment_kind,
            "name": node.name,
            "locator": node.locator,
        }
    elif isinstance(node, ResearchDependency):
        identity = {
            "state": node.source_state_id,
            "type": node.node_type,
            "ecosystem": node.ecosystem,
            "package": node.package,
            "version": node.version,
        }
    elif isinstance(node, SupplyChainFinding):
        identity = {
            "state": node.source_state_id,
            "type": node.node_type,
            "finding": node.finding_identity,
        }
    else:
        identity = {
            "state": node.source_state_id,
            "type": node.node_type,
            "scheme": node.identity_scheme,
            "identity": node.external_identity,
        }
    return content_id("research-node", identity)
