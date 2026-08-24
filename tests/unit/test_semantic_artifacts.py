from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from anatomize.evidence import (
    CompletenessStatus,
    EvidenceProducer,
    ExternalEntity,
    FileCoordinateSpace,
    FileEntity,
    IdentityResolutionStatus,
    LocationOrigin,
    LocationRecord,
    ProviderRunStatus,
    RelationshipCategory,
    RepositoryEntity,
    RepositoryEvidence,
    SourcePosition,
    SourceRange,
    SymbolEntity,
)
from anatomize.identity import ProviderRange
from anatomize.providers import ProviderEnvelope
from anatomize.semantic import (
    LspPositionEncoding,
    LspSemanticArtifact,
    LspSemanticArtifactError,
    LspSemanticArtifactLimits,
    SemanticCapability,
    canonical_lsp_semantic_bytes,
    lsp_semantic_json_schema,
    normalize_lsp_semantic_artifact,
    parse_lsp_semantic_artifact,
    unavailable_lsp_semantic_envelope,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "semantic" / "lsp-semantic-capture.json"
CORE_SOURCE = (
    '"""😀"""\n'
    "def target():\n"
    "    return 1\n"
    "\n"
    "def caller():\n"
    "    return target() + ext()\n"
    "    return missing()\n"
)
API_SOURCE = "from .core import target as exported\n\ndef parse(value: int) -> int:\n    return value\n"


def _artifact() -> LspSemanticArtifact:
    return parse_lsp_semantic_artifact(FIXTURE.read_bytes())


def _baseline() -> RepositoryEvidence:
    artifact = _artifact()
    state = artifact.source_state
    file_locations = [
        LocationRecord(
            location_id="location:file:api",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file:api",
            path="src/api.py",
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:file:core",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file:core",
            path="src/core.py",
            coordinate_space=FileCoordinateSpace(),
        ),
    ]
    symbol_locations = [
        LocationRecord(
            location_id="location:symbol:caller",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file:core",
            path="src/core.py",
            source_range=SourceRange(
                start=SourcePosition(line=5, column=4),
                end=SourcePosition(line=5, column=10),
            ),
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:symbol:target",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file:core",
            path="src/core.py",
            source_range=SourceRange(
                start=SourcePosition(line=2, column=4),
                end=SourcePosition(line=2, column=10),
            ),
            coordinate_space=FileCoordinateSpace(),
        ),
    ]
    return RepositoryEvidence(
        producer=EvidenceProducer(version="semantic-baseline"),
        repository_id=state.repository_id,
        states=[state],
        locations=[*file_locations, *symbol_locations],
        entities=[
            RepositoryEntity(
                entity_id="entity:repository",
                source_state_id=state.state_id,
                repository_id=state.repository_id,
                display_name="semantic-fixture",
                root_name="semantic-fixture",
            ),
            FileEntity(
                entity_id="entity:file:api",
                source_state_id=state.state_id,
                display_name="src/api.py",
                location_ids=["location:file:api"],
                path="src/api.py",
                language="python",
                digest=artifact.documents[0].content_digest,
                size_bytes=len(API_SOURCE.encode()),
                roles=["source"],
            ),
            FileEntity(
                entity_id="entity:file:core",
                source_state_id=state.state_id,
                display_name="src/core.py",
                location_ids=["location:file:core"],
                path="src/core.py",
                language="python",
                digest=artifact.documents[1].content_digest,
                size_bytes=len(CORE_SOURCE.encode()),
                roles=["source"],
            ),
            SymbolEntity(
                entity_id="entity:symbol:caller",
                source_state_id=state.state_id,
                display_name="core.caller",
                location_ids=["location:symbol:caller"],
                language="python",
                symbol_kind="function",
                name="caller",
                qualified_name="core.caller",
                public=True,
            ),
            SymbolEntity(
                entity_id="entity:symbol:target",
                source_state_id=state.state_id,
                display_name="core.target",
                location_ids=["location:symbol:target"],
                language="python",
                symbol_kind="function",
                name="target",
                qualified_name="core.target",
                public=True,
            ),
        ],
    )


def _normalized(artifact: LspSemanticArtifact | None = None) -> ProviderEnvelope:
    artifact = artifact or _artifact()
    return normalize_lsp_semantic_artifact(
        artifact,
        expected_state=artifact.source_state,
        baseline=_baseline(),
        sources={"src/api.py": API_SOURCE, "src/core.py": CORE_SOURCE},
        expected_configuration_digest=artifact.configuration_digest,
    )


def test_semantic_fixture_is_exact_versioned_bounded_and_deterministic() -> None:
    artifact = _artifact()
    assert artifact.protocol_version == "3.18"
    assert artifact.position_encoding is LspPositionEncoding.UTF16
    assert set(artifact.capabilities) == {
        SemanticCapability.DEFINITIONS,
        SemanticCapability.DECLARATIONS,
        SemanticCapability.REFERENCES,
        SemanticCapability.CALLS,
    }
    canonical = canonical_lsp_semantic_bytes(artifact)
    assert canonical_lsp_semantic_bytes(parse_lsp_semantic_artifact(canonical)) == canonical
    assert lsp_semantic_json_schema()["additionalProperties"] is False

    with pytest.raises(LspSemanticArtifactError) as too_many:
        parse_lsp_semantic_artifact(
            canonical_lsp_semantic_bytes(artifact),
            limits=LspSemanticArtifactLimits(max_values=2),
        )
    assert too_many.value.code == "semantic_artifact_value_limit"

    bounded_cases = [
        (b"{not-json", LspSemanticArtifactLimits(), "semantic_artifact_corrupt"),
        (
            canonical,
            LspSemanticArtifactLimits(max_bytes=len(canonical) - 1),
            "semantic_artifact_too_large",
        ),
        (canonical, LspSemanticArtifactLimits(max_depth=2), "semantic_artifact_depth_limit"),
        (canonical, LspSemanticArtifactLimits(max_string_bytes=4), "semantic_artifact_string_limit"),
    ]
    for raw, limits, expected_code in bounded_cases:
        with pytest.raises(LspSemanticArtifactError) as bounded:
            parse_lsp_semantic_artifact(raw, limits=limits)
        assert bounded.value.code == expected_code

    incompatible = json.loads(FIXTURE.read_bytes())
    incompatible["schema_version"] = "2.0.0"
    with pytest.raises(LspSemanticArtifactError) as schema:
        parse_lsp_semantic_artifact(json.dumps(incompatible).encode())
    assert schema.value.code == "semantic_schema_incompatible"


def test_semantic_relations_reuse_baseline_and_preserve_new_external_and_unresolved_identities() -> None:
    envelope = _normalized()
    edge_shapes = {
        (item.source_entity_id, item.target_entity_id, item.category, item.predicate)
        for item in envelope.payload.edges
    }

    assert envelope.status is ProviderRunStatus.PARTIAL
    assert ("entity:symbol:caller", "entity:symbol:target", RelationshipCategory.CALL, "calls") in edge_shapes
    assert any(item.category is RelationshipCategory.REFERENCE for item in envelope.payload.edges)
    assert any(isinstance(item, ExternalEntity) for item in envelope.payload.entities)
    assert any(
        item.resolution is IdentityResolutionStatus.UNRESOLVED and item.value == "lsp:python:missing"
        for item in envelope.payload.aliases
    )
    assert {"entity:symbol:caller", "entity:symbol:target"}.issubset(
        {item.entity_id for item in envelope.payload.entities}
    )
    assert any(item.predicate == "reexports" for item in envelope.payload.edges)
    assert any(item.predicate == "declares_overload" for item in envelope.payload.edges)
    assert envelope.payload.completeness[0].status is CompletenessStatus.PARTIAL
    assert any(item.code == "unresolved_semantic_target" for item in envelope.payload.limitations)


def test_stale_version_skew_and_provider_absence_fail_closed_without_semantic_edges() -> None:
    artifact = _artifact()
    stale = artifact.model_copy(update={"indexed_content_digest": "sha256:stale"})
    stale_envelope = _normalized(stale)
    assert stale_envelope.status is ProviderRunStatus.UNAVAILABLE
    assert not stale_envelope.payload.edges
    assert {item.code for item in stale_envelope.payload.limitations} == {"stale_index"}

    skew = artifact.model_copy(update={"protocol_version": "4.0"})
    skew_envelope = _normalized(skew)
    assert skew_envelope.status is ProviderRunStatus.UNAVAILABLE
    assert {item.code for item in skew_envelope.payload.limitations} == {"version_skew"}

    configuration_mismatch = normalize_lsp_semantic_artifact(
        artifact,
        expected_state=artifact.source_state,
        baseline=_baseline(),
        sources={"src/api.py": API_SOURCE, "src/core.py": CORE_SOURCE},
        expected_configuration_digest="configuration:different",
    )
    assert configuration_mismatch.status is ProviderRunStatus.UNAVAILABLE
    assert {item.code for item in configuration_mismatch.payload.limitations} == {
        "configuration_mismatch"
    }

    absent = unavailable_lsp_semantic_envelope(
        expected_state=artifact.source_state,
        baseline=_baseline(),
        configuration_digest="configuration:none",
        requested_paths=["src/core.py"],
    )
    assert absent.status is ProviderRunStatus.UNAVAILABLE
    assert absent.scope.paths == ["src/core.py"]
    assert {item.code for item in absent.payload.limitations} == {"provider_unavailable"}


def test_document_digest_coordinate_and_compiler_degradation_are_explicit() -> None:
    artifact = _artifact()
    wrong_source = normalize_lsp_semantic_artifact(
        artifact,
        expected_state=artifact.source_state,
        baseline=_baseline(),
        sources={"src/api.py": API_SOURCE, "src/core.py": "changed"},
        expected_configuration_digest=artifact.configuration_digest,
    )
    assert "document_source_mismatch" in {item.code for item in wrong_source.payload.limitations}
    assert all(item.source_entity_id != "entity:symbol:caller" for item in wrong_source.payload.edges)

    bad_occurrence = artifact.occurrences[0].model_copy(
        update={
            "source_range": ProviderRange(
                start={"line": 0, "column": 999},
                end={"line": 0, "column": 1000},
            )
        }
    )
    bad_coordinate = artifact.model_copy(update={"occurrences": [bad_occurrence, *artifact.occurrences[1:]]})
    coordinate_envelope = _normalized(LspSemanticArtifact.model_validate(bad_coordinate.model_dump(mode="json")))
    assert "coordinate_column_out_of_bounds" in {
        item.code for item in coordinate_envelope.payload.limitations
    }

    partial_payload = artifact.model_dump(mode="json")
    partial_payload["status"] = "partial"
    partial_payload["issues"] = [
        {
            "issue_id": "compiler:1",
            "code": "compiler_error",
            "message": "Project compilation failed in src/api.py.",
            "path": "src/api.py",
            "recoverable": True,
            "remediation": "Repair the compiler error and regenerate the capture.",
        },
        {
            "issue_id": "project:incomplete",
            "code": "incomplete_project",
            "message": "The environment omitted an optional dependency.",
            "recoverable": True,
        },
    ]
    partial = _normalized(LspSemanticArtifact.model_validate(partial_payload))
    assert partial.status is ProviderRunStatus.PARTIAL
    assert {"compiler_error", "incomplete_project"}.issubset(
        {item.code for item in partial.payload.limitations}
    )


def test_generated_documents_remain_generated_and_never_claim_unavailable_projection() -> None:
    artifact = _artifact()
    payload = artifact.model_dump(mode="json")
    payload["status"] = "partial"
    payload["documents"].append(
        {
            "path": "generated/client.py",
            "language": "python",
            "content_digest": "sha256:9a01b16ee677430589a301a067d6d81577a24ab911a51c4f099769a745bff6f4",
            "generated": True,
            "generator_identity_id": "workflow:codegen",
            "projection": "unavailable",
        }
    )
    payload["symbols"].append(
        {
            "symbol_id": "lsp:python:generated.generated_call",
            "document_path": "generated/client.py",
            "source_range": {"start": {"line": 0, "column": 4}, "end": {"line": 0, "column": 18}},
            "language": "python",
            "symbol_kind": "function",
            "name": "generated_call",
            "qualified_name": "generated.generated_call",
            "role": "definition",
        }
    )
    payload["issues"] = [
        {
            "issue_id": "generated:projection",
            "code": "generated_path",
            "message": "Generated output has no source projection.",
            "path": "generated/client.py",
            "recoverable": True,
        }
    ]
    generated = LspSemanticArtifact.model_validate(payload)
    envelope = normalize_lsp_semantic_artifact(
        generated,
        expected_state=artifact.source_state,
        baseline=_baseline(),
        sources={
            "src/api.py": API_SOURCE,
            "src/core.py": CORE_SOURCE,
            "generated/client.py": "def generated_call():\n    return target()\n",
        },
        expected_configuration_digest=artifact.configuration_digest,
    )
    generated_locations = [
        item for item in envelope.payload.locations if item.path == "generated/client.py"
    ]
    assert generated_locations
    assert all(item.origin is LocationOrigin.GENERATED for item in generated_locations)
    assert any(item.code == "generated_path" for item in envelope.payload.limitations)


def test_semantic_model_rejects_missing_capabilities_and_checkout_specific_external_uris() -> None:
    payload = _artifact().model_dump(mode="json")
    payload["capabilities"] = ["definitions"]
    with pytest.raises(ValidationError, match="lack declared capabilities"):
        LspSemanticArtifact.model_validate(payload)

    payload = _artifact().model_dump(mode="json")
    payload["occurrences"][1]["external_target"]["external_identity"] = "file:///home/user/project/x.py"
    with pytest.raises(ValidationError, match="checkout-specific"):
        LspSemanticArtifact.model_validate(payload)
