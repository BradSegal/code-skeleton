"""Project the repository index into canonical evidence at the provider boundary."""

from __future__ import annotations

from anatomize._artifacts import content_id
from anatomize.evidence.models import (
    CandidateKind,
    CandidateRecord,
    CompletenessRecord,
    CompletenessStatus,
    DocumentationEntity,
    EdgeRecord,
    EvidenceProducer,
    EvidenceStrength,
    FileCoordinateSpace,
    FileEntity,
    LimitationRecord,
    LocationOrigin,
    LocationRecord,
    OmissionRecord,
    ProviderRunRecord,
    ProviderRunStatus,
    RelationshipCategory,
    RepositoryEntity,
    RepositoryEvidence,
    SourcePosition,
    SourceRange,
    SourceStateRecord,
    SymbolEntity,
)
from anatomize.index.intelligence import relationship_from_occurrence
from anatomize.index.models import (
    DocumentationSection,
    EvidenceConfidence,
    FileRecord,
    ProviderCompleteness,
    RelationshipKind,
    RepositoryIndex,
    SymbolRecord,
)


def repository_index_evidence(
    index: RepositoryIndex,
    *,
    repository_id: str,
) -> RepositoryEvidence:
    """Losslessly project index facts needed by canonical dossiers.

    The index remains a compact compatibility artifact. This bridge creates no
    new semantic claims: exact index facts remain exact, duplicate groups remain
    candidates, and provider limitations become recoverable omissions.
    """
    state_id = f"state:{index.source_state.fact_digest}"
    run_id = f"provider-run:repository-index:{index.source_state.fact_digest}"
    state = SourceStateRecord(
        state_id=state_id,
        repository_id=repository_id,
        revision=index.source_state.commit,
        dirty=index.source_state.dirty,
        content_digest=index.source_state.fact_digest,
        file_count=index.source_state.fact_file_count,
    )
    repository = RepositoryEntity(
        entity_id=content_id("entity:repository-index", {"repository": repository_id, "state": state_id}),
        source_state_id=state_id,
        repository_id=repository_id,
        display_name=index.root_name,
        root_name=index.root_name,
        provider_run_ids=[run_id],
    )
    locations: list[LocationRecord] = []
    entities: list[FileEntity | SymbolEntity | DocumentationEntity | RepositoryEntity] = [repository]
    file_entities: dict[str, FileEntity] = {}
    for record in index.files:
        location_id = content_id("location:index-file", {"state": state_id, "path": record.path})
        file_entity = _file_entity(record, state_id, location_id, run_id)
        file_entities[record.path] = file_entity
        entities.append(file_entity)
        locations.append(
            LocationRecord(
                location_id=location_id,
                source_state_id=state_id,
                origin=LocationOrigin.REPOSITORY,
                file_id=file_entity.entity_id,
                path=record.path,
                coordinate_space=FileCoordinateSpace(),
            )
        )
    for symbol in index.symbols:
        location_id = content_id("location:index-symbol", {"state": state_id, "symbol": symbol.symbol_id})
        locations.append(_symbol_location(symbol, state_id, location_id, file_entities[symbol.path]))
        entities.append(
            SymbolEntity(
                entity_id=symbol.symbol_id,
                source_state_id=state_id,
                display_name=symbol.qualified_name,
                location_ids=[location_id],
                provider_run_ids=[run_id],
                language="python",
                symbol_kind=symbol.kind.value,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                public=symbol.public,
                digest=symbol.digest,
            )
        )
    for section in index.documentation_sections:
        location_id = content_id("location:index-documentation", {"state": state_id, "section": section.section_id})
        locations.append(_documentation_location(section, state_id, location_id, file_entities[section.path]))
        entities.append(
            DocumentationEntity(
                entity_id=section.section_id,
                source_state_id=state_id,
                display_name=f"{section.path}#{section.heading}",
                location_ids=[location_id],
                provider_run_ids=[run_id],
                documentation_kind="markdown_section",
                heading=section.heading,
                digest=section.digest,
            )
        )
    entity_ids = {item.entity_id for item in entities}
    edges: list[EdgeRecord] = []
    for occurrence in index.occurrences:
        relationship = relationship_from_occurrence(occurrence)
        if relationship.source_id not in entity_ids or relationship.target_id not in entity_ids:
            continue
        category = {
            RelationshipKind.DEFINES: RelationshipCategory.STRUCTURE,
            RelationshipKind.IMPORTS: RelationshipCategory.DEPENDENCY,
            RelationshipKind.REFERENCES: RelationshipCategory.REFERENCE,
        }[relationship.kind]
        edges.append(
            EdgeRecord(
                edge_id=relationship.relationship_id,
                source_state_id=state_id,
                source_entity_id=relationship.source_id,
                target_entity_id=relationship.target_id,
                category=category,
                predicate=relationship.kind.value,
                provider_run_ids=[run_id],
            )
        )
    for import_edge in index.import_edges:
        source = file_entities.get(import_edge.importer_path)
        target = file_entities.get(import_edge.imported_path)
        if source is None or target is None:
            continue
        edges.append(
            EdgeRecord(
                edge_id=content_id(
                    "edge:index-import",
                    {"state": state_id, "source": source.entity_id, "target": target.entity_id},
                ),
                source_state_id=state_id,
                source_entity_id=source.entity_id,
                target_entity_id=target.entity_id,
                category=RelationshipCategory.DEPENDENCY,
                predicate="imports_module",
                provider_run_ids=[run_id],
            )
        )
    candidates = [
        CandidateRecord(
            candidate_id=group.group_id,
            source_state_id=state_id,
            kind=CandidateKind.DUPLICATION,
            member_entity_ids=sorted(
                member.symbol_id or member.section_id
                for member in group.members
                if member.symbol_id is not None or member.section_id is not None
            ),
            method=f"{group.provider_id}:{group.normalization.value}",
            strength=(
                EvidenceStrength.EXACT
                if group.confidence is EvidenceConfidence.EXACT
                else EvidenceStrength.CONSERVATIVE
            ),
            provider_run_ids=[run_id],
            rationale="; ".join(group.differences) or "Members share the declared structural representation.",
        )
        for group in index.duplicate_groups
        if any(member.symbol_id is not None or member.section_id is not None for member in group.members)
    ]
    limitations = [
        LimitationRecord(
            limitation_id=content_id("limitation:repository-index", {"run": run_id, "summary": summary}),
            provider_run_id=run_id,
            code="repository_index_boundary",
            summary=summary,
        )
        for summary in sorted(set(index.limitations))
    ]
    omissions = [
        OmissionRecord(
            omission_id=content_id("omission:repository-index", {"run": run_id, "limitation": item.limitation_id}),
            source_state_id=state_id,
            provider_run_id=run_id,
            reason=item.summary,
            scope_type="repository",
            scope_id=repository.entity_id,
            recoverable=True,
            remediation="Attach a qualified semantic, runtime, or domain provider artifact when required.",
        )
        for item in limitations
    ]
    capabilities = sorted({capability for provider in index.providers for capability in provider.capabilities})
    completeness_status = _completeness(index)
    completeness_id = content_id("completeness:repository-index", {"run": run_id})
    completeness = CompletenessRecord(
        completeness_id=completeness_id,
        source_state_id=state_id,
        provider_run_id=run_id,
        scope_type="repository",
        scope_id=repository.entity_id,
        evidence_families=capabilities,
        status=completeness_status,
        omission_ids=[item.omission_id for item in omissions],
    )
    run = ProviderRunRecord(
        provider_run_id=run_id,
        provider_id="anatomize.repository-index",
        provider_version=index.schema_version,
        source_state_id=state_id,
        configuration_digest=f"sha256:{index.source_state.provider_digest}",
        method="built-in deterministic repository index",
        capabilities=capabilities,
        status=(
            ProviderRunStatus.COMPLETE
            if completeness_status is CompletenessStatus.COMPLETE
            else ProviderRunStatus.UNAVAILABLE
            if completeness_status is CompletenessStatus.UNAVAILABLE
            else ProviderRunStatus.PARTIAL
        ),
        limitation_ids=[item.limitation_id for item in limitations],
    )
    return RepositoryEvidence(
        producer=EvidenceProducer(version=index.producer.version),
        repository_id=repository_id,
        states=[state],
        provider_runs=[run],
        locations=sorted(locations, key=lambda item: item.location_id),
        entities=sorted(entities, key=lambda item: item.entity_id),
        edges=sorted(edges, key=lambda item: item.edge_id),
        candidates=sorted(candidates, key=lambda item: item.candidate_id),
        completeness=[completeness],
        limitations=limitations,
        omissions=omissions,
    )


