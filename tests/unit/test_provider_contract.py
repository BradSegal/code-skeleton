from __future__ import annotations

import json

import pytest

from anatomize.evidence import (
    CompletenessRecord,
    CompletenessStatus,
    EdgeRecord,
    EvidenceStrength,
    FileCoordinateSpace,
    FileEntity,
    LocationOrigin,
    LocationRecord,
    ObservationStance,
    ProviderRunStatus,
    RelationshipCategory,
    RepositoryEntity,
    SourceStateRecord,
    StructuralObservation,
    SymbolEntity,
)
from anatomize.providers import (
    AuthorityLevel,
    InvocationAuthority,
    InvocationMode,
    ProviderArtifactLimits,
    ProviderConformanceExpectation,
    ProviderEnvelope,
    ProviderEnvelopeError,
    ProviderEvidenceBatch,
    ProviderScope,
    ProviderToolIdentity,
    build_provider_envelope,
    canonical_provider_bytes,
    parse_provider_envelope,
    provider_cache_key,
    run_provider_conformance,
)


def _minimal_envelope(*, invocation: InvocationAuthority | None = None) -> ProviderEnvelope:
    state = SourceStateRecord(
        state_id="state:minimal",
        repository_id="repository:minimal",
        revision="abc123",
        dirty=False,
        content_digest="source-digest",
        file_count=1,
    )
    locations = [
        LocationRecord(
            location_id="location:file",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file",
            path="src/core.py",
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:symbol",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file",
            path="src/core.py",
            coordinate_space=FileCoordinateSpace(),
        ),
    ]
    entities = [
        RepositoryEntity(
            entity_id="entity:repository",
            repository_id=state.repository_id,
            source_state_id=state.state_id,
            display_name="minimal",
            root_name="minimal",
        ),
        FileEntity(
            entity_id="entity:file",
            source_state_id=state.state_id,
            display_name="src/core.py",
            location_ids=["location:file"],
            path="src/core.py",
            language="python",
            digest="file-digest",
            size_bytes=20,
            roles=["source"],
        ),
        SymbolEntity(
            entity_id="entity:symbol",
            source_state_id=state.state_id,
            display_name="core.answer",
            location_ids=["location:symbol"],
            language="python",
            symbol_kind="function",
            name="answer",
            qualified_name="core.answer",
            public=True,
        ),
    ]
    edge = EdgeRecord(
        edge_id="edge:defines",
        source_state_id=state.state_id,
        source_entity_id="entity:file",
        target_entity_id="entity:symbol",
        category=RelationshipCategory.STRUCTURE,
        predicate="defines",
    )
    observation = StructuralObservation(
        observation_id="observation:defines",
        source_state_id=state.state_id,
        provider_run_id="run:minimal",
        method="minimal-parser",
        method_version="1",
        strength=EvidenceStrength.EXACT,
        stance=ObservationStance.SUPPORTS,
        location_ids=["location:symbol"],
        completeness_id="completeness:minimal",
        rationale="The provider parsed the declaration.",
        target_type="edge",
        target_id=edge.edge_id,
    )
    completeness = CompletenessRecord(
        completeness_id="completeness:minimal",
        source_state_id=state.state_id,
        provider_run_id="run:minimal",
        scope_type="repository",
        scope_id="entity:repository",
        evidence_families=["definitions"],
        status=CompletenessStatus.COMPLETE,
    )
    batch = ProviderEvidenceBatch(
        locations=locations,
        entities=entities,
        edges=[edge],
        observations=[observation],
        completeness=[completeness],
    )
    return build_provider_envelope(
        provider_run_id="run:minimal",
        provider_id="example.minimal",
        provider_version="1.0.0",
        tool=ProviderToolIdentity(name="minimal-parser", version="1.0.0"),
        capabilities=["definitions"],
        languages=["python"],
        repository_id=state.repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest="configuration-digest",
        scope=ProviderScope(
            scope_id="scope:minimal",
            source_state_ids=[state.state_id],
            paths=["src/core.py"],
            evidence_families=["definitions"],
        ),
        invocation=invocation
        or InvocationAuthority(
            mode=InvocationMode.ARTIFACT_IMPORT,
            level=AuthorityLevel.A1_ARTIFACT,
            policy_digest="artifact-import-policy",
        ),
        status=ProviderRunStatus.COMPLETE,
        payload=batch,
    )


def test_provider_envelope_records_complete_contract_and_cache_identity() -> None:
    envelope = _minimal_envelope()
    loaded = parse_provider_envelope(canonical_provider_bytes(envelope))

    assert loaded.provider_id == "example.minimal"
    assert loaded.tool.name == "minimal-parser"
    assert loaded.scope.evidence_families == ["definitions"]
    assert loaded.payload.observations[0].provider_run_id == loaded.provider_run_id
    assert loaded.artifact_digest.startswith("sha256:")
    assert provider_cache_key(loaded) == provider_cache_key(envelope)
    assert provider_cache_key(loaded) != provider_cache_key(
        loaded.model_copy(update={"configuration_digest": "changed"})
    )


def test_artifact_ingestion_is_data_only() -> None:
    loaded = parse_provider_envelope(canonical_provider_bytes(_minimal_envelope()))
    assert loaded.status is ProviderRunStatus.COMPLETE


def test_provider_artifact_digest_and_limits_fail_closed() -> None:
    raw = canonical_provider_bytes(_minimal_envelope())
    tampered = json.loads(raw)
    tampered["payload"]["entities"][0]["display_name"] = "tampered"
    with pytest.raises(ProviderEnvelopeError) as digest:
        parse_provider_envelope(json.dumps(tampered).encode())
    assert digest.value.code == "provider_artifact_digest_mismatch"

    with pytest.raises(ProviderEnvelopeError) as values:
        parse_provider_envelope(raw, limits=ProviderArtifactLimits(max_values=2))
    assert values.value.code == "provider_artifact_value_limit"


def test_reusable_conformance_harness_covers_hostile_and_failure_cases() -> None:
    raw = canonical_provider_bytes(_minimal_envelope())

    def fail_once() -> None:
        raise RuntimeError("controlled provider failure")

    report = run_provider_conformance(
        lambda: raw,
        expectation=ProviderConformanceExpectation(
            provider_id="example.minimal",
            repository_id="repository:minimal",
            source_state_ids=frozenset({"state:minimal"}),
        ),
        failure_probe=fail_once,
    )

    assert report.passed, {item.check_id: item.detail for item in report.checks if not item.passed}
    assert {item.check_id for item in report.checks} >= {
        "deterministic_bytes",
        "source_binding",
        "coordinate_containment",
        "conflict_references",
        "failure_isolation",
        "cache_identity",
        "corrupt_json",
        "artifact_digest",
        "value_limit",
    }
