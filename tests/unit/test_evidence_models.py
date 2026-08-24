from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from anatomize.evidence import (
    AliasRecord,
    ArtifactEntity,
    CandidateKind,
    CandidateRecord,
    CompletenessRecord,
    CompletenessStatus,
    ConfigurationEntity,
    ConflictRecord,
    ContentClass,
    ContractKind,
    ContractRecord,
    DataEntity,
    DependencyEntity,
    DiagnosticEntity,
    DiagnosticObservation,
    DocumentationEntity,
    EdgeRecord,
    EntityKind,
    EvidenceArtifactError,
    EvidenceProducer,
    EvidenceStrength,
    ExternalEntity,
    FileCoordinateSpace,
    FileEntity,
    GeneratedCoordinateSpace,
    IdentityCandidate,
    IdentityReason,
    IdentityResolutionStatus,
    LimitationRecord,
    LineageCertainty,
    LineageKind,
    LineageReason,
    LineageRecord,
    LocationOrigin,
    LocationRecord,
    ObservationStance,
    OmissionRecord,
    ProjectionCompleteness,
    ProviderRunRecord,
    ProviderRunStatus,
    RangeEntity,
    RelationshipCategory,
    RepositoryEntity,
    RepositoryEvidence,
    RuntimeEntity,
    RuntimeObservation,
    SimilarityEntity,
    SimilarityObservation,
    SourcePosition,
    SourceRange,
    SourceStateRecord,
    StructuralObservation,
    SymbolEntity,
    WorkflowEntity,
    canonical_evidence_bytes,
    evidence_json_schema,
    parse_evidence,
    write_evidence,
)
from anatomize.evidence import TestEntity as EvidenceTestEntity

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "evidence"


