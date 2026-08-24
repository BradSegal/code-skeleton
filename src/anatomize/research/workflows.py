"""Artifact-only adapters for Snakemake and targets workflow metadata."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from anatomize._artifacts import JsonLimits, compact_json, parse_bounded_json_object, sha256_digest
from anatomize._errors import AnatomizeError
from anatomize.evidence import ContentClass
from anatomize.research.graphs import (
    EnvironmentDeclaration,
    ResearchEdge,
    ResearchGraphArtifact,
    ResearchNode,
    ResearchResource,
    ResourceAvailability,
    ResourceRole,
    WorkflowStep,
    build_research_edge,
    build_research_graph,
    build_research_node,
)

_MAX_WORKFLOW_BYTES = 32 * 1024 * 1024


class ResearchArtifactError(AnatomizeError):
    """Stable, actionable failure for an imported research metadata artifact."""

@dataclass
class _ResourceDraft:
    locator: str
    locator_kind: str
    roles: set[ResourceRole] = field(default_factory=set)
    media_type: str | None = None
    size_bytes: int | None = None
    digest: str | None = None
    generated: bool = False
    content_class: ContentClass = ContentClass.OPAQUE
    availability: ResourceAvailability = ResourceAvailability.OPAQUE
    schema_identity: str | None = None


def parse_snakemake_workflow(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
) -> ResearchGraphArtifact:
    """Parse a bounded, content-free Snakemake workflow summary artifact.

    The accepted artifact is an explicit adapter contract, not a Snakefile parser and
    not proof that Snakemake executed. Each rule may declare ``input``, ``output``,
    ``report``, ``script``, ``params``, ``resources``, ``environment``, ``depends_on``,
    and ``dynamic`` fields.
    """
    try:
        payload = parse_bounded_json_object(raw, limits=JsonLimits(max_bytes=_MAX_WORKFLOW_BYTES))
    except ValueError as error:
        raise ResearchArtifactError(
            "snakemake_artifact_invalid",
            str(error),
            remediation="Export a bounded Snakemake summary object and retry.",
        ) from error
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise ResearchArtifactError(
            "snakemake_rules_missing",
            "Snakemake artifact requires a rules array",
            remediation="Include one metadata object per declared rule.",
        )
    workflow_id = _required_string(payload.get("workflow_id"), "workflow_id")
    nodes: list[ResearchNode] = []
    edges: list[ResearchEdge] = []
    steps: dict[str, WorkflowStep] = {}
    resources: dict[tuple[str, str], _ResourceDraft] = {}
    limitations: set[str] = set()
    pending_dependencies: list[tuple[WorkflowStep, str]] = []
    for index, value in enumerate(rules):
        rule = _mapping(value, f"rules[{index}]")
        name = _required_string(rule.get("name"), f"rules[{index}].name")
        if name in steps:
            raise ResearchArtifactError(
                "snakemake_rule_duplicate",
                f"Duplicate Snakemake rule name: {name}",
                remediation="Preserve one stable record per native rule identity.",
            )
        dynamic = bool(rule.get("dynamic", False))
        if dynamic:
            limitations.add(f"Rule {name} has runtime-dynamic structure that static metadata cannot resolve.")
        step = build_research_node(
            WorkflowStep,
            source_state_id=source_state_id,
            workflow_id=workflow_id,
            workflow_kind="snakemake_rule",
            name=name,
            script_locator=_optional_string(rule.get("script")),
            parameters=_scalar_mapping(rule.get("params"), f"rule {name} params", limitations),
            resources=_scalar_mapping(rule.get("resources"), f"rule {name} resources", limitations),
            dynamic=dynamic,
        )
        steps[name] = step
        nodes.append(step)
        if step.script_locator is not None:
            script = _merge_resource(
                resources,
                step.script_locator,
                role=ResourceRole.SCRIPT,
                generated=False,
                limitations=limitations,
            )
            edges.append(
                _pending_edge(
                    source_state_id,
                    step.node_id,
                    (script.locator_kind, script.locator),
                    "implemented_by",
                )
            )
        for field_name, role, predicate, generated in (
            ("input", ResourceRole.INPUT, "consumes", False),
            ("output", ResourceRole.OUTPUT, "produces", True),
            ("report", ResourceRole.REPORT, "produces", True),
        ):
            for raw_resource in _sequence(rule.get(field_name)):
                draft = _merge_resource(
                    resources,
                    raw_resource,
                    role=role,
                    generated=generated,
                    limitations=limitations,
                )
                edges.append(
                    _pending_edge(
                        source_state_id,
                        step.node_id,
                        (draft.locator_kind, draft.locator),
                        predicate,
                    )
                )
        environment = rule.get("environment")
        if environment is not None:
            environment_node = _environment_node(environment, source_state_id, name)
            nodes.append(environment_node)
            edges.append(
                build_research_edge(
                    source_state_id=source_state_id,
                    source_node_id=step.node_id,
                    target_node_id=environment_node.node_id,
                    predicate="uses_environment",
                    evidence_kind="declared",
                )
            )
        for dependency in _string_sequence(rule.get("depends_on"), f"rule {name} depends_on"):
            pending_dependencies.append((step, dependency))
    resource_nodes: dict[tuple[str, str], ResearchResource] = {}
    for key, draft in sorted(resources.items()):
        resource = build_research_node(
            ResearchResource,
            source_state_id=source_state_id,
            locator_kind=draft.locator_kind,
            locator=draft.locator,
            roles=sorted(draft.roles, key=lambda item: item.value),
            media_type=draft.media_type,
            size_bytes=draft.size_bytes,
            digest=draft.digest,
            generated=draft.generated,
            content_class=draft.content_class,
            availability=draft.availability,
            schema_identity=draft.schema_identity,
        )
        resource_nodes[key] = resource
        nodes.append(resource)
    edges = [_resolve_pending_resource_edge(edge, resource_nodes) for edge in edges]
    for step, dependency_name in pending_dependencies:
        dependency_step = steps.get(dependency_name)
        if dependency_step is None:
            limitations.add(f"Rule {step.name} names unresolved dependency {dependency_name}.")
            continue
        edges.append(
            build_research_edge(
                source_state_id=source_state_id,
                source_node_id=step.node_id,
                target_node_id=dependency_step.node_id,
                predicate="depends_on",
                evidence_kind="declared",
            )
        )
    if bool(payload.get("dynamic_dag", False)):
        limitations.add("The workflow declares a runtime-dynamic DAG; the captured graph is a static projection.")
    return build_research_graph(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="snakemake-summary",
        provider_version=provider_version,
        source_artifact=raw,
        nodes=nodes,
        edges=edges,
        limitations=limitations,
        evidence_families=["artifact", "configuration", "data", "workflow"],
    )


def parse_targets_manifest(
    manifest_raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
    metadata_raw: bytes | None = None,
    network_raw: bytes | None = None,
) -> ResearchGraphArtifact:
    """Parse native ``tar_manifest()``, ``tar_meta()``, and edge-list exports."""
    manifest = _csv_rows(manifest_raw, "targets manifest")
    metadata = (
        {row.get("name", ""): row for row in _csv_rows(metadata_raw, "targets metadata")}
        if metadata_raw is not None
        else {}
    )
    if not manifest or "name" not in manifest[0]:
        raise ResearchArtifactError(
            "targets_manifest_invalid",
            "targets manifest requires a name column and at least one row",
            remediation="Export targets::tar_manifest() as CSV and preserve its native name/command columns.",
        )
    nodes: list[ResearchNode] = []
    edges: list[ResearchEdge] = []
    steps: dict[str, WorkflowStep] = {}
    resources: dict[str, ResearchResource] = {}
    limitations: set[str] = set()
    for row in manifest:
        name = _required_string(row.get("name"), "targets name")
        command = row.get("command") or ""
        step = build_research_node(
            WorkflowStep,
            source_state_id=source_state_id,
            workflow_id="targets-manifest",
            workflow_kind="targets_target",
            name=name,
            script_locator=None,
            parameters={"command_digest": sha256_digest(command.encode())} if command else {},
            resources={},
            dynamic=_truthy(row.get("dynamic")),
        )
        steps[name] = step
        nodes.append(step)
        if step.dynamic:
            limitations.add(f"Target {name} uses dynamic branching; branch instances are not represented.")
        output_path = (metadata.get(name) or {}).get("path") or row.get("path") or row.get("file")
        if output_path:
            resource = _target_resource(output_path, row, metadata.get(name), source_state_id)
            resources[name] = resource
            nodes.append(resource)
            edges.append(
                build_research_edge(
                    source_state_id=source_state_id,
                    source_node_id=step.node_id,
                    target_node_id=resource.node_id,
                    predicate="produces",
                    evidence_kind="observed" if metadata.get(name) else "declared",
                )
            )
            if _truthy((metadata.get(name) or {}).get("stale")):
                limitations.add(f"Target {name} output is marked stale.")
        for dependency in _split_names(row.get("dependencies") or row.get("deps") or ""):
            limitations.add(
                "Non-native dependencies/deps manifest columns were accepted as an adapter extension; "
                "prefer a tar_network() edge-list export."
            )
            if dependency not in steps and dependency not in {item.get("name") for item in manifest}:
                limitations.add(f"Target {name} names unresolved dependency {dependency}.")
                continue
            # Resolved after all targets have been inventoried.
    for row in manifest:
        name = row.get("name", "")
        for dependency_name in _split_names(row.get("dependencies") or row.get("deps") or ""):
            if name in steps and dependency_name in steps:
                edges.append(
                    build_research_edge(
                        source_state_id=source_state_id,
                        source_node_id=steps[name].node_id,
                        target_node_id=steps[dependency_name].node_id,
                        predicate="depends_on",
                        evidence_kind="declared",
                    )
                )
            dependency_resource = resources.get(dependency_name)
            if name in steps and dependency_resource is not None:
                edges.append(
                    build_research_edge(
                        source_state_id=source_state_id,
                        source_node_id=steps[name].node_id,
                        target_node_id=dependency_resource.node_id,
                        predicate="consumes",
                        evidence_kind="declared",
                    )
                )
    if network_raw is not None:
        for row in _csv_rows(network_raw, "targets network"):
            upstream = row.get("from") or row.get("source")
            downstream = row.get("to") or row.get("target")
            if not upstream or not downstream:
                limitations.add("A targets network row omitted from/to identities.")
                continue
            if upstream not in steps or downstream not in steps:
                limitations.add(f"targets network edge {upstream}->{downstream} references an unknown target.")
                continue
            edges.append(
                build_research_edge(
                    source_state_id=source_state_id,
                    source_node_id=steps[downstream].node_id,
                    target_node_id=steps[upstream].node_id,
                    predicate="depends_on",
                    evidence_kind="declared",
                )
            )
    combined = manifest_raw + (metadata_raw or b"") + (network_raw or b"")
    return build_research_graph(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="targets-metadata",
        provider_version=provider_version,
        source_artifact=combined,
        nodes=nodes,
        edges=edges,
        limitations=limitations,
        evidence_families=["artifact", "data", "workflow"],
    )


def _merge_resource(
    resources: dict[tuple[str, str], _ResourceDraft],
    raw: object,
    *,
    role: ResourceRole,
    generated: bool,
    limitations: set[str],
) -> _ResourceDraft:
    value = {"locator": raw} if isinstance(raw, str) else _mapping(raw, f"{role.value} resource")
    locator = _required_string(value.get("locator") or value.get("path") or value.get("uri"), "resource locator")
    locator_kind = str(value.get("locator_kind") or ("remote_uri" if "://" in locator else "repository_path"))
    if locator_kind not in {"repository_path", "remote_uri", "opaque"}:
        raise ResearchArtifactError(
            "resource_locator_kind_invalid",
            f"Unsupported resource locator kind: {locator_kind}",
            remediation="Use repository_path, remote_uri, or opaque.",
        )
    key = (locator_kind, locator)
    draft = resources.setdefault(key, _ResourceDraft(locator=locator, locator_kind=locator_kind))
    draft.roles.add(role)
    draft.generated = draft.generated or generated or bool(value.get("generated", False))
    draft.media_type = _optional_string(value.get("media_type")) or draft.media_type
    draft.schema_identity = _optional_string(value.get("schema_identity")) or draft.schema_identity
    draft.digest = _optional_string(value.get("digest")) or draft.digest
    size = value.get("size_bytes")
    if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
        draft.size_bytes = size
    availability = str(value.get("availability") or ("remote" if locator_kind == "remote_uri" else "available"))
    try:
        draft.availability = ResourceAvailability(availability)
    except ValueError as error:
        raise ResearchArtifactError(
            "resource_availability_invalid",
            f"Unsupported resource availability: {availability}",
            remediation="Use available, missing, remote, opaque, or ignored.",
        ) from error
    content_class = str(value.get("content_class") or ("external" if locator_kind == "remote_uri" else "opaque"))
    try:
        draft.content_class = ContentClass(content_class)
    except ValueError as error:
        raise ResearchArtifactError(
            "resource_content_class_invalid",
            f"Unsupported resource content class: {content_class}",
            remediation="Use an anatomize content classification.",
        ) from error
    if _truthy(value.get("stale")):
        limitations.add(f"Resource {locator} is marked stale.")
    if draft.availability is not ResourceAvailability.AVAILABLE:
        limitations.add(f"Resource {locator} is {draft.availability.value}; content was not inspected.")
    if draft.content_class is ContentClass.SENSITIVE:
        limitations.add(f"Resource {locator} is sensitive; content was excluded by policy.")
    return draft


def _pending_edge(state: str, source: str, target: tuple[str, str], predicate: str) -> ResearchEdge:
    return build_research_edge(
        source_state_id=state,
        source_node_id=source,
        target_node_id=f"pending-resource:{compact_json(target)}",
        predicate=predicate,
        evidence_kind="declared",
    )


def _resolve_pending_resource_edge(
    edge: ResearchEdge,
    resources: dict[tuple[str, str], ResearchResource],
) -> ResearchEdge:
    if not edge.target_node_id.startswith("pending-resource:"):
        return edge
    raw = edge.target_node_id.removeprefix("pending-resource:")
    key_value = __import__("json").loads(raw)
    key = (str(key_value[0]), str(key_value[1]))
    return build_research_edge(
        source_state_id=edge.source_state_id,
        source_node_id=edge.source_node_id,
        target_node_id=resources[key].node_id,
        predicate=edge.predicate,
        evidence_kind=edge.evidence_kind,
    )


def _environment_node(raw: object, state: str, rule_name: str) -> EnvironmentDeclaration:
    value = {"locator": raw, "kind": "conda"} if isinstance(raw, str) else _mapping(raw, "environment")
    kind = _required_string(value.get("kind"), "environment kind")
    locator = _optional_string(value.get("locator"))
    return build_research_node(
        EnvironmentDeclaration,
        source_state_id=state,
        environment_kind=kind,
        name=_optional_string(value.get("name")) or f"{rule_name}:{kind}",
        version=_optional_string(value.get("version")),
        locator=locator,
        digest=_optional_string(value.get("digest")),
    )


def _target_resource(
    path: str,
    manifest: Mapping[str, str],
    metadata: Mapping[str, str] | None,
    state: str,
) -> ResearchResource:
    observed = metadata or {}
    availability = ResourceAvailability.MISSING if _truthy(observed.get("missing")) else ResourceAvailability.AVAILABLE
    digest = observed.get("data") or observed.get("hash") or None
    return build_research_node(
        ResearchResource,
        source_state_id=state,
        locator_kind="remote_uri" if "://" in path else "repository_path",
        locator=path,
        roles=[ResourceRole.OUTPUT],
        media_type=manifest.get("media_type") or None,
        size_bytes=int(observed["bytes"]) if observed.get("bytes", "").isdigit() else None,
        digest=digest,
        generated=True,
        content_class=ContentClass.SENSITIVE if _truthy(manifest.get("sensitive")) else ContentClass.GENERATED,
        availability=availability,
        schema_identity=manifest.get("schema_identity") or None,
    )


def _csv_rows(raw: bytes | None, label: str) -> list[dict[str, str]]:
    if raw is None:
        return []
    if len(raw) > _MAX_WORKFLOW_BYTES:
        raise ResearchArtifactError(
            "workflow_artifact_too_large",
            f"{label} exceeds {_MAX_WORKFLOW_BYTES} bytes",
            remediation="Export only manifest and metadata fields required for review.",
        )
    try:
        text = raw.decode("utf-8-sig")
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    except (UnicodeDecodeError, csv.Error) as error:
        raise ResearchArtifactError(
            "workflow_csv_invalid",
            f"{label} is not valid UTF-8 CSV",
            remediation="Re-export the artifact as UTF-8 CSV.",
        ) from error


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchArtifactError(
            "research_artifact_shape_invalid",
            f"{label} must be an object",
            remediation="Regenerate the metadata artifact using the documented schema.",
        )
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> list[object]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchArtifactError(
            "research_artifact_field_invalid",
            f"{label} must be a non-empty string",
            remediation="Regenerate the artifact with stable native identities and locators.",
        )
    return value.strip()


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _scalar_mapping(value: object, label: str, limitations: set[str]) -> dict[str, str | int | float | bool]:
    if value is None:
        return {}
    mapping = _mapping(value, label)
    result: dict[str, str | int | float | bool] = {}
    for key, item in mapping.items():
        if isinstance(item, (str, int, float, bool)):
            result[key] = item
        else:
            result[key] = f"digest:{sha256_digest(compact_json(item).encode())}"
            limitations.add(f"{label} field {key} was reduced to a digest because it is structured.")
    return result


def _string_sequence(value: object, label: str) -> list[str]:
    values = _sequence(value)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ResearchArtifactError(
            "research_artifact_field_invalid",
            f"{label} must contain non-empty strings",
            remediation="Emit native rule or target names only.",
        )
    return [str(item).strip() for item in values]


def _split_names(value: str) -> list[str]:
    return sorted({item.strip() for item in value.replace(";", ",").split(",") if item.strip()})


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"})
