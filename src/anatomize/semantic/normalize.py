"""Normalize captured LSP semantics into the common provider evidence contract."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from anatomize._artifacts import content_id
from anatomize.evidence import (
    CandidateKind,
    CandidateRecord,
    CompletenessRecord,
    CompletenessStatus,
    ContentClass,
    EdgeRecord,
    EvidenceStrength,
    ExternalEntity,
    FileCoordinateSpace,
    FileEntity,
    GeneratedCoordinateSpace,
    IdentityReason,
    LocationOrigin,
    LocationRecord,
    ObservationStance,
    ProjectionCompleteness,
    ProviderRunStatus,
    RelationshipCategory,
    RepositoryEntity,
    RepositoryEvidence,
    SourceRange,
    SourceStateRecord,
    StructuralObservation,
    SymbolEntity,
)
from anatomize.identity import (
    ClaimCertainty,
    ColumnEncoding,
    CoordinateConvention,
    CoordinateError,
    FileIdentityKey,
    IdentityMappingClaim,
    PathKind,
    ProviderRange,
    SourceCoordinateMap,
    SymbolIdentityKey,
    SymbolRole,
    canonical_identity_id,
    reconcile_identity_claims,
)
from anatomize.providers import (
    AuthorityLevel,
    InvocationAuthority,
    InvocationMode,
    ProviderBatchBuilder,
    ProviderEnvelope,
    ProviderScope,
    ProviderToolIdentity,
    build_provider_envelope,
)
from anatomize.semantic.models import (
    SUPPORTED_LSP_VERSIONS,
    LspPositionEncoding,
    LspSemanticArtifact,
    SemanticCapability,
    SemanticDocument,
    SemanticIssueCode,
    SemanticOccurrence,
    SemanticRelationship,
    SemanticSymbol,
)

LSP_SEMANTIC_PROVIDER_ID = "anatomize.lsp-artifact"
LSP_SEMANTIC_PROVIDER_VERSION = "1.0.0"


@dataclass(frozen=True)
class _Resolution:
    entity_ids: tuple[str, ...]
    exact: bool


_BatchBuilder = ProviderBatchBuilder


def normalize_lsp_semantic_artifact(
    artifact: LspSemanticArtifact,
    *,
    expected_state: SourceStateRecord,
    baseline: RepositoryEvidence,
    sources: Mapping[str, str],
    expected_configuration_digest: str,
    invocation: InvocationAuthority | None = None,
    provider_id: str = LSP_SEMANTIC_PROVIDER_ID,
    provider_version: str = LSP_SEMANTIC_PROVIDER_VERSION,
) -> ProviderEnvelope:
    """Reconcile one captured LSP artifact with canonical baseline identities.

    Source bytes are supplied by the trusted repository reader, not embedded in the
    provider artifact. Every covered document is digest-checked before its ranges
    can become evidence.
    """
    _validate_baseline(expected_state, baseline)
    invocation = invocation or _artifact_invocation()
    run_id = content_id(
        "provider-run:lsp",
        {
            "artifact": artifact.model_dump(mode="json"),
            "expected_state": expected_state.model_dump(mode="json"),
            "expected_configuration_digest": expected_configuration_digest,
            "invocation": invocation.model_dump(mode="json"),
            "provider_id": provider_id,
            "provider_version": provider_version,
        },
    )
    capabilities = sorted(item.value for item in artifact.capabilities)
    builder = _BatchBuilder(baseline=baseline)
    repository_entity = builder.repository_entity(expected_state)
    completeness_id = content_id("completeness:lsp", {"run": run_id})

    for issue in artifact.issues:
        _add_degradation(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code=issue.code.value,
            summary=issue.message,
            scope_type="path" if issue.path else "repository",
            scope_id=issue.path or repository_entity.entity_id,
            recoverable=issue.recoverable,
            remediation=issue.remediation,
            discriminator=issue.issue_id,
        )

    fatal_reason = _fatal_artifact_reason(
        artifact,
        expected_state,
        expected_configuration_digest=expected_configuration_digest,
    )
    if fatal_reason is not None:
        code, summary, remediation = fatal_reason
        _add_degradation(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code=code,
            summary=summary,
            scope_type="repository",
            scope_id=repository_entity.entity_id,
            recoverable=True,
            remediation=remediation,
        )
        return _seal(
            artifact=artifact,
            expected_state=expected_state,
            run_id=run_id,
            capabilities=capabilities,
            repository_entity=repository_entity,
            builder=builder,
            completeness_id=completeness_id,
            status=ProviderRunStatus.UNAVAILABLE,
            invocation=invocation,
            provider_id=provider_id,
            provider_version=provider_version,
        )

    valid_documents: dict[str, tuple[SemanticDocument, str, FileEntity]] = {}
    for document in sorted(artifact.documents, key=lambda item: item.path):
        source = sources.get(document.path)
        observed_digest = _source_digest(source) if source is not None else None
        if source is None or observed_digest != document.content_digest:
            observed = "missing" if source is None else f"digest {observed_digest}"
            _add_degradation(
                builder,
                run_id=run_id,
                state_id=expected_state.state_id,
                code="document_source_mismatch",
                summary=f"Semantic document {document.path} is {observed}; expected {document.content_digest}.",
                scope_type="path",
                scope_id=document.path,
                recoverable=True,
                remediation="Capture semantics from the selected source state and retry.",
            )
            continue
        file_entity = _file_entity(
            document,
            expected_state,
            baseline,
            builder,
            source,
            run_id=run_id,
        )
        valid_documents[document.path] = (document, source, file_entity)

    resolutions: dict[str, _Resolution] = {}
    declaration_ordinals: defaultdict[tuple[str, str, str, str, str | None], int] = defaultdict(int)
    for symbol in sorted(artifact.symbols, key=lambda item: item.symbol_id):
        document_entry = valid_documents.get(symbol.document_path)
        if document_entry is None:
            continue
        document, source, file_entity = document_entry
        source_range = _convert_range_or_omit(
            builder,
            artifact=artifact,
            run_id=run_id,
            state_id=expected_state.state_id,
            native_id=symbol.symbol_id,
            path=document.path,
            source=source,
            provider_range=symbol.source_range,
        )
        if source_range is None:
            continue
        location = _semantic_location(
            expected_state=expected_state,
            document=document,
            file_entity=file_entity,
            source_range=source_range,
            native_id=symbol.symbol_id,
        )
        builder.locations[location.location_id] = location
        matches = _baseline_symbol_matches(symbol, source_range, expected_state, baseline)
        if len(matches) == 1:
            entity = matches[0]
            builder.include_baseline_entity(entity)
            resolution = _Resolution((entity.entity_id,), True)
            reasons = [_reason_for_role(symbol.role), IdentityReason.COORDINATE_PROJECTION]
        elif len(matches) > 1:
            for entity in matches:
                builder.include_baseline_entity(entity)
            resolution = _Resolution(tuple(sorted(item.entity_id for item in matches)), False)
            reasons = [IdentityReason.MULTIPLE_CANDIDATES]
        else:
            ordinal_key = (
                symbol.document_path,
                symbol.language,
                symbol.qualified_name,
                symbol.role.value,
                symbol.signature,
            )
            ordinal = declaration_ordinals[ordinal_key]
            declaration_ordinals[ordinal_key] += 1
            entity = _new_symbol_entity(symbol, expected_state, location.location_id, ordinal)
            builder.entities[entity.entity_id] = entity
            resolution = _Resolution((entity.entity_id,), True)
            reasons = [_reason_for_role(symbol.role)]
        resolutions[symbol.symbol_id] = resolution
        claim = IdentityMappingClaim(
            provider_run_id=run_id,
            certainty=ClaimCertainty.EXACT if resolution.exact else ClaimCertainty.CANDIDATE,
            candidate_entity_ids=list(resolution.entity_ids),
            reason_codes=reasons,
            location_ids=[location.location_id],
        )
        alias = reconcile_identity_claims(
            repository_id=expected_state.repository_id,
            source_state_id=expected_state.state_id,
            scheme="lsp-symbol",
            value=symbol.symbol_id,
            claims=[claim],
            known_entity_ids=set(builder.entities),
            rationale="The captured LSP declaration was reconciled by state, path, language, name, role, and range.",
        )
        builder.aliases[alias.alias_id] = alias
        if resolution.exact:
            _add_declaration_edge(
                builder,
                run_id=run_id,
                state_id=expected_state.state_id,
                completeness_id=completeness_id,
                symbol=symbol,
                file_entity=file_entity,
                target_entity_id=resolution.entity_ids[0],
                location_id=location.location_id,
            )
        else:
            candidate = CandidateRecord(
                candidate_id=content_id("candidate:lsp-symbol", {"run": run_id, "symbol": symbol.symbol_id}),
                source_state_id=expected_state.state_id,
                kind=CandidateKind.REFERENCE,
                member_entity_ids=list(resolution.entity_ids),
                method="lsp-symbol-reconciliation",
                strength=EvidenceStrength.CONSERVATIVE,
                rationale=(
                    "Multiple baseline identities match the captured semantic declaration; "
                    "no edge was asserted."
                ),
            )
            builder.candidates[candidate.candidate_id] = candidate

    unresolved_targets: defaultdict[str, list[str]] = defaultdict(list)
    for occurrence in sorted(artifact.occurrences, key=lambda item: item.occurrence_id):
        document_entry = valid_documents.get(occurrence.document_path)
        if document_entry is None:
            continue
        document, source, file_entity = document_entry
        source_range = _convert_range_or_omit(
            builder,
            artifact=artifact,
            run_id=run_id,
            state_id=expected_state.state_id,
            native_id=occurrence.occurrence_id,
            path=document.path,
            source=source,
            provider_range=occurrence.source_range,
        )
        if source_range is None:
            continue
        location = _semantic_location(
            expected_state=expected_state,
            document=document,
            file_entity=file_entity,
            source_range=source_range,
            native_id=occurrence.occurrence_id,
        )
        builder.locations[location.location_id] = location
        source_entity_id = file_entity.entity_id
        if occurrence.source_symbol_id is not None:
            source_resolution = resolutions.get(occurrence.source_symbol_id)
            if source_resolution is not None and source_resolution.exact:
                source_entity_id = source_resolution.entity_ids[0]

        target_resolution = resolutions.get(occurrence.target_symbol_id or "")
        target_entity_id: str | None = None
        if target_resolution is not None and target_resolution.exact:
            target_entity_id = target_resolution.entity_ids[0]
        elif target_resolution is not None:
            candidate = CandidateRecord(
                candidate_id=content_id(
                    "candidate:lsp-occurrence",
                    {"run": run_id, "occurrence": occurrence.occurrence_id},
                ),
                source_state_id=expected_state.state_id,
                kind=CandidateKind.REFERENCE,
                member_entity_ids=list(target_resolution.entity_ids),
                method="lsp-target-reconciliation",
                strength=EvidenceStrength.CONSERVATIVE,
                rationale=(
                    "The semantic occurrence target maps to multiple canonical identities; "
                    "no edge was asserted."
                ),
            )
            builder.candidates[candidate.candidate_id] = candidate
        elif occurrence.external_target is not None:
            external = _external_entity(occurrence, expected_state)
            builder.entities[external.entity_id] = external
            target_entity_id = external.entity_id
        else:
            unresolved_value = occurrence.target_symbol_id or f"occurrence:{occurrence.occurrence_id}:target"
            unresolved_targets[unresolved_value].append(location.location_id)

        if target_entity_id is not None:
            _add_occurrence_edge(
                builder,
                run_id=run_id,
                state_id=expected_state.state_id,
                completeness_id=completeness_id,
                occurrence=occurrence,
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                location_id=location.location_id,
            )
        elif target_resolution is None:
            _add_degradation(
                builder,
                run_id=run_id,
                state_id=expected_state.state_id,
                code="unresolved_semantic_target",
                summary=f"Semantic occurrence {occurrence.occurrence_id} has no exact target mapping.",
                scope_type="occurrence",
                scope_id=occurrence.occurrence_id,
                recoverable=True,
                remediation="Capture a complete project index or inspect the unresolved alias.",
            )

    for native_id, location_ids in sorted(unresolved_targets.items()):
        alias = reconcile_identity_claims(
            repository_id=expected_state.repository_id,
            source_state_id=expected_state.state_id,
            scheme="lsp-symbol",
            value=native_id,
            claims=[
                IdentityMappingClaim(
                    provider_run_id=run_id,
                    certainty=ClaimCertainty.UNRESOLVED,
                    reason_codes=[IdentityReason.NO_CANDIDATE],
                    location_ids=sorted(set(location_ids)),
                )
            ],
            known_entity_ids=set(builder.entities),
            rationale="The captured semantic target was absent or could not be reconciled; no edge was asserted.",
        )
        builder.aliases[alias.alias_id] = alias

    status = artifact.status
    if status is ProviderRunStatus.COMPLETE and builder.omissions:
        status = ProviderRunStatus.PARTIAL
    return _seal(
        artifact=artifact,
        expected_state=expected_state,
        run_id=run_id,
        capabilities=capabilities,
        repository_entity=repository_entity,
        builder=builder,
        completeness_id=completeness_id,
        status=status,
        invocation=invocation,
        provider_id=provider_id,
        provider_version=provider_version,
    )


def unavailable_lsp_semantic_envelope(
    *,
    expected_state: SourceStateRecord,
    baseline: RepositoryEvidence,
    configuration_digest: str,
    requested_paths: Sequence[str] = (),
    reason: str = "No semantic artifact provider was available.",
) -> ProviderEnvelope:
    """Represent optional-provider absence without probing or importing provider code."""
    _validate_baseline(expected_state, baseline)
    artifact = LspSemanticArtifact(
        protocol_version="3.18",
        position_encoding=LspPositionEncoding.UTF16,
        tool={"name": "unavailable-language-server", "version": "unavailable"},
        repository_id=expected_state.repository_id,
        source_state=expected_state,
        indexed_content_digest=expected_state.content_digest,
        configuration_digest=configuration_digest,
        capabilities=list(SemanticCapability),
        languages=["unknown"],
        status=ProviderRunStatus.UNAVAILABLE,
        documents=[],
        issues=[
            {
                "issue_id": "provider-unavailable",
                "code": SemanticIssueCode.PROVIDER_UNAVAILABLE,
                "message": reason,
                "recoverable": True,
                "remediation": "Install or invoke a compatible artifact producer, then import its capture.",
            }
        ],
    )
    envelope = normalize_lsp_semantic_artifact(
        artifact,
        expected_state=expected_state,
        baseline=baseline,
        sources={},
        expected_configuration_digest=configuration_digest,
    )
    if not requested_paths:
        return envelope
    scope = envelope.scope.model_copy(update={"paths": sorted(set(requested_paths))})
    return envelope.model_copy(update={"scope": scope})


def _artifact_invocation() -> InvocationAuthority:
    return InvocationAuthority(
        mode=InvocationMode.ARTIFACT_IMPORT,
        level=AuthorityLevel.A1_ARTIFACT,
        policy_digest="lsp-semantic-artifact-import-v1",
    )


def _validate_baseline(expected_state: SourceStateRecord, baseline: RepositoryEvidence) -> None:
    if baseline.repository_id != expected_state.repository_id:
        raise ValueError("semantic baseline belongs to another repository")
    if expected_state not in baseline.states:
        raise ValueError("semantic baseline does not contain the exact expected source state")


def _fatal_artifact_reason(
    artifact: LspSemanticArtifact,
    expected_state: SourceStateRecord,
    *,
    expected_configuration_digest: str,
) -> tuple[str, str, str] | None:
    if artifact.protocol_version not in SUPPORTED_LSP_VERSIONS:
        return (
            "version_skew",
            (
                f"LSP {artifact.protocol_version} is unsupported; supported captures are "
                f"{sorted(SUPPORTED_LSP_VERSIONS)}."
            ),
            "Regenerate the artifact using a supported LSP capture schema.",
        )
    if (
        artifact.repository_id != expected_state.repository_id
        or artifact.source_state != expected_state
        or artifact.indexed_content_digest != expected_state.content_digest
    ):
        return (
            "stale_index",
            "The semantic artifact is not bound to the selected repository source state.",
            "Regenerate the semantic artifact from the selected source state.",
        )
    if artifact.configuration_digest != expected_configuration_digest:
        return (
            "configuration_mismatch",
            "The semantic artifact was produced under a different build configuration.",
            "Regenerate the semantic artifact with the selected build configuration.",
        )
    if artifact.status in {
        ProviderRunStatus.UNAVAILABLE,
        ProviderRunStatus.FAILED,
        ProviderRunStatus.CANCELLED,
    }:
        reported = next(
            (
                issue
                for issue in artifact.issues
                if issue.code is SemanticIssueCode.PROVIDER_UNAVAILABLE
            ),
            None,
        )
        return (
            "provider_unavailable",
            reported.message
            if reported is not None
            else f"The semantic producer ended with status {artifact.status.value}.",
            reported.remediation
            if reported is not None and reported.remediation is not None
            else "Inspect producer diagnostics and regenerate the semantic artifact.",
        )
    return None


def _file_entity(
    document: SemanticDocument,
    state: SourceStateRecord,
    baseline: RepositoryEvidence,
    builder: _BatchBuilder,
    source: str,
    *,
    run_id: str,
) -> FileEntity:
    matches = [
        item
        for item in baseline.entities
        if isinstance(item, FileEntity)
        and item.source_state_id == state.state_id
        and item.path == document.path
        and (item.content_class is ContentClass.GENERATED) == document.generated
    ]
    if len(matches) == 1:
        builder.include_baseline_entity(matches[0])
        return matches[0]
    path_kind = PathKind.GENERATED if document.generated else PathKind.REGULAR
    content_class = ContentClass.GENERATED if document.generated else ContentClass.ORDINARY
    entity_id = canonical_identity_id(
        FileIdentityKey(
            repository_id=state.repository_id,
            source_state_id=state.state_id,
            path=document.path,
            path_kind=path_kind,
            content_class=content_class,
        )
    )
    location_id = content_id("location:lsp-file", {"state": state.state_id, "path": document.path})
    if document.generated:
        valid_sources = [
            location_id
            for location_id in document.source_location_ids
            if any(item.location_id == location_id for item in baseline.locations)
        ]
        projection = document.projection
        if len(valid_sources) != len(document.source_location_ids):
            projection = ProjectionCompleteness.UNAVAILABLE
            valid_sources = []
            _add_degradation(
                builder,
                run_id=run_id,
                state_id=state.state_id,
                code="generated_projection_unresolved",
                summary=f"Generated document {document.path} references unknown source locations.",
                scope_type="path",
                scope_id=document.path,
                recoverable=True,
                remediation="Capture source locations in the same canonical evidence state.",
            )
        coordinate_space: GeneratedCoordinateSpace | FileCoordinateSpace = GeneratedCoordinateSpace(
            generator_identity_id=document.generator_identity_id or "generator:unknown",
            source_location_ids=valid_sources,
            projection=projection,
        )
        origin = LocationOrigin.GENERATED
        for source_location_id in valid_sources:
            source_location = next(item for item in baseline.locations if item.location_id == source_location_id)
            if source_location.file_id is not None:
                source_entity = next(item for item in baseline.entities if item.entity_id == source_location.file_id)
                builder.include_baseline_entity(source_entity)
    else:
        coordinate_space = FileCoordinateSpace()
        origin = LocationOrigin.REPOSITORY
    location = LocationRecord(
        location_id=location_id,
        source_state_id=state.state_id,
        origin=origin,
        file_id=entity_id,
        path=document.path,
        coordinate_space=coordinate_space,
    )
    entity = FileEntity(
        entity_id=entity_id,
        source_state_id=state.state_id,
        display_name=document.path,
        location_ids=[location_id],
        path=document.path,
        language=document.language,
        digest=document.content_digest,
        size_bytes=len(source.encode("utf-8")),
        roles=["generated" if document.generated else "source"],
        content_class=content_class,
    )
    builder.locations[location_id] = location
    builder.entities[entity_id] = entity
    return entity


def _baseline_symbol_matches(
    symbol: SemanticSymbol,
    source_range: SourceRange,
    expected_state: SourceStateRecord,
    baseline: RepositoryEvidence,
) -> list[SymbolEntity]:
    locations = {item.location_id: item for item in baseline.locations}
    candidates: list[SymbolEntity] = []
    exact_range: list[SymbolEntity] = []
    for entity in baseline.entities:
        if not isinstance(entity, SymbolEntity):
            continue
        if (
            entity.source_state_id != expected_state.state_id
            or entity.language != symbol.language
            or entity.qualified_name != symbol.qualified_name
        ):
            continue
        entity_locations = [locations[item] for item in entity.location_ids]
        if not any(item.path == symbol.document_path for item in entity_locations):
            continue
        candidates.append(entity)
        if any(item.path == symbol.document_path and item.source_range == source_range for item in entity_locations):
            exact_range.append(entity)
    return exact_range or candidates


def _new_symbol_entity(
    symbol: SemanticSymbol,
    state: SourceStateRecord,
    location_id: str,
    declaration_ordinal: int,
) -> SymbolEntity:
    identity = SymbolIdentityKey(
        repository_id=state.repository_id,
        source_state_id=state.state_id,
        path=symbol.document_path,
        language=symbol.language,
        symbol_kind=symbol.symbol_kind,
        qualified_name=symbol.qualified_name,
        role=symbol.role,
        signature=symbol.signature,
        declaration_ordinal=declaration_ordinal,
    )
    return SymbolEntity(
        entity_id=canonical_identity_id(identity),
        source_state_id=state.state_id,
        display_name=symbol.qualified_name,
        location_ids=[location_id],
        language=symbol.language,
        symbol_kind=symbol.symbol_kind,
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        public=symbol.public,
    )


def _semantic_location(
    *,
    expected_state: SourceStateRecord,
    document: SemanticDocument,
    file_entity: FileEntity,
    source_range: SourceRange,
    native_id: str,
) -> LocationRecord:
    if document.generated:
        coordinate_space: GeneratedCoordinateSpace | FileCoordinateSpace = GeneratedCoordinateSpace(
            generator_identity_id=document.generator_identity_id or "generator:unknown",
            projection=ProjectionCompleteness.UNAVAILABLE,
        )
        origin = LocationOrigin.GENERATED
    else:
        coordinate_space = FileCoordinateSpace()
        origin = LocationOrigin.REPOSITORY
    return LocationRecord(
        location_id=content_id(
            "location:lsp",
            {
                "state": expected_state.state_id,
                "path": document.path,
                "native_id": native_id,
                "range": source_range.model_dump(mode="json"),
            },
        ),
        source_state_id=expected_state.state_id,
        origin=origin,
        file_id=file_entity.entity_id,
        path=document.path,
        source_range=source_range,
        coordinate_space=coordinate_space,
    )


def _convert_range_or_omit(
    builder: _BatchBuilder,
    *,
    artifact: LspSemanticArtifact,
    run_id: str,
    state_id: str,
    native_id: str,
    path: str,
    source: str,
    provider_range: ProviderRange,
) -> SourceRange | None:
    convention = CoordinateConvention(
        line_base=0,
        column_encoding={
            LspPositionEncoding.UTF8: ColumnEncoding.UTF8_BYTE,
            LspPositionEncoding.UTF16: ColumnEncoding.UTF16_CODE_UNIT,
            LspPositionEncoding.UTF32: ColumnEncoding.UNICODE_CODEPOINT,
        }[artifact.position_encoding],
    )
    try:
        return SourceCoordinateMap(source).to_canonical(provider_range, convention)
    except CoordinateError as error:
        _add_degradation(
            builder,
            run_id=run_id,
            state_id=state_id,
            code=error.code,
            summary=f"Semantic range {native_id} in {path} is invalid: {error}.",
            scope_type="semantic_record",
            scope_id=native_id,
            recoverable=True,
            remediation="Regenerate the capture with the negotiated LSP position encoding.",
        )
        return None


def _add_declaration_edge(
    builder: _BatchBuilder,
    *,
    run_id: str,
    state_id: str,
    completeness_id: str,
    symbol: SemanticSymbol,
    file_entity: FileEntity,
    target_entity_id: str,
    location_id: str,
) -> None:
    category, predicate = {
        SymbolRole.DEFINITION: (RelationshipCategory.STRUCTURE, "defines"),
        SymbolRole.METHOD: (RelationshipCategory.STRUCTURE, "defines_method"),
        SymbolRole.DECLARATION: (RelationshipCategory.DECLARATION, "declares"),
        SymbolRole.REEXPORT: (RelationshipCategory.DECLARATION, "reexports"),
        SymbolRole.ALIAS: (RelationshipCategory.DECLARATION, "aliases"),
        SymbolRole.OVERLOAD: (RelationshipCategory.DECLARATION, "declares_overload"),
    }[symbol.role]
    edge = EdgeRecord(
        edge_id=content_id("edge:lsp-declaration", {"run": run_id, "symbol": symbol.symbol_id}),
        source_state_id=state_id,
        source_entity_id=file_entity.entity_id,
        target_entity_id=target_entity_id,
        category=category,
        predicate=predicate,
    )
    builder.edges[edge.edge_id] = edge
    _observe_edge(
        builder,
        run_id=run_id,
        state_id=state_id,
        completeness_id=completeness_id,
        edge=edge,
        location_id=location_id,
        rationale="The captured language-server declaration resolved to one canonical identity.",
    )


def _add_occurrence_edge(
    builder: _BatchBuilder,
    *,
    run_id: str,
    state_id: str,
    completeness_id: str,
    occurrence: SemanticOccurrence,
    source_entity_id: str,
    target_entity_id: str,
    location_id: str,
) -> None:
    category, predicate = {
        SemanticRelationship.REFERENCE: (RelationshipCategory.REFERENCE, "references"),
        SemanticRelationship.CALL: (RelationshipCategory.CALL, "calls"),
        SemanticRelationship.IMPLEMENTATION: (RelationshipCategory.IMPLEMENTATION, "implements"),
        SemanticRelationship.OVERRIDE: (RelationshipCategory.OVERRIDE, "overrides"),
    }[occurrence.relationship]
    edge = EdgeRecord(
        edge_id=content_id("edge:lsp-occurrence", {"run": run_id, "occurrence": occurrence.occurrence_id}),
        source_state_id=state_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        category=category,
        predicate=predicate,
    )
    builder.edges[edge.edge_id] = edge
    _observe_edge(
        builder,
        run_id=run_id,
        state_id=state_id,
        completeness_id=completeness_id,
        edge=edge,
        location_id=location_id,
        rationale="The captured language server resolved this exact occurrence target.",
    )


def _observe_edge(
    builder: _BatchBuilder,
    *,
    run_id: str,
    state_id: str,
    completeness_id: str,
    edge: EdgeRecord,
    location_id: str,
    rationale: str,
) -> None:
    observation = StructuralObservation(
        observation_id=content_id("observation:lsp", {"run": run_id, "edge": edge.edge_id}),
        source_state_id=state_id,
        provider_run_id=run_id,
        method="captured-lsp-semantic-response",
        method_version=LSP_SEMANTIC_PROVIDER_VERSION,
        strength=EvidenceStrength.EXACT,
        stance=ObservationStance.SUPPORTS,
        location_ids=[location_id],
        completeness_id=completeness_id,
        rationale=rationale,
        target_type="edge",
        target_id=edge.edge_id,
    )
    builder.observations[observation.observation_id] = observation


def _external_entity(occurrence: SemanticOccurrence, state: SourceStateRecord) -> ExternalEntity:
    assert occurrence.external_target is not None
    target = occurrence.external_target
    return ExternalEntity(
        entity_id=content_id(
            "entity:external:lsp",
            {
                "state": state.state_id,
                "scheme": target.identity_scheme,
                "identity": target.external_identity,
            },
        ),
        source_state_id=state.state_id,
        display_name=target.display_name,
        identity_scheme=target.identity_scheme,
        external_identity=target.external_identity,
        entity_kind=target.entity_kind,
    )


def _add_degradation(
    builder: _BatchBuilder,
    *,
    run_id: str,
    state_id: str,
    code: str,
    summary: str,
    scope_type: str,
    scope_id: str,
    recoverable: bool,
    remediation: str | None,
    discriminator: str | None = None,
) -> None:
    builder.add_degradation(
        run_id=run_id,
        state_id=state_id,
        code=code,
        summary=summary,
        scope_type=scope_type,
        scope_id=scope_id,
        recoverable=recoverable,
        remediation=remediation,
        discriminator=discriminator,
    )


def _seal(
    *,
    artifact: LspSemanticArtifact,
    expected_state: SourceStateRecord,
    run_id: str,
    capabilities: list[str],
    repository_entity: RepositoryEntity,
    builder: _BatchBuilder,
    completeness_id: str,
    status: ProviderRunStatus,
    invocation: InvocationAuthority,
    provider_id: str,
    provider_version: str,
) -> ProviderEnvelope:
    completeness_status = {
        ProviderRunStatus.COMPLETE: CompletenessStatus.COMPLETE,
        ProviderRunStatus.PARTIAL: CompletenessStatus.PARTIAL,
        ProviderRunStatus.UNAVAILABLE: CompletenessStatus.UNAVAILABLE,
        ProviderRunStatus.FAILED: CompletenessStatus.UNAVAILABLE,
        ProviderRunStatus.CANCELLED: CompletenessStatus.UNAVAILABLE,
    }[status]
    completeness = CompletenessRecord(
        completeness_id=completeness_id,
        source_state_id=expected_state.state_id,
        provider_run_id=run_id,
        scope_type="repository",
        scope_id=repository_entity.entity_id,
        evidence_families=capabilities,
        status=completeness_status,
        omission_ids=sorted(builder.omissions),
    )
    payload = builder.build_payload([completeness])
    return build_provider_envelope(
        provider_run_id=run_id,
        provider_id=provider_id,
        provider_version=provider_version,
        tool=ProviderToolIdentity(
            name=artifact.tool.name,
            version=artifact.tool.version,
            executable_digest=artifact.tool.executable_digest,
        ),
        capabilities=capabilities,
        languages=artifact.languages,
        repository_id=expected_state.repository_id,
        source_states=[expected_state],
        primary_source_state_id=expected_state.state_id,
        configuration_digest=artifact.configuration_digest,
        scope=ProviderScope(
            scope_id=content_id("scope:lsp", {"run": run_id}),
            source_state_ids=[expected_state.state_id],
            paths=sorted(item.path for item in artifact.documents),
            entity_ids=sorted(builder.entities),
            evidence_families=capabilities,
        ),
        invocation=invocation,
        status=status,
        payload=payload,
    )


def _reason_for_role(role: SymbolRole) -> IdentityReason:
    return {
        SymbolRole.DEFINITION: IdentityReason.DECLARED_ALIAS,
        SymbolRole.DECLARATION: IdentityReason.DECLARED_ALIAS,
        SymbolRole.REEXPORT: IdentityReason.REEXPORT,
        SymbolRole.ALIAS: IdentityReason.DECLARED_ALIAS,
        SymbolRole.OVERLOAD: IdentityReason.OVERLOAD,
        SymbolRole.METHOD: IdentityReason.METHOD_SCOPE,
    }[role]


def _source_digest(source: str) -> str:
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"