def _known_truth_evidence() -> RepositoryEvidence:
    states = [
        SourceStateRecord(
            state_id="state:before",
            repository_id="repository:fixture",
            revision="abc123",
            dirty=False,
            content_digest="digest-before",
            file_count=1,
        ),
        SourceStateRecord(
            state_id="state:after",
            repository_id="repository:fixture",
            revision=None,
            dirty=True,
            content_digest="digest-after",
            file_count=4,
        ),
    ]
    locations = [
        LocationRecord(
            location_id="location:file-before",
            source_state_id="state:before",
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file-before",
            path="src/pkg/core.py",
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:repository-after-file",
            source_state_id="state:after",
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file-after",
            path="src/pkg/core.py",
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:symbol-before",
            source_state_id="state:before",
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file-before",
            path="src/pkg/core.py",
            coordinate_space=FileCoordinateSpace(),
            source_range=SourceRange(
                start=SourcePosition(line=1, column=0),
                end=SourcePosition(line=2, column=12),
            ),
        ),
        LocationRecord(
            location_id="location:symbol-after",
            source_state_id="state:after",
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:file-after",
            path="src/pkg/core.py",
            coordinate_space=FileCoordinateSpace(),
            source_range=SourceRange(
                start=SourcePosition(line=1, column=0),
                end=SourcePosition(line=2, column=13),
            ),
        ),
        LocationRecord(
            location_id="location:generated",
            source_state_id="state:after",
            origin=LocationOrigin.GENERATED,
            file_id="entity:file-generated",
            path="build/generated.py",
            coordinate_space=GeneratedCoordinateSpace(
                generator_identity_id="generator:notebook",
                source_location_ids=["location:symbol-after"],
                projection=ProjectionCompleteness.PARTIAL,
            ),
            source_range=SourceRange(
                start=SourcePosition(line=1, column=0),
                end=SourcePosition(line=1, column=4),
            ),
        ),
        LocationRecord(
            location_id="location:external",
            source_state_id="state:after",
            origin=LocationOrigin.EXTERNAL,
            opaque_locator="pypi:requests@2",
        ),
    ]
    entities = [
        RepositoryEntity(
            entity_id="entity:repository-before",
            repository_id="repository:fixture",
            source_state_id="state:before",
            display_name="fixture",
            root_name="fixture",
        ),
        RepositoryEntity(
            entity_id="entity:repository-after",
            repository_id="repository:fixture",
            source_state_id="state:after",
            display_name="fixture",
            root_name="fixture",
        ),
        FileEntity(
            entity_id="entity:file-before",
            source_state_id="state:before",
            display_name="src/pkg/core.py",
            location_ids=["location:file-before"],
            path="src/pkg/core.py",
            language="python",
            digest="file-before",
            size_bytes=40,
            roles=["source"],
        ),
        FileEntity(
            entity_id="entity:file-after",
            source_state_id="state:after",
            display_name="src/pkg/core.py",
            location_ids=["location:repository-after-file"],
            path="src/pkg/core.py",
            language="python",
            digest="file-after",
            size_bytes=42,
            roles=["source"],
        ),
        FileEntity(
            entity_id="entity:file-generated",
            source_state_id="state:after",
            display_name="build/generated.py",
            location_ids=["location:generated"],
            path="build/generated.py",
            language="python",
            size_bytes=4,
            roles=["generated"],
            content_class=ContentClass.GENERATED,
        ),
        SymbolEntity(
            entity_id="entity:symbol-before",
            source_state_id="state:before",
            display_name="pkg.core.answer",
            location_ids=["location:symbol-before"],
            language="python",
            symbol_kind="function",
            name="answer",
            qualified_name="pkg.core.answer",
            public=True,
            digest="symbol-before",
        ),
        SymbolEntity(
            entity_id="entity:symbol-after",
            source_state_id="state:after",
            display_name="pkg.core.answer",
            location_ids=["location:symbol-after"],
            language="python",
            symbol_kind="function",
            name="answer",
            qualified_name="pkg.core.answer",
            public=True,
            digest="symbol-after",
        ),
        RangeEntity(
            entity_id="entity:range",
            source_state_id="state:after",
            display_name="answer body",
            location_ids=["location:symbol-after"],
            range_kind="function_body",
            parent_entity_id="entity:symbol-after",
        ),
        DocumentationEntity(
            entity_id="entity:documentation",
            source_state_id="state:after",
            display_name="Answer API",
            documentation_kind="markdown_section",
            heading="Answer API",
        ),
        ConfigurationEntity(
            entity_id="entity:configuration",
            source_state_id="state:after",
            display_name="tool.fixture",
            configuration_kind="toml_key",
            key="tool.fixture",
        ),
        EvidenceTestEntity(
            entity_id="entity:test",
            source_state_id="state:after",
            display_name="test_answer",
            test_kind="function",
            framework="pytest",
            test_name="test_answer",
        ),
        DataEntity(
            entity_id="entity:data",
            source_state_id="state:after",
            display_name="cohort",
            data_kind="table",
            media_type="text/csv",
            schema_identity="schema:cohort-v1",
        ),
        WorkflowEntity(
            entity_id="entity:workflow",
            source_state_id="state:after",
            display_name="analyse",
            workflow_kind="snakemake_rule",
            rule_name="analyse",
        ),
        ArtifactEntity(
            entity_id="entity:artifact",
            source_state_id="state:after",
            display_name="results.csv",
            artifact_kind="workflow_output",
            digest="results-digest",
            media_type="text/csv",
            generated=True,
        ),
        DependencyEntity(
            entity_id="entity:dependency",
            source_state_id="state:after",
            display_name="requests",
            location_ids=["location:external"],
            ecosystem="pypi",
            package="requests",
            version="2",
            scope="runtime",
        ),
        DiagnosticEntity(
            entity_id="entity:diagnostic",
            source_state_id="state:after",
            display_name="unused import",
            location_ids=["location:symbol-after"],
            rule_id="F401",
            severity="warning",
            message="Imported symbol is unused",
        ),
        RuntimeEntity(
            entity_id="entity:runtime",
            source_state_id="state:after",
            display_name="pytest run",
            runtime_kind="test_run",
            status="passed",
            run_identity="run-1",
        ),
        SimilarityEntity(
            entity_id="entity:similarity",
            source_state_id="state:after",
            display_name="similar functions",
            method="ast_digest",
            normalization="definition_name",
            member_entity_ids=["entity:symbol-after", "entity:test"],
        ),
        ExternalEntity(
            entity_id="entity:external",
            source_state_id="state:after",
            display_name="external symbol",
            location_ids=["location:external"],
            identity_scheme="scip",
            external_identity="scip-python pypi requests 2 `requests/get`.",
            entity_kind="symbol",
        ),
    ]
    edges = [
        EdgeRecord(
            edge_id="edge:defines",
            source_state_id="state:after",
            source_entity_id="entity:file-after",
            target_entity_id="entity:symbol-after",
            category=RelationshipCategory.STRUCTURE,
            predicate="defines",
        )
    ]
    limitations = [
        LimitationRecord(
            limitation_id="limitation:semantic",
            provider_run_id="provider:semantic",
            code="dynamic_dispatch",
            summary="Dynamic dispatch is outside this provider scope.",
        )
    ]
    omissions = [
        OmissionRecord(
            omission_id="omission:dynamic",
            source_state_id="state:after",
            provider_run_id="provider:semantic",
            reason="unsupported dynamic dispatch",
            scope_type="entity",
            scope_id="entity:symbol-after",
            recoverable=True,
            remediation="Attach runtime evidence.",
        )
    ]
    provider_runs = [
        ProviderRunRecord(
            provider_run_id="provider:ast",
            provider_id="python_ast",
            provider_version="1",
            source_state_id="state:after",
            configuration_digest="config-ast",
            method="ast",
            capabilities=["definitions", "relationships"],
            status=ProviderRunStatus.COMPLETE,
        ),
        ProviderRunRecord(
            provider_run_id="provider:semantic",
            provider_id="semantic",
            provider_version="2",
            source_state_id="state:after",
            configuration_digest="config-semantic",
            method="compiler_index",
            capabilities=["relationships", "diagnostics", "runtime", "similarity"],
            status=ProviderRunStatus.PARTIAL,
            limitation_ids=["limitation:semantic"],
        ),
    ]
    completeness = [
        CompletenessRecord(
            completeness_id="completeness:ast",
            source_state_id="state:after",
            provider_run_id="provider:ast",
            scope_type="repository",
            scope_id="entity:repository-after",
            evidence_families=["definitions", "relationships"],
            status=CompletenessStatus.COMPLETE,
        ),
        CompletenessRecord(
            completeness_id="completeness:semantic",
            source_state_id="state:after",
            provider_run_id="provider:semantic",
            scope_type="entity",
            scope_id="entity:symbol-after",
            evidence_families=["relationships"],
            status=CompletenessStatus.PARTIAL,
            omission_ids=["omission:dynamic"],
        ),
    ]
    observations = [
        StructuralObservation(
            observation_id="observation:ast-defines",
            source_state_id="state:after",
            provider_run_id="provider:ast",
            method="ast",
            method_version="1",
            strength=EvidenceStrength.EXACT,
            stance=ObservationStance.SUPPORTS,
            location_ids=["location:symbol-after"],
            completeness_id="completeness:ast",
            rationale="The definition is a child of the module AST.",
            target_type="edge",
            target_id="edge:defines",
        ),
        StructuralObservation(
            observation_id="observation:semantic-conflict",
            source_state_id="state:after",
            provider_run_id="provider:semantic",
            method="compiler_index",
            method_version="2",
            strength=EvidenceStrength.CONSERVATIVE,
            stance=ObservationStance.CONFLICTS,
            location_ids=["location:symbol-after"],
            completeness_id="completeness:semantic",
            limitation_ids=["limitation:semantic"],
            rationale="Generated-source mapping points to a different owner.",
            target_type="edge",
            target_id="edge:defines",
        ),
        DiagnosticObservation(
            observation_id="observation:diagnostic",
            source_state_id="state:after",
            provider_run_id="provider:semantic",
            method="sarif",
            method_version="2.1",
            strength=EvidenceStrength.EXACT,
            stance=ObservationStance.SUPPORTS,
            rationale="SARIF result normalized without changing the provider severity.",
            diagnostic_entity_id="entity:diagnostic",
            subject_entity_ids=["entity:symbol-after"],
        ),
        RuntimeObservation(
            observation_id="observation:runtime",
            source_state_id="state:after",
            provider_run_id="provider:semantic",
            method="junit",
            method_version="1",
            strength=EvidenceStrength.EXACT,
            stance=ObservationStance.QUALIFIES,
            rationale="One declared test run passed.",
            runtime_entity_id="entity:runtime",
            subject_entity_ids=["entity:test", "entity:symbol-after"],
            outcome="passed",
            metrics={"duration_seconds": 0.01},
        ),
        SimilarityObservation(
            observation_id="observation:similarity",
            source_state_id="state:after",
            provider_run_id="provider:semantic",
            method="ast_digest",
            method_version="1",
            strength=EvidenceStrength.CONSERVATIVE,
            stance=ObservationStance.SUPPORTS,
            rationale="The members share a declared normalization.",
            similarity_entity_id="entity:similarity",
            member_entity_ids=["entity:symbol-after", "entity:test"],
            score=1.0,
        ),
    ]
    candidates = [
        CandidateRecord(
            candidate_id="candidate:duplicate",
            source_state_id="state:after",
            kind=CandidateKind.DUPLICATION,
            member_entity_ids=["entity:symbol-after", "entity:test"],
            method="ast_digest",
            strength=EvidenceStrength.CONSERVATIVE,
            observation_ids=["observation:similarity"],
            rationale="Candidate only; behavior and intent may differ.",
        )
    ]
    return RepositoryEvidence(
        producer=EvidenceProducer(version="test"),
        repository_id="repository:fixture",
        states=states,
        provider_runs=provider_runs,
        locations=locations,
        entities=entities,
        edges=edges,
        contracts=[
            ContractRecord(
                contract_id="contract:answer",
                source_state_id="state:after",
                kind=ContractKind.API,
                subject_entity_ids=["entity:symbol-after"],
                declaration_location_ids=["location:symbol-after"],
                summary="Return the documented answer.",
                terms_digest="contract-digest",
            )
        ],
        candidates=candidates,
        observations=observations,
        completeness=completeness,
        limitations=limitations,
        omissions=omissions,
        conflicts=[
            ConflictRecord(
                conflict_id="conflict:defines",
                source_state_id="state:after",
                target_type="edge",
                target_id="edge:defines",
                observation_ids=["observation:ast-defines", "observation:semantic-conflict"],
                summary="AST and generated semantic ownership disagree.",
            )
        ],
        aliases=[
            AliasRecord(
                alias_id="alias:answer",
                source_state_id="state:after",
                scheme="python.qualified_name",
                value="pkg.core.answer",
                resolution=IdentityResolutionStatus.EXACT,
                provider_run_ids=["provider:ast", "provider:semantic"],
                reason_codes=[IdentityReason.DECLARED_ALIAS, IdentityReason.REEXPORT],
                candidates=[
                    IdentityCandidate(
                        entity_id="entity:symbol-after",
                        provider_run_ids=["provider:ast", "provider:semantic"],
                        reason_codes=[IdentityReason.DECLARED_ALIAS, IdentityReason.REEXPORT],
                        exact=True,
                    )
                ],
                rationale="The AST provider observed the exact qualified name.",
            )
        ],
        lineage=[
            LineageRecord(
                lineage_id="lineage:answer",
                predecessor_state_id="state:before",
                successor_state_id="state:after",
                predecessor_entity_ids=["entity:symbol-before"],
                successor_entity_ids=["entity:symbol-after"],
                kind=LineageKind.MODIFIED,
                certainty=LineageCertainty.CANDIDATE,
                method="qualified-name-and-body",
                strength=EvidenceStrength.DERIVED,
                reason_codes=[LineageReason.QUALIFIED_NAME_MATCH, LineageReason.CONTENT_CHANGED],
                rationale="The qualified name persists while the body digest changes.",
                observation_ids=["observation:ast-defines"],
            )
        ],
    )