def _file_entity(record: FileRecord, state_id: str, location_id: str, run_id: str) -> FileEntity:
    return FileEntity(
        entity_id=record.file_id,
        source_state_id=state_id,
        display_name=record.path,
        location_ids=[location_id],
        provider_run_ids=[run_id],
        path=record.path,
        language=record.language,
        digest=record.digest,
        size_bytes=record.size,
        roles=[role.value for role in record.roles],
    )


def _symbol_location(
    symbol: SymbolRecord,
    state_id: str,
    location_id: str,
    file_entity: FileEntity,
) -> LocationRecord:
    return LocationRecord(
        location_id=location_id,
        source_state_id=state_id,
        origin=LocationOrigin.REPOSITORY,
        file_id=file_entity.entity_id,
        path=symbol.path,
        coordinate_space=FileCoordinateSpace(),
        source_range=SourceRange(
            start=SourcePosition(line=symbol.line, column=symbol.column),
            end=SourcePosition(line=symbol.end_line, column=symbol.end_column),
        ),
    )


def _documentation_location(
    section: DocumentationSection,
    state_id: str,
    location_id: str,
    file_entity: FileEntity,
) -> LocationRecord:
    return LocationRecord(
        location_id=location_id,
        source_state_id=state_id,
        origin=LocationOrigin.REPOSITORY,
        file_id=file_entity.entity_id,
        path=section.path,
        coordinate_space=FileCoordinateSpace(),
        source_range=SourceRange(
            start=SourcePosition(line=section.line, column=0),
            end=SourcePosition(line=section.end_line, column=0),
        ),
    )


def _completeness(index: RepositoryIndex) -> CompletenessStatus:
    values = {provider.completeness for provider in index.providers}
    if ProviderCompleteness.UNAVAILABLE in values:
        return CompletenessStatus.UNAVAILABLE
    if ProviderCompleteness.PARTIAL in values:
        return CompletenessStatus.PARTIAL
    return CompletenessStatus.COMPLETE
