"""Normalize SARIF runs into canonical diagnostic provider envelopes."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from anatomize._artifacts import canonical_ordered_json_bytes, content_id, sha256_digest
from anatomize.diagnostics.models import (
    DeclaredDiagnosticContract,
    SarifArtifact,
    SarifArtifactBinding,
    SarifArtifactLocation,
    SarifFix,
    SarifLocation,
    SarifLog,
    SarifMessage,
    SarifRegion,
    SarifReportingDescriptor,
    SarifResult,
    SarifRun,
    sarif_digest,
)
from anatomize.evidence import (
    CompletenessRecord,
    CompletenessStatus,
    ContractRecord,
    DiagnosticEntity,
    DiagnosticFixSuggestion,
    DiagnosticObservation,
    DiagnosticSuppression,
    EvidenceStrength,
    ExternalEntity,
    FileCoordinateSpace,
    FileEntity,
    LocationOrigin,
    LocationRecord,
    ObservationStance,
    ProviderRunStatus,
    RepositoryEntity,
    RepositoryEvidence,
    SourceRange,
    SourceStateRecord,
    StructuralObservation,
    SymbolEntity,
    validate_repository_path,
)
from anatomize.identity import (
    ColumnEncoding,
    CoordinateConvention,
    CoordinateError,
    FileIdentityKey,
    PathKind,
    ProviderPosition,
    ProviderRange,
    SourceCoordinateMap,
    canonical_identity_id,
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

SARIF_PROVIDER_VERSION = "1.0.0"


@dataclass(frozen=True)
class _ResolvedLocation:
    location: LocationRecord
    subject_entity_ids: tuple[str, ...]
    repository_path: str | None


_Builder = ProviderBatchBuilder


def normalize_sarif_log(
    log: SarifLog,
    *,
    binding: SarifArtifactBinding,
    expected_state: SourceStateRecord,
    expected_configuration_digest: str,
    baseline: RepositoryEvidence,
    sources: Mapping[str, str],
    repository_uri_base_ids: Sequence[str] = (),
    declared_contracts: Mapping[str, DeclaredDiagnosticContract] | None = None,
    invocation: InvocationAuthority | None = None,
) -> tuple[ProviderEnvelope, ...]:
    """Normalize every SARIF run independently without comparing tool severities."""
    _validate_baseline(expected_state, baseline)
    invocation = invocation or InvocationAuthority(
        mode=InvocationMode.ARTIFACT_IMPORT,
        level=AuthorityLevel.A1_ARTIFACT,
        policy_digest="sarif-artifact-import-v1",
    )
    contracts = declared_contracts or {}
    envelopes = [
        _normalize_run(
            log=log,
            run=run,
            run_index=index,
            binding=binding,
            expected_state=expected_state,
            expected_configuration_digest=expected_configuration_digest,
            baseline=baseline,
            sources=sources,
            repository_uri_base_ids=frozenset(repository_uri_base_ids),
            declared_contracts=contracts,
            invocation=invocation,
        )
        for index, run in enumerate(log.runs)
    ]
    return tuple(envelopes)


def unavailable_diagnostic_envelope(
    *,
    tool_name: str,
    expected_state: SourceStateRecord,
    expected_configuration_digest: str,
    baseline: RepositoryEvidence,
    reason: str,
) -> ProviderEnvelope:
    """Record unavailable diagnostics without loading an external executable."""
    log = SarifLog(
        version="2.1.0",
        runs=[
            {
                "tool": {"driver": {"name": tool_name, "version": "unavailable"}},
                "invocations": [
                    {
                        "executionSuccessful": False,
                        "processStartFailureMessage": reason,
                    }
                ],
                "results": None,
            }
        ],
    )
    binding = SarifArtifactBinding(
        artifact_digest=sarif_digest(log),
        repository_id=expected_state.repository_id,
        source_state=expected_state,
        configuration_digest=expected_configuration_digest,
    )
    return normalize_sarif_log(
        log,
        binding=binding,
        expected_state=expected_state,
        expected_configuration_digest=expected_configuration_digest,
        baseline=baseline,
        sources={},
    )[0]


def _normalize_run(
    *,
    log: SarifLog,
    run: SarifRun,
    run_index: int,
    binding: SarifArtifactBinding,
    expected_state: SourceStateRecord,
    expected_configuration_digest: str,
    baseline: RepositoryEvidence,
    sources: Mapping[str, str],
    repository_uri_base_ids: frozenset[str],
    declared_contracts: Mapping[str, DeclaredDiagnosticContract],
    invocation: InvocationAuthority,
) -> ProviderEnvelope:
    tool = run.tool.driver
    provider_id = f"sarif:{_portable_tool_slug(tool.name)}"
    provider_version = tool.semantic_version or tool.version or "unknown"
    run_id = content_id(
        "provider-run:sarif",
        {
            "artifact_digest": binding.artifact_digest,
            "run_index": run_index,
            "tool": tool.model_dump(mode="json", by_alias=True, exclude_none=True),
            "binding": binding.model_dump(mode="json"),
            "expected_state": expected_state.model_dump(mode="json"),
            "expected_configuration_digest": expected_configuration_digest,
            "invocation": invocation.model_dump(mode="json"),
        },
    )
    builder = _Builder(baseline=baseline)
    repository_entity = builder.repository_entity(expected_state)
    completeness_id = content_id("completeness:sarif", {"run": run_id})
    fatal = _binding_failure(
        log,
        run,
        binding=binding,
        expected_state=expected_state,
        expected_configuration_digest=expected_configuration_digest,
    )
    if fatal is not None:
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code=fatal[0],
            summary=fatal[1],
            scope_type="repository",
            scope_id=repository_entity.entity_id,
            remediation=fatal[2],
        )
        return _seal(
            run=run,
            provider_id=provider_id,
            provider_version=provider_version,
            run_id=run_id,
            expected_state=expected_state,
            expected_configuration_digest=expected_configuration_digest,
            invocation=invocation,
            builder=builder,
            repository_entity=repository_entity,
            completeness_id=completeness_id,
            status=ProviderRunStatus.UNAVAILABLE,
        )

    execution_successful = [item.execution_successful for item in run.invocations]
    if not run.invocations:
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="invocation_absent",
            summary="The SARIF run did not report invocation state.",
            scope_type="repository",
            scope_id=repository_entity.entity_id,
            remediation="Export SARIF with invocation executionSuccessful metadata.",
        )
    for index, item in enumerate(run.invocations):
        if not item.execution_successful:
            detail = item.process_start_failure_message or item.exit_code_description
            summary = f"SARIF invocation {index} reported failure"
            if detail:
                summary = f"{summary}: {detail}"
            _degrade(
                builder,
                run_id=run_id,
                state_id=expected_state.state_id,
                code="tool_invocation_failed",
                summary=summary,
                scope_type="invocation",
                scope_id=f"invocation:{index}",
                remediation="Inspect how the analysis tool was run, then rerun it separately.",
            )
    if run.results is None:
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="results_unavailable",
            summary="The SARIF run omitted its results array; this is not an empty successful result set.",
            scope_type="repository",
            scope_id=repository_entity.entity_id,
            remediation="Export a completed SARIF run with an explicit results array.",
        )
        status = (
            ProviderRunStatus.FAILED
            if execution_successful and not all(execution_successful)
            else ProviderRunStatus.UNAVAILABLE
        )
        return _seal(
            run=run,
            provider_id=provider_id,
            provider_version=provider_version,
            run_id=run_id,
            expected_state=expected_state,
            expected_configuration_digest=expected_configuration_digest,
            invocation=invocation,
            builder=builder,
            repository_entity=repository_entity,
            completeness_id=completeness_id,
            status=status,
        )

    grouped = _deduplicate_results(run.results)
    for key, group in sorted(grouped.items()):
        result = group[0]
        if len(group) > 1:
            _degrade(
                builder,
                run_id=run_id,
                state_id=expected_state.state_id,
                code="duplicate_diagnostic_collapsed",
                summary=f"Collapsed {len(group)} byte-equivalent SARIF results with identity {key}.",
                scope_type="diagnostic",
                scope_id=key,
                remediation="Remove duplicate producer results if distinct review items were intended.",
            )
        _normalize_result(
            result,
            duplicate_count=len(group),
            run=run,
            run_id=run_id,
            expected_state=expected_state,
            baseline=baseline,
            sources=sources,
            repository_uri_base_ids=repository_uri_base_ids,
            declared_contracts=declared_contracts,
            builder=builder,
            repository_entity=repository_entity,
            completeness_id=completeness_id,
        )

    status = ProviderRunStatus.COMPLETE
    if builder.omissions or not run.invocations or not all(execution_successful):
        status = ProviderRunStatus.PARTIAL
    return _seal(
        run=run,
        provider_id=provider_id,
        provider_version=provider_version,
        run_id=run_id,
        expected_state=expected_state,
        expected_configuration_digest=expected_configuration_digest,
        invocation=invocation,
        builder=builder,
        repository_entity=repository_entity,
        completeness_id=completeness_id,
        status=status,
    )


def _normalize_result(
    result: SarifResult,
    *,
    duplicate_count: int,
    run: SarifRun,
    run_id: str,
    expected_state: SourceStateRecord,
    baseline: RepositoryEvidence,
    sources: Mapping[str, str],
    repository_uri_base_ids: frozenset[str],
    declared_contracts: Mapping[str, DeclaredDiagnosticContract],
    builder: _Builder,
    repository_entity: RepositoryEntity,
    completeness_id: str,
) -> None:
    rule_id, rule = _resolve_rule(result, run)
    if rule_id is None:
        rule_id = "sarif-rule:absent"
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="rule_identity_absent",
            summary="A SARIF result has neither ruleId nor a resolvable ruleIndex.",
            scope_type="diagnostic",
            scope_id=_result_identity(result),
            remediation="Configure the producer or converter to emit stable rule identities.",
        )
    elif rule is None:
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="rule_metadata_absent",
            summary=f"SARIF rule {rule_id} has no reporting descriptor in the tool driver.",
            scope_type="rule",
            scope_id=rule_id,
            remediation="Export rule metadata when the analysis tool supports it.",
        )
    message = _render_message(result.message, rule)
    location_attempts = [
        _resolve_location(
            item,
            result=result,
            run=run,
            run_id=run_id,
            expected_state=expected_state,
            baseline=baseline,
            sources=sources,
            repository_uri_base_ids=repository_uri_base_ids,
            builder=builder,
        )
        for item in result.locations
    ]
    resolved_locations = [item for item in location_attempts if item is not None]
    location_ids = sorted({item.location.location_id for item in resolved_locations})
    subjects = sorted(
        {
            subject
            for item in resolved_locations
            for subject in item.subject_entity_ids
        }
    )
    if not subjects:
        subjects = [repository_entity.entity_id]
    if not result.locations:
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="diagnostic_location_absent",
            summary=f"SARIF result for {rule_id} has no location.",
            scope_type="rule",
            scope_id=rule_id,
            remediation="Export a physical or logical location when the rule supports one.",
        )
    fingerprints = {
        **{f"full:{key}": value for key, value in result.fingerprints.items()},
        **{f"partial:{key}": value for key, value in result.partial_fingerprints.items()},
    }
    diagnostic_id = content_id(
        "entity:diagnostic:sarif",
        {
            "state": expected_state.state_id,
            "tool": run.tool.driver.name,
            "tool_version": run.tool.driver.semantic_version or run.tool.driver.version,
            "rule": rule_id,
            "message": message,
            "locations": location_ids,
            "fingerprints": fingerprints,
            "level": result.level,
            "kind": result.kind,
            "baseline_state": result.baseline_state,
            "suppressions": [item.model_dump(mode="json") for item in result.suppressions or []],
            "fixes": [item.model_dump(mode="json", by_alias=True) for item in result.fixes],
        },
    )
    help_uri = rule.help_uri if rule is not None else None
    if help_uri is not None and _is_local_absolute_uri(help_uri):
        help_uri = None
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="local_rule_help_uri_redacted",
            summary=f"Rule {rule_id} supplied a checkout-specific help URI; it was not retained.",
            scope_type="rule",
            scope_id=rule_id,
            remediation="Use a portable HTTPS rule help URI.",
        )
    diagnostic = DiagnosticEntity(
        entity_id=diagnostic_id,
        source_state_id=expected_state.state_id,
        display_name=f"{run.tool.driver.name}:{rule_id}",
        location_ids=location_ids,
        rule_id=rule_id,
        rule_name=rule.name if rule is not None else None,
        rule_help_uri=help_uri,
        severity=_effective_level(result, rule),
        result_kind=result.kind,
        baseline_state=result.baseline_state,
        message=message,
        suppressions=[
            DiagnosticSuppression(
                kind=item.kind,
                status=item.status,
                justification=item.justification,
            )
            for item in result.suppressions or []
        ],
        fix_suggestions=[_fix_suggestion(item, diagnostic_id, index) for index, item in enumerate(result.fixes)],
        fingerprints=fingerprints,
        duplicate_count=duplicate_count,
    )
    builder.entities[diagnostic.entity_id] = diagnostic
    observation = DiagnosticObservation(
        observation_id=content_id("observation:sarif", {"run": run_id, "diagnostic": diagnostic_id}),
        source_state_id=expected_state.state_id,
        provider_run_id=run_id,
        method="sarif-2.1.0-result",
        method_version=SARIF_PROVIDER_VERSION,
        strength=EvidenceStrength.EXACT,
        stance=ObservationStance.SUPPORTS,
        location_ids=location_ids,
        completeness_id=completeness_id,
        rationale=(
            "This is an exact external tool observation; its severity and message remain producer-declared "
            "and are not an Anatomize quality judgement."
        ),
        diagnostic_entity_id=diagnostic.entity_id,
        subject_entity_ids=subjects,
    )
    builder.observations[observation.observation_id] = observation
    descriptor = declared_contracts.get(f"{run.tool.driver.name}:{rule_id}") or declared_contracts.get(rule_id)
    if descriptor is not None:
        contract = ContractRecord(
            contract_id=content_id(
                "contract:sarif",
                {
                    "state": expected_state.state_id,
                    "rule": rule_id,
                    "subjects": subjects,
                    "terms": descriptor.terms_digest,
                },
            ),
            source_state_id=expected_state.state_id,
            kind=descriptor.kind,
            subject_entity_ids=subjects,
            declaration_location_ids=location_ids,
            provider_run_ids=[run_id],
            summary=descriptor.summary,
            terms_digest=descriptor.terms_digest,
        )
        builder.contracts[contract.contract_id] = contract
        contract_observation = StructuralObservation(
            observation_id=content_id(
                "observation:declared-contract",
                {"run": run_id, "contract": contract.contract_id},
            ),
            source_state_id=expected_state.state_id,
            provider_run_id=run_id,
            method="caller-declared-contract-binding",
            method_version="1.0.0",
            strength=EvidenceStrength.DECLARED,
            stance=ObservationStance.SUPPORTS,
            location_ids=location_ids,
            completeness_id=completeness_id,
            rationale="The caller declared this contract separately from the tool diagnostic.",
            target_type="contract",
            target_id=contract.contract_id,
        )
        builder.observations[contract_observation.observation_id] = contract_observation


def _resolve_location(
    location: SarifLocation,
    *,
    result: SarifResult,
    run: SarifRun,
    run_id: str,
    expected_state: SourceStateRecord,
    baseline: RepositoryEvidence,
    sources: Mapping[str, str],
    repository_uri_base_ids: frozenset[str],
    builder: _Builder,
) -> _ResolvedLocation | None:
    artifact_location = (
        location.physical_location.artifact_location
        if location.physical_location is not None
        else None
    )
    uri, artifact = _artifact_uri(run, artifact_location)
    path = _repository_path(
        uri,
        artifact_location,
        repository_uri_base_ids,
        known_paths=frozenset(sources),
        declared_base_ids=frozenset(run.original_uri_base_ids),
    )
    logical_names: list[str] = []
    for logical_location in location.logical_locations:
        name = logical_location.fully_qualified_name or logical_location.name
        if name is not None:
            logical_names.append(name)
    if path is None:
        token = content_id(
            "sarif-external-location",
            {"uri": uri, "base": artifact_location.uri_base_id if artifact_location else None},
        )
        external = ExternalEntity(
            entity_id=content_id("entity:external:sarif", {"state": expected_state.state_id, "token": token}),
            source_state_id=expected_state.state_id,
            display_name="external SARIF artifact",
            location_ids=[token],
            identity_scheme="sarif-artifact-location-digest",
            external_identity=token,
            entity_kind="artifact",
        )
        external_location = LocationRecord(
            location_id=token,
            source_state_id=expected_state.state_id,
            origin=LocationOrigin.EXTERNAL,
            opaque_locator=token,
        )
        builder.entities[external.entity_id] = external
        builder.locations[token] = external_location
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="external_artifact_location",
            summary="A SARIF location is outside the declared repository URI bases and remains opaque.",
            scope_type="external_location",
            scope_id=token,
            remediation="Export repository-relative artifact URIs or declare the repository URI base explicitly.",
        )
        return _ResolvedLocation(external_location, (external.entity_id,), None)

    source = sources.get(path)
    if artifact is not None and source is not None and not _artifact_hash_matches(artifact, source):
        token = content_id("sarif-stale-location", {"path": path, "result": _result_identity(result)})
        external = ExternalEntity(
            entity_id=content_id("entity:external:sarif", {"state": expected_state.state_id, "token": token}),
            source_state_id=expected_state.state_id,
            display_name=f"stale SARIF artifact {PurePosixPath(path).name}",
            location_ids=[token],
            identity_scheme="sarif-stale-artifact-digest",
            external_identity=token,
            entity_kind="artifact",
        )
        opaque = LocationRecord(
            location_id=token,
            source_state_id=expected_state.state_id,
            origin=LocationOrigin.OPAQUE,
            opaque_locator=token,
        )
        builder.entities[external.entity_id] = external
        builder.locations[token] = opaque
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="artifact_hash_mismatch",
            summary=f"SARIF artifact hash for {path} does not match the selected source.",
            scope_type="path",
            scope_id=path,
            remediation="Rerun the analysis tool against the selected checkout.",
        )
        return _ResolvedLocation(opaque, (external.entity_id,), None)

    file_entity = _file_entity(path, artifact, expected_state, baseline, builder, source)
    source_range = None
    region = location.physical_location.region if location.physical_location is not None else None
    if region is not None:
        source_range = _sarif_range(
            region,
            column_kind=run.column_kind,
            source=source,
            path=path,
            run_id=run_id,
            state_id=expected_state.state_id,
            builder=builder,
        )
    location_id = content_id(
        "location:sarif",
        {
            "state": expected_state.state_id,
            "path": path,
            "range": source_range.model_dump(mode="json") if source_range else None,
            "logical": logical_names,
            "result": _result_identity(result),
        },
    )
    record = LocationRecord(
        location_id=location_id,
        source_state_id=expected_state.state_id,
        origin=LocationOrigin.REPOSITORY,
        file_id=file_entity.entity_id,
        path=path,
        source_range=source_range,
        coordinate_space=FileCoordinateSpace(),
    )
    builder.locations[record.location_id] = record
    builder.paths.add(path)
    symbols = _logical_symbol_matches(path, logical_names, baseline)
    for symbol in symbols:
        builder.include_baseline_entity(symbol)
    subjects = tuple(sorted(item.entity_id for item in symbols)) or (file_entity.entity_id,)
    if len(symbols) > 1:
        _degrade(
            builder,
            run_id=run_id,
            state_id=expected_state.state_id,
            code="logical_location_ambiguous",
            summary=f"SARIF logical location in {path} matches multiple canonical symbols.",
            scope_type="path",
            scope_id=path,
            remediation="Export a more precise physical range or fully-qualified logical name.",
        )
    return _ResolvedLocation(record, subjects, path)


def _sarif_range(
    region: SarifRegion,
    *,
    column_kind: str | None,
    source: str | None,
    path: str,
    run_id: str,
    state_id: str,
    builder: _Builder,
) -> SourceRange | None:
    if region.start_line is None:
        _coordinate_degradation(builder, run_id, state_id, path, "SARIF region has no line coordinates.")
        return None
    # SARIF 2.1.0 defaults columnKind to UTF-16 code units. Apply the
    # standard default instead of discarding otherwise exact locations.
    column_kind = column_kind or "utf16CodeUnits"
    start_column = region.start_column or 1
    end_line = region.end_line or region.start_line
    end_column = region.end_column
    if end_column is None:
        if source is None:
            _coordinate_degradation(
                builder,
                run_id,
                state_id,
                path,
                "SARIF endColumn defaults require source text, but source is unavailable.",
            )
            return None
        try:
            end_column = _encoded_line_width(source, end_line, column_kind) + 1
        except IndexError:
            _coordinate_degradation(builder, run_id, state_id, path, "SARIF endLine is outside source.")
            return None
    if source is None:
        if column_kind == "unicodeCodePoints":
            from anatomize.evidence import SourcePosition

            return SourceRange(
                start=SourcePosition(line=region.start_line, column=start_column - 1),
                end=SourcePosition(line=end_line, column=end_column - 1),
            )
        _coordinate_degradation(
            builder,
            run_id,
            state_id,
            path,
            "UTF-16 SARIF coordinates require source text for exact conversion.",
        )
        return None
    convention = CoordinateConvention(
        line_base=1,
        column_base=1,
        column_encoding=(
            ColumnEncoding.UTF16_CODE_UNIT
            if column_kind == "utf16CodeUnits"
            else ColumnEncoding.UNICODE_CODEPOINT
        ),
    )
    try:
        return SourceCoordinateMap(source).to_canonical(
            ProviderRange(
                start=ProviderPosition(line=region.start_line, column=start_column),
                end=ProviderPosition(line=end_line, column=end_column),
            ),
            convention,
        )
    except CoordinateError as error:
        _coordinate_degradation(builder, run_id, state_id, path, f"{error.code}: {error}")
        return None


def _coordinate_degradation(
    builder: _Builder,
    run_id: str,
    state_id: str,
    path: str,
    detail: str,
) -> None:
    _degrade(
        builder,
        run_id=run_id,
        state_id=state_id,
        code="sarif_coordinate_unavailable",
        summary=f"Cannot project SARIF coordinates for {path}: {detail}",
        scope_type="path",
        scope_id=path,
        remediation="Export line coordinates with columnKind and provide the exact analyzed source.",
    )


def _artifact_uri(
    run: SarifRun,
    location: SarifArtifactLocation | None,
) -> tuple[str | None, SarifArtifact | None]:
    if location is None:
        return None, None
    artifact = None
    if location.index is not None and location.index < len(run.artifacts):
        artifact = run.artifacts[location.index]
    uri = location.uri
    if uri is None and artifact is not None and artifact.location is not None:
        uri = artifact.location.uri
    if artifact is None and uri is not None:
        artifact = next(
            (
                item
                for item in run.artifacts
                if item.location is not None and item.location.uri == uri
            ),
            None,
        )
    return uri, artifact


def _repository_path(
    uri: str | None,
    location: SarifArtifactLocation | None,
    repository_uri_base_ids: frozenset[str],
    *,
    known_paths: frozenset[str],
    declared_base_ids: frozenset[str],
) -> str | None:
    if uri is None:
        return None
    if location is not None and location.uri_base_id is not None:
        if (
            location.uri_base_id not in repository_uri_base_ids
            and location.uri_base_id not in declared_base_ids
        ):
            return None
    parsed = urlsplit(uri)
    if parsed.query or parsed.fragment or (parsed.scheme and parsed.scheme != "file"):
        return None
    value = unquote(parsed.path)
    if parsed.scheme == "file" or parsed.netloc:
        suffix_matches = sorted(
            path for path in known_paths if value.replace("\\", "/").endswith(f"/{path}")
        )
        return suffix_matches[0] if len(suffix_matches) == 1 else None
    try:
        path = validate_repository_path(value)
    except ValueError:
        return None
    return path if path in known_paths else None


def _file_entity(
    path: str,
    artifact: SarifArtifact | None,
    state: SourceStateRecord,
    baseline: RepositoryEvidence,
    builder: _Builder,
    source: str | None,
) -> FileEntity:
    matches = [
        item
        for item in baseline.entities
        if isinstance(item, FileEntity)
        and item.source_state_id == state.state_id
        and item.path == path
    ]
    if len(matches) == 1:
        builder.include_baseline_entity(matches[0])
        return matches[0]
    digest = _source_digest(source) if source is not None else None
    entity_id = canonical_identity_id(
        FileIdentityKey(
            repository_id=state.repository_id,
            source_state_id=state.state_id,
            path=path,
            path_kind=PathKind.REGULAR,
        )
    )
    file_location_id = content_id("location:sarif-file", {"state": state.state_id, "path": path})
    location = LocationRecord(
        location_id=file_location_id,
        source_state_id=state.state_id,
        origin=LocationOrigin.REPOSITORY,
        file_id=entity_id,
        path=path,
        coordinate_space=FileCoordinateSpace(),
    )
    entity = FileEntity(
        entity_id=entity_id,
        source_state_id=state.state_id,
        display_name=path,
        location_ids=[file_location_id],
        path=path,
        language=artifact.source_language if artifact is not None else None,
        digest=digest,
        size_bytes=len(source.encode("utf-8")) if source is not None else 0,
        roles=list(artifact.roles) if artifact is not None else [],
    )
    builder.locations[location.location_id] = location
    builder.entities[entity.entity_id] = entity
    return entity


def _logical_symbol_matches(
    path: str,
    logical_names: list[str],
    baseline: RepositoryEvidence,
) -> list[SymbolEntity]:
    if not logical_names:
        return []
    locations = {item.location_id: item for item in baseline.locations}
    return [
        item
        for item in baseline.entities
        if isinstance(item, SymbolEntity)
        and item.qualified_name in logical_names
        and any(locations[location_id].path == path for location_id in item.location_ids)
    ]


def _resolve_rule(
    result: SarifResult,
    run: SarifRun,
) -> tuple[str | None, SarifReportingDescriptor | None]:
    rules = run.tool.driver.rules
    if result.rule_id is not None:
        return result.rule_id, next((item for item in rules if item.rule_id == result.rule_id), None)
    if result.rule_index is not None and result.rule_index < len(rules):
        rule = rules[result.rule_index]
        return rule.rule_id, rule
    return None, None


def _render_message(
    message: SarifMessage,
    rule: SarifReportingDescriptor | None,
) -> str:
    text = message.text or message.markdown
    if text is None and message.message_id is not None and rule is not None:
        template = rule.message_strings.get(message.message_id)
        if template is not None:
            text = template.text or template.markdown
    if text is None:
        text = f"message-id:{message.message_id or 'unavailable'}"
    for index, argument in enumerate(message.arguments):
        text = text.replace(f"{{{index}}}", argument)
    return text


def _effective_level(
    result: SarifResult,
    rule: SarifReportingDescriptor | None,
) -> str | None:
    if result.level is not None:
        return result.level
    if rule is not None and rule.default_configuration is not None:
        if rule.default_configuration.level is not None:
            return rule.default_configuration.level
    return None


def _fix_suggestion(fix: SarifFix, diagnostic_id: str, index: int) -> DiagnosticFixSuggestion:
    raw = canonical_ordered_json_bytes(
        fix.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    return DiagnosticFixSuggestion(
        suggestion_id=content_id(
            "diagnostic-fix:sarif",
            {"diagnostic": diagnostic_id, "index": index, "digest": sha256_digest(raw)},
        ),
        description=_render_message(fix.description, None) if fix.description is not None else None,
        digest=sha256_digest(raw),
        artifact_change_count=len(fix.artifact_changes),
        replacement_count=sum(len(item.replacements) for item in fix.artifact_changes),
    )


def _deduplicate_results(results: Sequence[SarifResult]) -> dict[str, list[SarifResult]]:
    grouped: defaultdict[str, list[SarifResult]] = defaultdict(list)
    for result in results:
        grouped[_result_identity(result)].append(result)
    return dict(grouped)


def _result_identity(result: SarifResult) -> str:
    return content_id(
        "sarif-result",
        result.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


def _binding_failure(
    log: SarifLog,
    run: SarifRun,
    *,
    binding: SarifArtifactBinding,
    expected_state: SourceStateRecord,
    expected_configuration_digest: str,
) -> tuple[str, str, str] | None:
    if binding.artifact_digest != sarif_digest(log):
        return (
            "sarif_binding_digest_mismatch",
            "The SARIF log no longer matches its acquisition binding.",
            "Rebind the exact immutable SARIF artifact before ingestion.",
        )
    if (
        binding.repository_id != expected_state.repository_id
        or binding.source_state != expected_state
    ):
        return (
            "stale_diagnostic_artifact",
            "The SARIF artifact is bound to a different repository source state.",
            "Rerun the analysis tool against the selected checkout.",
        )
    if binding.configuration_digest != expected_configuration_digest:
        return (
            "diagnostic_configuration_mismatch",
            "The SARIF artifact was acquired under a different configuration.",
            "Rerun the analysis tool with the selected configuration.",
        )
    revisions = {
        item.revision_id
        for item in run.version_control_provenance
        if item.revision_id is not None
    }
    if expected_state.revision is not None and revisions and expected_state.revision not in revisions:
        return (
            "stale_diagnostic_revision",
            "SARIF version-control provenance does not include the selected revision.",
            "Rerun the analysis tool at the selected revision.",
        )
    return None


def _seal(
    *,
    run: SarifRun,
    provider_id: str,
    provider_version: str,
    run_id: str,
    expected_state: SourceStateRecord,
    expected_configuration_digest: str,
    invocation: InvocationAuthority,
    builder: _Builder,
    repository_entity: RepositoryEntity,
    completeness_id: str,
    status: ProviderRunStatus,
) -> ProviderEnvelope:
    capabilities = ["diagnostics"]
    if builder.contracts:
        capabilities.append("declared_contracts")
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
    languages = sorted(
        {
            item.source_language
            for item in run.artifacts
            if item.source_language is not None
        }
    )
    return build_provider_envelope(
        provider_run_id=run_id,
        provider_id=provider_id,
        provider_version=provider_version,
        tool=ProviderToolIdentity(name=run.tool.driver.name, version=provider_version),
        capabilities=capabilities,
        languages=languages,
        repository_id=expected_state.repository_id,
        source_states=[expected_state],
        primary_source_state_id=expected_state.state_id,
        configuration_digest=expected_configuration_digest,
        scope=ProviderScope(
            scope_id=content_id("scope:sarif", {"run": run_id}),
            source_state_ids=[expected_state.state_id],
            paths=sorted(builder.paths),
            entity_ids=sorted(builder.entities),
            evidence_families=capabilities,
        ),
        invocation=invocation,
        status=status,
        payload=payload,
    )


def _degrade(
    builder: _Builder,
    *,
    run_id: str,
    state_id: str,
    code: str,
    summary: str,
    scope_type: str,
    scope_id: str,
    remediation: str,
) -> None:
    builder.add_degradation(
        run_id=run_id,
        state_id=state_id,
        code=code,
        summary=summary,
        scope_type=scope_type,
        scope_id=scope_id,
        remediation=remediation,
    )


def _artifact_hash_matches(artifact: SarifArtifact, source: str) -> bool:
    sha256 = next(
        (
            value
            for key, value in artifact.hashes.items()
            if key.casefold().replace("_", "-") in {"sha-256", "sha256"}
        ),
        None,
    )
    if sha256 is None:
        return True
    return sha256.casefold().removeprefix("sha256:") == hashlib.sha256(source.encode("utf-8")).hexdigest()


def _source_digest(source: str) -> str:
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


def _encoded_line_width(source: str, line_number: int, column_kind: str) -> int:
    lines = source.splitlines(keepends=True)
    if source.endswith(("\n", "\r")):
        lines.append("")
    if not lines:
        lines = [""]
    line = lines[line_number - 1]
    if line.endswith("\r\n"):
        line = line[:-2]
    elif line.endswith(("\r", "\n")):
        line = line[:-1]
    if column_kind == "utf16CodeUnits":
        return len(line.encode("utf-16-le")) // 2
    return len(line)


def _portable_tool_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return slug or content_id("tool", name)


def _is_local_absolute_uri(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme.casefold() == "file" or value.startswith("/") or bool(re.match(r"^[A-Za-z]:[/\\]", value))


def _validate_baseline(expected_state: SourceStateRecord, baseline: RepositoryEvidence) -> None:
    if baseline.repository_id != expected_state.repository_id:
        raise ValueError("diagnostic baseline belongs to another repository")
    if expected_state not in baseline.states:
        raise ValueError("diagnostic baseline does not contain the exact expected source state")
