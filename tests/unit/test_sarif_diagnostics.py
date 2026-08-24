from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize.diagnostics import (
    DeclaredDiagnosticContract,
    SarifArtifactError,
    SarifArtifactLimits,
    SarifLog,
    build_sarif_binding,
    canonical_sarif_bytes,
    normalize_sarif_log,
    parse_sarif_log,
    unavailable_diagnostic_envelope,
)
from anatomize.evidence import (
    ContractKind,
    DiagnosticEntity,
    DiagnosticObservation,
    EvidenceProducer,
    FileCoordinateSpace,
    FileEntity,
    LocationOrigin,
    LocationRecord,
    ProviderRunRecord,
    ProviderRunStatus,
    RepositoryEntity,
    RepositoryEvidence,
    SourcePosition,
    SourceRange,
    SourceStateRecord,
    SymbolEntity,
)
from anatomize.providers import ProviderEnvelope, canonical_provider_bytes

FIXTURE = Path(__file__).parents[1] / "fixtures" / "diagnostics" / "specialist-tools.sarif"
SOURCES = {
    "src/app.py": 'def unused():\n    password = "example"\n    return dangerous(password)\n',
    "R/analysis.R": "result <- mean(values)\n",
    "pyproject.toml": "[project]\ndependencies=[]\n",
    "src/architecture.py": "from forbidden import api\n",
}


def _log() -> SarifLog:
    return parse_sarif_log(FIXTURE.read_bytes())


def _state() -> SourceStateRecord:
    return SourceStateRecord(
        state_id="state:sarif-fixture",
        repository_id="repository:sarif-fixture",
        revision="abc123",
        dirty=False,
        content_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_count=len(SOURCES),
    )


def _baseline() -> RepositoryEvidence:
    state = _state()
    locations: list[LocationRecord] = []
    entities: list[RepositoryEntity | FileEntity | SymbolEntity] = [
        RepositoryEntity(
            entity_id="entity:repository:sarif",
            source_state_id=state.state_id,
            repository_id=state.repository_id,
            display_name="sarif-fixture",
            root_name="sarif-fixture",
        )
    ]
    for index, (path, source) in enumerate(SOURCES.items()):
        entity_id = f"entity:file:{index}"
        location_id = f"location:file:{index}"
        locations.append(
            LocationRecord(
                location_id=location_id,
                source_state_id=state.state_id,
                origin=LocationOrigin.REPOSITORY,
                file_id=entity_id,
                path=path,
                coordinate_space=FileCoordinateSpace(),
            )
        )
        entities.append(
            FileEntity(
                entity_id=entity_id,
                source_state_id=state.state_id,
                display_name=path,
                location_ids=[location_id],
                path=path,
                language={".py": "python", ".R": "r", ".toml": "toml"}.get(Path(path).suffix),
                size_bytes=len(source.encode()),
                roles=["source"],
            )
        )
    locations.append(
        LocationRecord(
            location_id="location:symbol:unused",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file:0",
            path="src/app.py",
            source_range=SourceRange(
                start=SourcePosition(line=1, column=4),
                end=SourcePosition(line=1, column=10),
            ),
            coordinate_space=FileCoordinateSpace(),
        )
    )
    entities.append(
        SymbolEntity(
            entity_id="entity:symbol:unused",
            source_state_id=state.state_id,
            display_name="app.unused",
            location_ids=["location:symbol:unused"],
            language="python",
            symbol_kind="function",
            name="unused",
            qualified_name="app.unused",
            public=True,
        )
    )
    return RepositoryEvidence(
        producer=EvidenceProducer(version="sarif-baseline"),
        repository_id=state.repository_id,
        states=[state],
        locations=locations,
        entities=entities,
    )


def _normalize(log: SarifLog | None = None) -> tuple[ProviderEnvelope, ...]:
    log = log or _log()
    return normalize_sarif_log(
        log,
        binding=build_sarif_binding(
            log,
            source_state=_state(),
            configuration_digest="configuration:sarif-fixture",
        ),
        expected_state=_state(),
        expected_configuration_digest="configuration:sarif-fixture",
        baseline=_baseline(),
        sources=SOURCES,
        declared_contracts={
            "Import Linter:ILC001": DeclaredDiagnosticContract(
                kind=ContractKind.API,
                summary="The architecture layer must not import the forbidden layer.",
                terms_digest="sha256:architecture-contract",
            )
        },
    )


