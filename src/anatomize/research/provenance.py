"""Content-free environment, supply-chain, and RO-Crate artifact adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from anatomize._artifacts import JsonLimits, parse_bounded_json_object
from anatomize.evidence import ContentClass
from anatomize.research.graphs import (
    EnvironmentDeclaration,
    ProvenanceContext,
    ResearchDependency,
    ResearchEdge,
    ResearchGraphArtifact,
    ResearchNode,
    ResearchResource,
    ResourceAvailability,
    ResourceRole,
    SupplyChainFinding,
    build_research_edge,
    build_research_graph,
    build_research_node,
)
from anatomize.research.workflows import ResearchArtifactError

_MAX_PROVENANCE_BYTES = 64 * 1024 * 1024


def parse_renv_lock(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
) -> ResearchGraphArtifact:
    """Import dependency declarations from an ``renv.lock`` artifact."""
    payload = _json(raw, "renv.lock")
    packages = payload.get("Packages")
    if not isinstance(packages, dict):
        raise ResearchArtifactError(
            "renv_packages_missing",
            "renv.lock requires a Packages object",
            remediation="Run renv::snapshot() and import the resulting lockfile.",
        )
    r_version = _mapping(payload.get("R"), "renv R declaration").get("Version")
    environment = build_research_node(
        EnvironmentDeclaration,
        source_state_id=source_state_id,
        environment_kind="renv_lockfile",
        name="renv.lock",
        version=_optional_string(r_version),
        locator="renv.lock",
        digest=None,
    )
    nodes: list[ResearchNode] = [environment]
    edges: list[ResearchEdge] = []
    limitations: set[str] = set()
    for key, raw_package in sorted(packages.items()):
        package = _mapping(raw_package, f"renv package {key}")
        name = _optional_string(package.get("Package")) or str(key)
        version = _optional_string(package.get("Version"))
        if version is None:
            limitations.add(f"renv package {name} has no pinned version.")
        dependency = build_research_node(
            ResearchDependency,
            source_state_id=source_state_id,
            ecosystem=_renv_ecosystem(package),
            package=name,
            version=version,
            scope="runtime",
            source=_optional_string(package.get("Repository")) or _optional_string(package.get("Source")),
            digest=_optional_string(package.get("Hash")),
        )
        nodes.append(dependency)
        edges.append(
            build_research_edge(
                source_state_id=source_state_id,
                source_node_id=environment.node_id,
                target_node_id=dependency.node_id,
                predicate="declares_dependency",
                evidence_kind="declared",
            )
        )
    return build_research_graph(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="renv-lockfile",
        provider_version=provider_version,
        source_artifact=raw,
        nodes=nodes,
        edges=edges,
        limitations=limitations,
        evidence_families=["configuration", "dependency"],
    )


def parse_cyclonedx_sbom(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
) -> ResearchGraphArtifact:
    """Import CycloneDX components, dependency edges, and reported vulnerabilities."""
    payload = _json(raw, "CycloneDX SBOM")
    if payload.get("bomFormat") != "CycloneDX" or not isinstance(payload.get("components", []), list):
        raise ResearchArtifactError(
            "cyclonedx_shape_invalid",
            "Artifact is not a CycloneDX object with a components array",
            remediation="Export CycloneDX JSON and retry.",
        )
    nodes: list[ResearchNode] = []
    edges: list[ResearchEdge] = []
    limitations: set[str] = set()
    references: dict[str, ResearchDependency] = {}
    metadata = payload.get("metadata")
    root_component = metadata.get("component") if isinstance(metadata, dict) else None
    component_values = [
        *([root_component] if isinstance(root_component, dict) else []),
        *payload.get("components", []),
    ]
    for index, value in enumerate(component_values):
        component = _mapping(value, f"CycloneDX component {index}")
        name = _required_string(component.get("name"), f"CycloneDX component {index} name")
        purl = _optional_string(component.get("purl"))
        dependency = build_research_node(
            ResearchDependency,
            source_state_id=source_state_id,
            ecosystem=_ecosystem(purl, component),
            package=name,
            version=_optional_string(component.get("version")),
            scope=_optional_string(component.get("scope")),
            source=purl,
            digest=_component_digest(component),
        )
        nodes.append(dependency)
        reference = _optional_string(component.get("bom-ref")) or purl or name
        references[reference] = dependency
    for value in payload.get("dependencies", []):
        declaration = _mapping(value, "CycloneDX dependency")
        source = references.get(str(declaration.get("ref", "")))
        for dependency_ref in declaration.get("dependsOn", []):
            target = references.get(str(dependency_ref))
            if source is None or target is None:
                limitations.add(f"CycloneDX dependency references unresolved component {dependency_ref}.")
                continue
            edges.append(
                build_research_edge(
                    source_state_id=source_state_id,
                    source_node_id=source.node_id,
                    target_node_id=target.node_id,
                    predicate="depends_on",
                    evidence_kind="declared",
                )
            )
    for index, value in enumerate(payload.get("vulnerabilities", [])):
        vulnerability = _mapping(value, f"CycloneDX vulnerability {index}")
        identity = _required_string(vulnerability.get("id"), f"CycloneDX vulnerability {index} id")
        finding = build_research_node(
            SupplyChainFinding,
            source_state_id=source_state_id,
            finding_identity=identity,
            severity=_vulnerability_severity(vulnerability),
            status=_optional_string(vulnerability.get("analysis", {}).get("state"))
            if isinstance(vulnerability.get("analysis"), dict)
            else None,
            description_digest=None,
        )
        nodes.append(finding)
        for affect in vulnerability.get("affects", []):
            affected = _mapping(affect, f"CycloneDX vulnerability {identity} affect")
            target = references.get(str(affected.get("ref", "")))
            if target is None:
                limitations.add(f"CycloneDX vulnerability {identity} affects an unresolved component.")
                continue
            edges.append(
                build_research_edge(
                    source_state_id=source_state_id,
                    source_node_id=finding.node_id,
                    target_node_id=target.node_id,
                    predicate="affects",
                    evidence_kind="observed",
                )
            )
    compositions = payload.get("compositions", [])
    if not compositions:
        limitations.add("CycloneDX artifact does not declare composition completeness.")
    elif any(
        isinstance(item, dict) and item.get("aggregate") not in {"complete", "complete_first_party_only"}
        for item in compositions
    ):
        limitations.add("CycloneDX composition declares incomplete or unknown component coverage.")
    return build_research_graph(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="cyclonedx",
        provider_version=provider_version,
        source_artifact=raw,
        nodes=nodes,
        edges=edges,
        limitations=limitations,
        evidence_families=["dependency", "diagnostic", "provenance"],
    )


def parse_ro_crate(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
) -> ResearchGraphArtifact:
    """Import bounded RO-Crate JSON-LD identities and relationships without dereferencing."""
    payload = _json(raw, "RO-Crate metadata")
    graph = payload.get("@graph")
    if not isinstance(graph, list):
        raise ResearchArtifactError(
            "ro_crate_graph_missing",
            "RO-Crate metadata requires an @graph array",
            remediation="Validate ro-crate-metadata.json against the RO-Crate specification.",
        )
    nodes: list[ResearchNode] = []
    edges: list[ResearchEdge] = []
    limitations: set[str] = set()
    references: dict[str, ResearchNode] = {}
    for index, value in enumerate(graph):
        entity = _mapping(value, f"RO-Crate entity {index}")
        identity = _required_string(entity.get("@id"), f"RO-Crate entity {index} @id")
        node = _crate_node(entity, identity, source_state_id, limitations)
        references[identity] = node
        nodes.append(node)
    predicates = {
        "hasPart": "has_part",
        "isPartOf": "is_part_of",
        "distribution": "has_distribution",
        "subjectOf": "subject_of",
        "mentions": "mentions",
        "exampleOfWork": "example_of_work",
        "instrument": "uses_instrument",
        "object": "uses_object",
        "result": "produces",
    }
    for value in graph:
        entity = _mapping(value, "RO-Crate entity")
        source = references[str(entity.get("@id", ""))]
        for field_name, predicate in predicates.items():
            for reference in _references(entity.get(field_name)):
                target = references.get(reference)
                if target is None:
                    limitations.add(f"RO-Crate {field_name} references unresolved entity {reference}.")
                    continue
                edges.append(
                    build_research_edge(
                        source_state_id=source_state_id,
                        source_node_id=source.node_id,
                        target_node_id=target.node_id,
                        predicate=predicate,
                        evidence_kind="declared",
                    )
                )
    return build_research_graph(
        repository_id=repository_id,
        source_state_id=source_state_id,
        provider_run_id=provider_run_id,
        provider_id="ro-crate",
        provider_version=provider_version,
        source_artifact=raw,
        nodes=nodes,
        edges=edges,
        limitations=limitations,
        evidence_families=["artifact", "data", "provenance"],
    )


def _crate_node(
    entity: Mapping[str, Any],
    identity: str,
    state: str,
    limitations: set[str],
) -> ResearchNode:
    entity_types = _types(entity.get("@type"))
    name = _optional_string(entity.get("name")) or identity
    if "File" in entity_types or "Dataset" in entity_types and identity not in {"./", "."}:
        remote = "://" in identity
        opaque = _is_opaque(entity)
        ignored = bool(entity.get("anatomize:ignored", False))
        sensitive = bool(entity.get("anatomize:sensitive", False))
        if remote:
            availability = ResourceAvailability.REMOTE
            content_class = ContentClass.EXTERNAL
            limitations.add(f"RO-Crate resource {identity} is remote and was not dereferenced.")
        elif ignored:
            availability = ResourceAvailability.IGNORED
            content_class = ContentClass.IGNORED
            limitations.add(f"RO-Crate resource {identity} is ignored and was not inspected.")
        elif opaque:
            availability = ResourceAvailability.OPAQUE
            content_class = ContentClass.OPAQUE
            limitations.add(f"RO-Crate resource {identity} is opaque and only metadata was captured.")
        else:
            availability = ResourceAvailability.AVAILABLE
            content_class = ContentClass.SENSITIVE if sensitive else ContentClass.ORDINARY
            if sensitive:
                limitations.add(f"RO-Crate resource {identity} is sensitive; content was not imported.")
        return build_research_node(
            ResearchResource,
            source_state_id=state,
            locator_kind="remote_uri" if remote else "repository_path",
            locator=identity.removeprefix("./"),
            roles=[ResourceRole.DATA if "Dataset" in entity_types else ResourceRole.OTHER],
            media_type=_optional_string(entity.get("encodingFormat")),
            size_bytes=_integer(entity.get("contentSize")),
            digest=_optional_string(entity.get("sha256")),
            generated=bool(entity.get("dateCreated")),
            content_class=content_class,
            availability=availability,
            schema_identity=_optional_string(entity.get("conformsTo")),
        )
    external_identity = f"ro-crate:{identity}" if identity in {".", "./"} or identity.startswith("#") else identity
    return build_research_node(
        ProvenanceContext,
        source_state_id=state,
        identity_scheme="ro-crate-id",
        external_identity=external_identity,
        context_kind="/".join(entity_types) or "Thing",
        display_name=name,
    )


def _json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        return parse_bounded_json_object(raw, limits=JsonLimits(max_bytes=_MAX_PROVENANCE_BYTES))
    except ValueError as error:
        raise ResearchArtifactError(
            "research_json_invalid",
            f"{label}: {error}",
            remediation="Regenerate a bounded UTF-8 JSON metadata artifact and retry.",
        ) from error


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchArtifactError(
            "research_artifact_shape_invalid",
            f"{label} must be an object",
            remediation="Regenerate the metadata artifact using its canonical schema.",
        )
    return {str(key): item for key, item in value.items()}


def _required_string(value: object, label: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise ResearchArtifactError(
            "research_artifact_field_invalid",
            f"{label} must be a non-empty string",
            remediation="Preserve the artifact's native identity fields.",
        )
    return result


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _ecosystem(purl: str | None, component: Mapping[str, Any]) -> str:
    if purl and purl.startswith("pkg:"):
        return purl.removeprefix("pkg:").split("/", 1)[0]
    return _optional_string(component.get("type")) or "unknown"


def _renv_ecosystem(package: Mapping[str, Any]) -> str:
    source = (_optional_string(package.get("Source")) or "Repository").casefold()
    return {
        "bioconductor": "bioconductor",
        "github": "github",
        "gitlab": "gitlab",
        "bitbucket": "bitbucket",
        "local": "local",
        "url": "url",
    }.get(source, "cran" if package.get("Repository") is not None else source)


def _component_digest(component: Mapping[str, Any]) -> str | None:
    for value in component.get("hashes", []):
        if isinstance(value, dict) and value.get("alg") == "SHA-256":
            digest = _optional_string(value.get("content"))
            return f"sha256:{digest.lower()}" if digest else None
    return None


def _vulnerability_severity(value: Mapping[str, Any]) -> str:
    for rating in value.get("ratings", []):
        if isinstance(rating, dict) and _optional_string(rating.get("severity")):
            return str(rating["severity"]).lower()
    return "unknown"


def _references(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    references = []
    for item in values:
        if isinstance(item, dict) and isinstance(item.get("@id"), str):
            references.append(item["@id"])
        elif isinstance(item, str):
            references.append(item)
    return references


def _types(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return sorted(str(item) for item in values if isinstance(item, str))


def _is_opaque(entity: Mapping[str, Any]) -> bool:
    media_type = str(entity.get("encodingFormat", "")).lower()
    identity = str(entity.get("@id", "")).lower()
    return any(token in media_type or identity.endswith(token) for token in ("zip", ".tar", ".gz", ".7z"))


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