def test_schema_represents_all_selected_entity_families() -> None:
    evidence = _known_truth_evidence()

    assert {entity.entity_type for entity in evidence.entities} == set(EntityKind)
    schema = evidence_json_schema()
    assert schema["title"] == "RepositoryEvidence"
    assert "ConsumerAnnotation" not in schema.get("$defs", {})


def test_multiple_provider_observations_preserve_conflict_and_provenance() -> None:
    evidence = _known_truth_evidence()
    by_id = {item.observation_id: item for item in evidence.observations}
    conflict = evidence.conflicts[0]

    assert conflict.observation_ids == ["observation:ast-defines", "observation:semantic-conflict"]
    assert by_id["observation:ast-defines"].provider_run_id == "provider:ast"
    semantic = by_id["observation:semantic-conflict"]
    assert semantic.provider_run_id == "provider:semantic"
    assert semantic.stance is ObservationStance.CONFLICTS
    assert semantic.completeness_id == "completeness:semantic"
    assert semantic.limitation_ids == ["limitation:semantic"]


def test_fact_candidate_contract_runtime_diagnostic_and_annotation_types_cannot_collapse() -> None:
    evidence = _known_truth_evidence()
    record_types = {
        evidence.edges[0].record_type,
        evidence.candidates[0].record_type,
        evidence.contracts[0].record_type,
        next(item.record_type for item in evidence.observations if isinstance(item, RuntimeObservation)),
        next(item.record_type for item in evidence.observations if isinstance(item, DiagnosticObservation)),
    }
    assert len(record_types) == 5

    payload = evidence.model_dump(mode="json")
    payload["annotations"] = [{"target_id": "candidate:duplicate", "disposition": "keep_separate"}]
    with pytest.raises(ValidationError, match="annotations"):
        RepositoryEvidence.model_validate(payload)