def test_sarif_parser_preserves_standard_diagnostic_semantics_and_order() -> None:
    log = _log()
    ruff = log.runs[0]
    result = (ruff.results or [])[0]
    assert log.version == "2.1.0"
    assert ruff.tool.driver.name == "Ruff"
    assert ruff.invocations[0].execution_successful is True
    assert ruff.invocations[0].exit_code == 1
    assert result.level == "warning"
    assert result.baseline_state == "new"
    assert result.fixes[0].artifact_changes[0].replacements[0].inserted_content is not None
    canonical = canonical_sarif_bytes(log)
    reparsed = parse_sarif_log(canonical)
    assert [item.tool.driver.name for item in reparsed.runs] == [
        item.tool.driver.name for item in log.runs
    ]
    assert canonical_sarif_bytes(reparsed) == canonical

    bounded_cases = [
        (b"{broken", SarifArtifactLimits(), "sarif_artifact_corrupt"),
        (canonical, SarifArtifactLimits(max_bytes=len(canonical) - 1), "sarif_artifact_too_large"),
        (canonical, SarifArtifactLimits(max_depth=2), "sarif_artifact_depth_limit"),
        (canonical, SarifArtifactLimits(max_values=2), "sarif_artifact_value_limit"),
        (canonical, SarifArtifactLimits(max_string_bytes=4), "sarif_artifact_string_limit"),
    ]
    for raw, limits, code in bounded_cases:
        with pytest.raises(SarifArtifactError) as error:
            parse_sarif_log(raw, limits=limits)
        assert error.value.code == code


def test_specialist_runs_map_through_one_contract_without_core_tool_dependencies() -> None:
    envelopes = _normalize()
    by_tool = {item.tool.name: item for item in envelopes}
    assert set(by_tool) == {
        "Ruff",
        "lintr",
        "Semgrep",
        "CodeQL",
        "Gitleaks",
        "dependency-audit",
        "deadcode-scan",
        "Import Linter",
    }
    assert all("diagnostics" in item.capabilities for item in envelopes)
    assert all(item.invocation.mode.value == "artifact_import" for item in envelopes)
    assert any(
        isinstance(item, DiagnosticEntity) and item.rule_id == "object_usage_linter"
        for item in by_tool["lintr"].payload.entities
    )
    assert any(
        isinstance(item, DiagnosticEntity) and item.rule_id == "generic-secret"
        for item in by_tool["Gitleaks"].payload.entities
    )
    assert any(
        isinstance(item, DiagnosticEntity) and item.rule_id == "DEP001"
        for item in by_tool["dependency-audit"].payload.entities
    )
    assert any(
        isinstance(item, DiagnosticObservation)
        and item.subject_entity_ids == ["entity:symbol:unused"]
        for item in by_tool["deadcode-scan"].payload.observations
    )
    assert by_tool["Import Linter"].payload.contracts
    assert by_tool["Import Linter"].capabilities == ["declared_contracts", "diagnostics"]


def test_duplicate_fix_suppression_result_state_and_native_severity_remain_explicit() -> None:
    by_tool = {item.tool.name: item for item in _normalize()}
    ruff = next(item for item in by_tool["Ruff"].payload.entities if isinstance(item, DiagnosticEntity))
    semgrep = next(
        item for item in by_tool["Semgrep"].payload.entities if isinstance(item, DiagnosticEntity)
    )
    codeql = next(
        item for item in by_tool["CodeQL"].payload.entities if isinstance(item, DiagnosticEntity)
    )

    assert ruff.duplicate_count == 2
    assert ruff.baseline_state == "new"
    assert ruff.fix_suggestions[0].trust == "untrusted_suggestion"
    assert ruff.fix_suggestions[0].replacement_count == 1
    assert by_tool["Ruff"].status is ProviderRunStatus.PARTIAL
    assert "duplicate_diagnostic_collapsed" in {
        item.code for item in by_tool["Ruff"].payload.limitations
    }
    assert semgrep.suppressions[0].status == "accepted"
    assert semgrep.severity == "error"
    assert codeql.severity == "warning"
    assert semgrep.entity_id != codeql.entity_id


