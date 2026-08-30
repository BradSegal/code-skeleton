"""Import saved results from other tools through one data-only contract.

This module never discovers or runs tools, executes repository code, follows
external links, or treats a tool's output as trusted source fact. Callers
supply saved result bytes and the exact repository state they describe.
"""

from __future__ import annotations

import tokenize
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from anatomize._artifacts import content_id, sha256_digest
from anatomize._errors import AnatomizeError
from anatomize._paths import resolve_inside
from anatomize.diagnostics import build_sarif_binding, normalize_sarif_log, parse_sarif_log
from anatomize.evidence import (
    ArtifactEntity,
    CompletenessRecord,
    CompletenessStatus,
    ConfigurationEntity,
    DataEntity,
    DiagnosticEntity,
    DocumentationEntity,
    EdgeRecord,
    FileCoordinateSpace,
    FileEntity,
    LocationOrigin,
    LocationRecord,
    NotebookCellCoordinateSpace,
    ProviderRunStatus,
    RangeEntity,
    RelationshipCategory,
    RepositoryEvidence,
    SourcePosition,
    SourceRange,
    SymbolEntity,
    TestEntity,
)
from anatomize.lifecycle import (
    SimilarityQuery,
    extract_python_test_intent,
    jscpd_provider_envelope,
    parse_coverage_json,
    parse_junit_xml,
    parse_mutation_json,
    test_runtime_provider_envelope,
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
from anatomize.research import (
    extract_r_repository,
    parse_cyclonedx_sbom,
    parse_executable_document,
    parse_jupyter_notebook,
    parse_renv_lock,
    parse_ro_crate,
    parse_snakemake_workflow,
    parse_targets_manifest,
    research_graph_provider_envelope,
)
from anatomize.semantic import normalize_lsp_semantic_artifact, parse_lsp_semantic_artifact
from anatomize.version import __version__


class ProviderArtifactKind(str, Enum):
    """Captured artifact families understood by the public review gateway."""

    SARIF = "sarif"
    LSP = "lsp"
    JUNIT = "junit"
    COVERAGE = "coverage"
    MUTATION = "mutation"
    JSCPD = "jscpd"
    SNAKEMAKE = "snakemake"
    TARGETS = "targets"
    RENV = "renv"
    CYCLONEDX = "cyclonedx"
    RO_CRATE = "ro-crate"


@dataclass(frozen=True)
class ProviderArtifactInput:
    """One caller-selected, data-only provider artifact.

    ``metadata`` and ``network`` are used only by the targets adapter.
    ``selection`` records the exact test selection represented by runtime
    artifacts; it is never inferred from a passing result.
    """

    kind: ProviderArtifactKind
    content: bytes
    provider_version: str = "unknown"
    selection: tuple[str, ...] = ()
    environment_digest: str | None = None
    metadata: bytes | None = None
    network: bytes | None = None
    threshold: float | None = None


_IMPORT_POLICY_DIGEST = sha256_digest(b"anatomize:artifact-import:no-execution:no-network:1")
_SOURCE_POLICY_DIGEST = sha256_digest(b"anatomize:built-in-source-inventory:no-execution:1")


def normalize_builtin_source_facts(
    *,
    baseline: RepositoryEvidence,
    root: Path,
) -> ProviderEnvelope | None:
    """Extract R, notebook, and test intent facts already inside the source state.

    This is deliberately one small built-in provider rather than parallel
    language-specific graphs. It reads only baseline-inventoried, root-contained
    files and never executes Python, R, notebooks, or workflow tools.
    """
    if len(baseline.states) != 1:
        raise ValueError("built-in source inventory requires exactly one baseline state")
    state = baseline.states[0]
    files = {
        item.path: item
        for item in baseline.entities
        if isinstance(item, FileEntity)
    }
    selected = {
        path
        for path, file in files.items()
        if (
            file.language in {"python", "r", "jupyter"}
            or Path(path).suffix.casefold() in {".qmd", ".rmd"}
            or Path(path).name in {"DESCRIPTION", "NAMESPACE"}
        )
    }
    if not selected:
        return None
    sources = _trusted_sources(root, baseline, paths=selected)
    run_id = content_id(
        "provider-run:source-inventory",
        {"state": state.state_id, "paths": sorted(sources), "version": __version__},
    )
    configuration_digest = sha256_digest(b"anatomize:source-inventory:1")
    builder = ProviderBatchBuilder(baseline)
    repository = builder.repository_entity(state)
    limitations: list[tuple[str, str, str]] = []

    symbols = [item for item in baseline.entities if isinstance(item, SymbolEntity)]
    for path, source in sorted(sources.items()):
        file = files[path]
        if file.language != "python" or "test" not in file.roles:
            continue
        try:
            python_artifact = extract_python_test_intent(
                source,
                repository_id=baseline.repository_id,
                source_state_id=state.state_id,
                path=path,
            )
        except ValueError as error:
            limitations.append(("python_test_parse", path, _stable_error_summary(error)))
            continue
        for intent in python_artifact.intents:
            test = TestEntity(
                entity_id=intent.intent_id,
                source_state_id=state.state_id,
                display_name=intent.name,
                location_ids=[_source_location(builder, file, intent.locator.start_line, intent.locator.end_line)],
                provider_run_ids=[run_id],
                test_kind="static_intent",
                framework=intent.framework,
                test_name=intent.name,
            )
            builder.entities[test.entity_id] = test
            _reference_edges(
                builder,
                run_id=run_id,
                state_id=state.state_id,
                source_entity_id=test.entity_id,
                references=intent.targets,
                symbols=symbols,
                category=RelationshipCategory.TEST,
                predicate="statically_exercises",
            )

    r_sources = {
        path: source
        for path, source in sources.items()
        if files[path].language == "r" or Path(path).name in {"DESCRIPTION", "NAMESPACE"}
    }
    if r_sources:
        try:
            r_artifact = extract_r_repository(
                r_sources,
                repository_id=baseline.repository_id,
                source_state_id=state.state_id,
            )
            r_symbols: list[SymbolEntity] = []
            for function in r_artifact.functions:
                file = files[function.locator.path]
                r_entity = SymbolEntity(
                    entity_id=function.function_id,
                    source_state_id=state.state_id,
                    display_name=function.qualified_name,
                    location_ids=[
                        _source_location(
                            builder,
                            file,
                            function.locator.start_line,
                            function.locator.end_line,
                        )
                    ],
                    provider_run_ids=[run_id],
                    language="r",
                    symbol_kind=function.kind.value,
                    name=function.name,
                    qualified_name=function.qualified_name,
                    public=function.exported,
                )
                builder.entities[r_entity.entity_id] = r_entity
                r_symbols.append(r_entity)
            for call in r_artifact.calls:
                if call.caller_id is None:
                    continue
                _reference_edges(
                    builder,
                    run_id=run_id,
                    state_id=state.state_id,
                    source_entity_id=call.caller_id,
                    references=[call.target],
                    symbols=r_symbols,
                    category=RelationshipCategory.CALL,
                    predicate="static_call",
                )
            for intent in r_artifact.tests:
                file = files[intent.locator.path]
                test_entity = TestEntity(
                    entity_id=intent.intent_id,
                    source_state_id=state.state_id,
                    display_name=intent.name,
                    location_ids=[
                        _source_location(builder, file, intent.locator.start_line, intent.locator.end_line)
                    ],
                    provider_run_ids=[run_id],
                    test_kind="static_intent",
                    framework=intent.framework,
                    test_name=intent.name,
                )
                builder.entities[test_entity.entity_id] = test_entity
                _reference_edges(
                    builder,
                    run_id=run_id,
                    state_id=state.state_id,
                    source_entity_id=test_entity.entity_id,
                    references=intent.targets,
                    symbols=r_symbols,
                    category=RelationshipCategory.TEST,
                    predicate="statically_exercises",
                )
            for declaration in r_artifact.data_declarations:
                file = files[declaration.locator.path]
                data_entity = DataEntity(
                    entity_id=declaration.declaration_id,
                    source_state_id=state.state_id,
                    display_name=declaration.target or declaration.operation,
                    location_ids=[
                        _source_location(
                            builder,
                            file,
                            declaration.locator.start_line,
                            declaration.locator.end_line,
                        )
                    ],
                    provider_run_ids=[run_id],
                    data_kind=f"r:{declaration.operation}",
                )
                builder.entities[data_entity.entity_id] = data_entity
            for item in r_artifact.limitations:
                scope = next((path for path in r_sources if item.startswith(f"{path}:")), "repository")
                limitations.append(("r_inventory", scope, item))
        except ValueError as error:
            limitations.append(("r_inventory_parse", "repository", _stable_error_summary(error)))

    for path in sorted(selected):
        suffix = Path(path).suffix.casefold()
        if suffix not in {".ipynb", ".qmd", ".rmd"}:
            continue
        file = files[path]
        source_path = resolve_inside(root, path, purpose="notebook source inventory")
        try:
            notebook = (
                parse_jupyter_notebook(
                    source_path.read_bytes(),
                    repository_id=baseline.repository_id,
                    source_state_id=state.state_id,
                    path=path,
                )
                if suffix == ".ipynb"
                else parse_executable_document(
                    sources[path],
                    repository_id=baseline.repository_id,
                    source_state_id=state.state_id,
                    path=path,
                )
            )
        except (OSError, ValueError) as error:
            limitations.append(("notebook_parse", path, _stable_error_summary(error)))
            continue
        for cell in notebook.cells:
            location_id = _notebook_location(builder, file, cell)
            if cell.kind.value == "markdown":
                cell_entity: DocumentationEntity | RangeEntity = DocumentationEntity(
                    entity_id=cell.cell_key,
                    source_state_id=state.state_id,
                    display_name=f"{path} cell {cell.locator.ordinal}",
                    location_ids=[location_id],
                    provider_run_ids=[run_id],
                    documentation_kind="notebook_markdown_cell",
                    digest=cell.source_digest,
                )
            else:
                cell_entity = RangeEntity(
                    entity_id=cell.cell_key,
                    source_state_id=state.state_id,
                    display_name=f"{path} cell {cell.locator.ordinal}",
                    location_ids=[location_id],
                    provider_run_ids=[run_id],
                    range_kind=f"notebook_{cell.kind.value}_cell",
                    parent_entity_id=file.entity_id,
                )
            builder.entities[cell_entity.entity_id] = cell_entity
            _reference_edges(
                builder,
                run_id=run_id,
                state_id=state.state_id,
                source_entity_id=cell_entity.entity_id,
                references=cell.references,
                symbols=[*symbols, *[item for item in builder.entities.values() if isinstance(item, SymbolEntity)]],
                category=(
                    RelationshipCategory.DOCUMENTATION
                    if cell.kind.value == "markdown"
                    else RelationshipCategory.REFERENCE
                ),
                predicate="mentions" if cell.kind.value == "markdown" else "static_reference",
            )
            if cell.parameter:
                parameter = ConfigurationEntity(
                    entity_id=content_id("entity:notebook-parameters", {"cell": cell.cell_key}),
                    source_state_id=state.state_id,
                    display_name=f"Parameters: {path} cell {cell.locator.ordinal}",
                    location_ids=[location_id],
                    provider_run_ids=[run_id],
                    configuration_kind="notebook_parameters",
                    key=cell.native_cell_id or str(cell.locator.ordinal),
                )
                builder.entities[parameter.entity_id] = parameter
            for output in [*cell.outputs, *cell.attachments]:
                output_entity = ArtifactEntity(
                    entity_id=output.inventory_id,
                    source_state_id=state.state_id,
                    display_name=output.name or f"{path} cell output",
                    location_ids=[location_id],
                    provider_run_ids=[run_id],
                    artifact_kind="notebook_embedded_output",
                    digest=output.digest,
                    media_type=",".join(output.media_types),
                    generated=output in cell.outputs,
                )
                builder.entities[output_entity.entity_id] = output_entity
            if cell.output_state.value == "stale":
                diagnostic = DiagnosticEntity(
                    entity_id=content_id("entity:notebook-stale-output", {"cell": cell.cell_key}),
                    source_state_id=state.state_id,
                    display_name=f"Stale output: {path} cell {cell.locator.ordinal}",
                    location_ids=[location_id],
                    provider_run_ids=[run_id],
                    rule_id="notebook-output-stale",
                    severity="warning",
                    message="Stored output was produced from a different source digest.",
                )
                builder.entities[diagnostic.entity_id] = diagnostic
        limitations.extend(("notebook_inventory", path, item) for item in notebook.limitations)

    for index, (code, scope, summary) in enumerate(limitations):
        builder.add_degradation(
            run_id=run_id,
            state_id=state.state_id,
            code=code,
            summary=summary,
            scope_type="path" if scope != "repository" else "repository",
            scope_id=scope if scope != "repository" else repository.entity_id,
            remediation="Attach qualified semantic or runtime evidence when this boundary affects the decision.",
            discriminator=f"{index}:{scope}",
        )
    completeness_id = content_id("completeness:source-inventory", {"run": run_id})
    completeness = CompletenessRecord(
        completeness_id=completeness_id,
        source_state_id=state.state_id,
        provider_run_id=run_id,
        scope_type="repository",
        scope_id=repository.entity_id,
        evidence_families=["notebook_structure", "r_symbols", "static_test_intent"],
        status=CompletenessStatus.PARTIAL if limitations else CompletenessStatus.COMPLETE,
        omission_ids=sorted(builder.omissions),
    )
    return build_provider_envelope(
        provider_run_id=run_id,
        provider_id="anatomize.source-inventory",
        provider_version=__version__,
        tool=ProviderToolIdentity(name="anatomize.source-inventory", version=__version__),
        capabilities=completeness.evidence_families,
        languages=sorted(
            {file.language for path, file in files.items() if path in selected and file.language is not None}
        ),
        repository_id=baseline.repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=configuration_digest,
        scope=ProviderScope(
            scope_id=content_id("provider-scope:source-inventory", {"run": run_id}),
            source_state_ids=[state.state_id],
            paths=sorted(selected),
            entity_ids=sorted(builder.entities),
            evidence_families=completeness.evidence_families,
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.BUILT_IN,
            level=AuthorityLevel.A0_BASELINE,
            policy_digest=_SOURCE_POLICY_DIGEST,
        ),
        status=ProviderRunStatus.PARTIAL if limitations else ProviderRunStatus.COMPLETE,
        payload=builder.build_payload([completeness]),
    )


def _stable_error_summary(error: OSError | ValueError) -> str:
    if isinstance(error, AnatomizeError):
        return f"{error.code}: {error}"
    message = str(error).strip()
    if message and "\n" not in message:
        return message[:240]
    return f"{type(error).__name__}: invalid source evidence"


def _source_location(
    builder: ProviderBatchBuilder,
    file: FileEntity,
    start_line: int,
    end_line: int,
) -> str:
    builder.include_baseline_entity(file)
    location_id = content_id(
        "location:source-inventory",
        {"file": file.entity_id, "start": start_line, "end": end_line},
    )
    builder.locations[location_id] = LocationRecord(
        location_id=location_id,
        source_state_id=file.source_state_id,
        origin=LocationOrigin.REPOSITORY,
        file_id=file.entity_id,
        path=file.path,
        source_range=SourceRange(
            start=SourcePosition(line=start_line, column=0),
            end=SourcePosition(line=end_line, column=0),
        ),
        coordinate_space=FileCoordinateSpace(),
    )
    return location_id


def _notebook_location(builder: ProviderBatchBuilder, file: FileEntity, cell: object) -> str:
    from anatomize.research import NotebookCell

    if not isinstance(cell, NotebookCell):
        raise TypeError("notebook location requires a NotebookCell")
    builder.include_baseline_entity(file)
    location_id = content_id("location:notebook-cell", {"cell": cell.cell_key})
    builder.locations[location_id] = LocationRecord(
        location_id=location_id,
        source_state_id=file.source_state_id,
        origin=LocationOrigin.REPOSITORY,
        file_id=file.entity_id,
        path=file.path,
        source_range=SourceRange(
            start=SourcePosition(line=cell.locator.start_line, column=0),
            end=SourcePosition(line=cell.locator.end_line, column=0),
        ),
        coordinate_space=NotebookCellCoordinateSpace(
            cell_id=cell.native_cell_id or cell.cell_key,
            cell_index=cell.locator.ordinal,
            source_digest=cell.source_digest,
        ),
    )
    return location_id


def _reference_edges(
    builder: ProviderBatchBuilder,
    *,
    run_id: str,
    state_id: str,
    source_entity_id: str,
    references: list[str],
    symbols: list[SymbolEntity],
    category: RelationshipCategory,
    predicate: str,
) -> None:
    """Add only unambiguous lexical relations; unresolved names remain unknown."""
    for reference in sorted(set(references)):
        candidates = [
            symbol
            for symbol in symbols
            if reference == symbol.name
            or reference == symbol.qualified_name
            or reference.endswith(f".{symbol.name}")
            or reference.endswith(f"::{symbol.name}")
        ]
        unique = {item.entity_id: item for item in candidates}
        if len(unique) != 1:
            continue
        target = next(iter(unique.values()))
        builder.include_baseline_entity(target)
        edge = EdgeRecord(
            edge_id=content_id(
                "edge:source-inventory",
                {"source": source_entity_id, "target": target.entity_id, "predicate": predicate},
            ),
            source_state_id=state_id,
            source_entity_id=source_entity_id,
            target_entity_id=target.entity_id,
            category=category,
            predicate=predicate,
            provider_run_ids=[run_id],
        )
        builder.edges[edge.edge_id] = edge


def normalize_provider_artifact(
    artifact: ProviderArtifactInput,
    *,
    baseline: RepositoryEvidence,
    root: Path,
) -> tuple[ProviderEnvelope, ...]:
    """Normalize one captured artifact against an exact baseline state."""
    if len(baseline.states) != 1:
        raise ValueError("provider artifact import requires exactly one baseline state")
    state = baseline.states[0]
    repository_id = baseline.repository_id
    digest = sha256_digest(artifact.content)
    run_id = content_id(
        "provider-run:artifact-import",
        {
            "kind": artifact.kind.value,
            "digest": digest,
            "state": state.state_id,
            "version": artifact.provider_version,
            "selection": list(artifact.selection),
            "metadata": sha256_digest(artifact.metadata) if artifact.metadata is not None else None,
            "network": sha256_digest(artifact.network) if artifact.network is not None else None,
        },
    )
    configuration_digest = sha256_digest(
        f"anatomize-import:{artifact.kind.value}:{artifact.provider_version}:1".encode()
    )
    environment_digest = artifact.environment_digest or sha256_digest(b"environment:not-declared")

    if artifact.kind is ProviderArtifactKind.SARIF:
        log = parse_sarif_log(artifact.content)
        binding = build_sarif_binding(log, source_state=state, configuration_digest=configuration_digest)
        return normalize_sarif_log(
            log,
            binding=binding,
            expected_state=state,
            expected_configuration_digest=configuration_digest,
            baseline=baseline,
            sources=_trusted_sources(root, baseline),
        )
    if artifact.kind is ProviderArtifactKind.LSP:
        semantic = parse_lsp_semantic_artifact(artifact.content)
        return (
            normalize_lsp_semantic_artifact(
                semantic,
                expected_state=state,
                baseline=baseline,
                sources=_trusted_sources(root, baseline, paths={item.path for item in semantic.documents}),
                expected_configuration_digest=semantic.configuration_digest,
                provider_id=semantic.tool.name,
                provider_version=semantic.tool.version,
            ),
        )
    if artifact.kind in {
        ProviderArtifactKind.JUNIT,
        ProviderArtifactKind.COVERAGE,
        ProviderArtifactKind.MUTATION,
    }:
        parser = {
            ProviderArtifactKind.JUNIT: parse_junit_xml,
            ProviderArtifactKind.COVERAGE: parse_coverage_json,
            ProviderArtifactKind.MUTATION: parse_mutation_json,
        }[artifact.kind]
        runtime = parser(
            artifact.content,
            repository_id=repository_id,
            source_state_id=state.state_id,
            provider_run_id=run_id,
            provider_version=artifact.provider_version,
            environment_digest=environment_digest,
            selection=list(artifact.selection),
        )
        return (
            test_runtime_provider_envelope(
                runtime,
                baseline=baseline,
                configuration_digest=configuration_digest,
                policy_digest=_IMPORT_POLICY_DIGEST,
            ),
        )
    if artifact.kind is ProviderArtifactKind.JSCPD:
        return (
            jscpd_provider_envelope(
                artifact.content,
                baseline=baseline,
                provider_run_id=run_id,
                provider_version=artifact.provider_version,
                configuration_digest=configuration_digest,
                policy_digest=_IMPORT_POLICY_DIGEST,
                query=SimilarityQuery(minimum_lines=3, minimum_tokens=1),
                repository_root=root,
                threshold=artifact.threshold,
            ),
        )

    graph = {
        ProviderArtifactKind.SNAKEMAKE: lambda: parse_snakemake_workflow(
            artifact.content,
            repository_id=repository_id,
            source_state_id=state.state_id,
            provider_run_id=run_id,
            provider_version=artifact.provider_version,
        ),
        ProviderArtifactKind.TARGETS: lambda: parse_targets_manifest(
            artifact.content,
            repository_id=repository_id,
            source_state_id=state.state_id,
            provider_run_id=run_id,
            provider_version=artifact.provider_version,
            metadata_raw=artifact.metadata,
            network_raw=artifact.network,
        ),
        ProviderArtifactKind.RENV: lambda: parse_renv_lock(
            artifact.content,
            repository_id=repository_id,
            source_state_id=state.state_id,
            provider_run_id=run_id,
            provider_version=artifact.provider_version,
        ),
        ProviderArtifactKind.CYCLONEDX: lambda: parse_cyclonedx_sbom(
            artifact.content,
            repository_id=repository_id,
            source_state_id=state.state_id,
            provider_run_id=run_id,
            provider_version=artifact.provider_version,
        ),
        ProviderArtifactKind.RO_CRATE: lambda: parse_ro_crate(
            artifact.content,
            repository_id=repository_id,
            source_state_id=state.state_id,
            provider_run_id=run_id,
            provider_version=artifact.provider_version,
        ),
    }.get(artifact.kind)
    if graph is None:  # pragma: no cover - exhaustive protection for future enum members
        raise ValueError(f"unsupported provider artifact kind: {artifact.kind.value}")
    return (
        research_graph_provider_envelope(
            graph(),
            baseline=baseline,
            policy_digest=_IMPORT_POLICY_DIGEST,
        ),
    )


def _trusted_sources(
    root: Path,
    baseline: RepositoryEvidence,
    *,
    paths: set[str] | None = None,
) -> dict[str, str]:
    """Read only baseline-known, root-contained text needed for coordinate checks."""
    known = sorted(
        item.path
        for item in baseline.entities
        if isinstance(item, FileEntity) and (paths is None or item.path in paths)
    )
    result: dict[str, str] = {}
    for path in known:
        source_path = resolve_inside(root, path, purpose="provider coordinate source")
        try:
            if source_path.suffix.casefold() == ".py":
                with tokenize.open(source_path) as handle:
                    result[path] = handle.read()
            else:
                result[path] = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, SyntaxError):
            # The normalizer records missing source as degraded evidence.  It
            # must not guess a coordinate system or decode arbitrary binaries.
            continue
    return result