def test_reference_and_portable_path_invariants_fail_closed() -> None:
    payload = _known_truth_evidence().model_dump(mode="json")
    payload["edges"][0]["target_entity_id"] = "entity:missing"
    with pytest.raises(ValidationError, match="unknown identifier"):
        RepositoryEvidence.model_validate(payload)

    with pytest.raises(ValidationError, match="normalized and relative"):
        FileEntity(
            entity_id="entity:escape",
            source_state_id="state:after",
            display_name="escape",
            path="../outside.py",
            size_bytes=1,
        )

    cross_state = _known_truth_evidence().model_dump(mode="json")
    cross_state["aliases"][0]["candidates"][0]["entity_id"] = "entity:symbol-before"
    with pytest.raises(ValidationError, match="crosses source states"):
        RepositoryEvidence.model_validate(cross_state)

    mismatched_file = _known_truth_evidence().model_dump(mode="json")
    mismatched_file["locations"][3]["file_id"] = "entity:file-before"
    with pytest.raises(ValidationError, match="source-bound file"):
        RepositoryEvidence.model_validate(mismatched_file)


def test_canonical_serialization_is_order_independent_and_atomic(tmp_path: Path) -> None:
    evidence = _known_truth_evidence()
    payload = evidence.model_dump(mode="json")
    for key, value in payload.items():
        if isinstance(value, list):
            value.reverse()
    reversed_evidence = RepositoryEvidence.model_validate(payload)

    assert canonical_evidence_bytes(evidence) == canonical_evidence_bytes(reversed_evidence)
    output = tmp_path / "evidence.json"
    write_evidence(reversed_evidence, output)
    assert output.read_bytes() == canonical_evidence_bytes(evidence)