def test_stale_external_absent_partial_malformed_and_unavailable_states_are_non_silent() -> None:
    log = _log()
    binding = build_sarif_binding(
        log,
        source_state=_state(),
        configuration_digest="configuration:stale",
    )
    stale = normalize_sarif_log(
        log,
        binding=binding,
        expected_state=_state(),
        expected_configuration_digest="configuration:current",
        baseline=_baseline(),
        sources=SOURCES,
    )
    assert all(item.status is ProviderRunStatus.UNAVAILABLE for item in stale)
    assert all(not item.payload.observations for item in stale)
    assert all(
        "diagnostic_configuration_mismatch" in {entry.code for entry in item.payload.limitations}
        for item in stale
    )

    payload = log.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"] = {
        "uri": "file:///home/person/private/project.py"
    }
    payload["runs"][0]["results"] = [payload["runs"][0]["results"][0]]
    external_log = SarifLog.model_validate(payload)
    external = _normalize(external_log)[0]
    assert "external_artifact_location" in {item.code for item in external.payload.limitations}
    assert b"/home/person" not in canonical_provider_bytes(external)

    payload = log.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["runs"][1]["tool"]["driver"]["rules"] = []
    payload["runs"][1]["results"][0].pop("ruleId")
    absent_rule_log = SarifLog.model_validate(payload)
    absent_rule = _normalize(absent_rule_log)[1]
    assert "rule_identity_absent" in {item.code for item in absent_rule.payload.limitations}

    payload = log.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["runs"][0]["artifacts"][0]["hashes"] = {"sha-256": "0" * 64}
    mismatched_hash_log = SarifLog.model_validate(payload)
    mismatched_hash = _normalize(mismatched_hash_log)[0]
    assert "artifact_hash_mismatch" in {
        item.code for item in mismatched_hash.payload.limitations
    }

    payload = log.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["runs"][2]["invocations"] = [{"executionSuccessful": False, "exitCode": 2}]
    partial_log = SarifLog.model_validate(payload)
    partial = _normalize(partial_log)[2]
    assert partial.status is ProviderRunStatus.PARTIAL
    assert partial.payload.observations
    assert "tool_invocation_failed" in {item.code for item in partial.payload.limitations}

    incompatible = json.loads(FIXTURE.read_bytes())
    incompatible["version"] = "3.0.0"
    with pytest.raises(SarifArtifactError) as version:
        parse_sarif_log(json.dumps(incompatible).encode())
    assert version.value.code == "sarif_version_incompatible"

    unavailable = unavailable_diagnostic_envelope(
        tool_name="Semgrep",
        expected_state=_state(),
        expected_configuration_digest="configuration:sarif-fixture",
        baseline=_baseline(),
        reason="Semgrep is not installed.",
    )
    assert unavailable.status is ProviderRunStatus.FAILED
    assert {item.code for item in unavailable.payload.limitations} == {
        "results_unavailable",
        "tool_invocation_failed",
    }


def test_review_semantics_keep_contract_diagnostic_fact_candidate_and_decision_roles_distinct() -> None:
    envelope = {item.tool.name: item for item in _normalize()}["Import Linter"]
    run = ProviderRunRecord(
        provider_run_id=envelope.provider_run_id,
        provider_id=envelope.provider_id,
        provider_version=envelope.provider_version,
        source_state_id=envelope.primary_source_state_id,
        configuration_digest=envelope.configuration_digest,
        method=f"{envelope.tool.name}:{envelope.tool.version}",
        capabilities=envelope.capabilities,
        status=envelope.status,
        limitation_ids=[item.limitation_id for item in envelope.payload.limitations],
    )
    evidence = RepositoryEvidence(
        producer=EvidenceProducer(version="sarif-presentation-test"),
        repository_id=envelope.repository_id,
        states=envelope.source_states,
        provider_runs=[run],
        locations=envelope.payload.locations,
        entities=envelope.payload.entities,
        edges=envelope.payload.edges,
        contracts=envelope.payload.contracts,
        candidates=envelope.payload.candidates,
        observations=envelope.payload.observations,
        completeness=envelope.payload.completeness,
        limitations=envelope.payload.limitations,
        omissions=envelope.payload.omissions,
    )
    diagnostic = next(item for item in evidence.entities if isinstance(item, DiagnosticEntity))
    assert diagnostic.entity_id in {item.entity_id for item in evidence.entities if isinstance(item, DiagnosticEntity)}
    assert evidence.contracts[0].kind is ContractKind.API
    assert diagnostic.entity_id not in {item.edge_id for item in evidence.edges}