def test_current_artifact_loads() -> None:
    payload = _known_truth_evidence().model_dump(mode="json")
    current = parse_evidence(json.dumps(payload).encode())
    assert current.schema_version == "1.0.0"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"schema_version": "1.1.0"}, "artifact_schema_incompatible"),
        ({"schema_version": "2.0.0"}, "artifact_schema_incompatible"),
        ({"artifact_type": "anatomize.provider"}, "artifact_type_incompatible"),
        ({"repository_id": None}, "artifact_invalid"),
    ],
)
def test_incompatible_future_and_incomplete_artifacts_are_actionable(
    mutation: dict[str, object],
    code: str,
) -> None:
    payload = _known_truth_evidence().model_dump(mode="json")
    payload.update(mutation)
    with pytest.raises(EvidenceArtifactError) as caught:
        parse_evidence(json.dumps(payload).encode())

    assert caught.value.code == code
    assert caught.value.remediation


def test_corrupt_and_oversize_artifacts_are_rejected_before_model_use() -> None:
    with pytest.raises(EvidenceArtifactError) as corrupt:
        parse_evidence(b"{not json")
    assert corrupt.value.code == "artifact_corrupt"

    with pytest.raises(EvidenceArtifactError) as oversized:
        parse_evidence(b"{}", max_bytes=1)
    assert oversized.value.code == "artifact_too_large"


def test_legacy_repository_index_is_rejected_with_regeneration_guidance() -> None:
    legacy = (Path(__file__).parents[1] / "fixtures" / "indexes" / "current-v2.json").read_bytes()
    with pytest.raises(EvidenceArtifactError) as caught:
        parse_evidence(legacy)

    assert caught.value.code == "artifact_type_incompatible"
    assert "Regenerate" in caught.value.remediation


def test_unknown_top_level_fields_are_not_mistaken_for_evidence() -> None:
    payload = deepcopy(_known_truth_evidence().model_dump(mode="json"))
    payload["consumer_annotations"] = []
    with pytest.raises(EvidenceArtifactError) as caught:
        parse_evidence(json.dumps(payload).encode())
    assert caught.value.code == "artifact_invalid"


def test_golden_current_fixture_loads() -> None:
    current = parse_evidence((FIXTURE_ROOT / "current-v1-minimal.json").read_bytes())
    assert current.schema_version == "1.0.0"


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("additive-v1.1-minimal.json", "artifact_schema_incompatible"),
        ("incompatible-v2.json", "artifact_schema_incompatible"),
        ("incomplete-v1.json", "artifact_invalid"),
        ("corrupt-v1.json", "artifact_corrupt"),
    ],
)
def test_golden_invalid_fixtures_fail_with_stable_codes(name: str, code: str) -> None:
    with pytest.raises(EvidenceArtifactError) as caught:
        parse_evidence((FIXTURE_ROOT / name).read_bytes())
    assert caught.value.code == code
