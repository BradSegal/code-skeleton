from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from anatomize.evidence import (
    ArtifactEntity,
    CandidateKind,
    CandidateRecord,
    ConfigurationEntity,
    ContractKind,
    ContractRecord,
    DataEntity,
    DependencyEntity,
    DiagnosticEntity,
    DocumentationEntity,
    EdgeRecord,
    EvidenceProducer,
    EvidenceStrength,
    FileEntity,
    LineageCertainty,
    LineageKind,
    LineageReason,
    LineageRecord,
    ProviderArtifactRecord,
    ProviderRunRecord,
    ProviderRunStatus,
    RelationshipCategory,
    RepositoryEntity,
    RepositoryEvidence,
    SimilarityEntity,
    SourceStateRecord,
    WorkflowEntity,
)
from anatomize.evidence import TestEntity as EvidenceTestEntity
from anatomize.temporal import (
    ComparisonArtifactError,
    ComparisonStatus,
    DeltaAvailability,
    DeltaFamily,
    DeltaReason,
    DeltaStatus,
    EvidenceRecordType,
    EvidenceTargetReference,
    HistoryStatus,
    ProviderStateBinding,
    SemanticDelta,
    StateManifest,
    build_repository_comparison,
    build_semantic_delta,
    build_state_manifest,
    canonical_comparison_bytes,
    compare_repository_evidence,
    parse_comparison,
    validate_comparison_evidence,
    write_comparison,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "temporal" / "two-state-corpus.json"


def _corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def _entity_id(label: str, side: str, ordinal: int = 0) -> str:
    return f"entity:{label}:{side}:{ordinal}"


def _evidence(side: str) -> RepositoryEvidence:
    corpus = _corpus()
    state_data = corpus["states"][side]
    state = SourceStateRecord(repository_id=corpus["repository_id"], **state_data)
    provider = corpus["provider"]
    provider_run_id = f"run:{side}"
    artifact_id = f"provider-artifact:{side}"
    entities: list[Any] = [
        RepositoryEntity(
            entity_id=_entity_id("repository", side),
            source_state_id=state.state_id,
            repository_id=state.repository_id,
            display_name="temporal",
            root_name="temporal",
        ),
        FileEntity(
            entity_id=_entity_id("subject", side),
            source_state_id=state.state_id,
            display_name="src/subject.py",
            path="src/subject.py",
            digest=f"sha256:subject-{side}",
            size_bytes=10,
        ),
        EvidenceTestEntity(
            entity_id=_entity_id("test", side),
            source_state_id=state.state_id,
            display_name="test_subject",
            test_kind="unit",
            framework="pytest",
            test_name="test_subject",
        ),
        DocumentationEntity(
            entity_id=_entity_id("documentation", side),
            source_state_id=state.state_id,
            display_name="Subject guide",
            documentation_kind="guide",
            heading="Subject",
            digest=f"sha256:documentation-{side}",
        ),
        WorkflowEntity(
            entity_id=_entity_id("workflow", side),
            source_state_id=state.state_id,
            display_name="test workflow",
            workflow_kind="ci",
            rule_name="test",
        ),
        DataEntity(
            entity_id=_entity_id("data", side),
            source_state_id=state.state_id,
            display_name="analysis data",
            data_kind="table",
            schema_identity=f"sha256:data-schema-{side}",
        ),
        ConfigurationEntity(
            entity_id=_entity_id("configuration", side),
            source_state_id=state.state_id,
            display_name="environment",
            configuration_kind="environment",
            key="python",
        ),
        DependencyEntity(
            entity_id=_entity_id("dependency", side),
            source_state_id=state.state_id,
            display_name="pydantic",
            ecosystem="pypi",
            package="pydantic",
            version="2.0" if side == "before" else "2.1",
        ),
        DiagnosticEntity(
            entity_id=_entity_id("diagnostic", side),
            source_state_id=state.state_id,
            display_name="diagnostic",
            rule_id="rule.example",
            severity="warning",
            message=f"diagnostic {side}",
        ),
        ArtifactEntity(
            entity_id=_entity_id("artifact", side),
            source_state_id=state.state_id,
            display_name="wheel",
            artifact_kind="wheel",
            digest=f"sha256:wheel-{side}",
            media_type="application/zip",
        ),
    ]
    for case in corpus["lineage_cases"]:
        count = case["before_count"] if side == "before" else case["after_count"]
        for ordinal in range(count):
            label = f"lineage-{case['kind']}"
            entities.append(
                FileEntity(
                    entity_id=_entity_id(label, side, ordinal),
                    source_state_id=state.state_id,
                    display_name=f"{label}-{ordinal}.py",
                    path=f"src/{label}-{ordinal}.py",
                    digest=f"sha256:{case['kind']}-{ordinal}",
                    size_bytes=10,
                )
            )
    entities.append(
        SimilarityEntity(
            entity_id=_entity_id("similarity", side),
            source_state_id=state.state_id,
            display_name="duplicate candidate",
            method="normalized-tree",
            normalization="identifiers",
            member_entity_ids=[_entity_id("subject", side), _entity_id("test", side)],
        )
    )
    edge = EdgeRecord(
        edge_id=f"edge:relationship:{side}",
        source_state_id=state.state_id,
        source_entity_id=_entity_id("subject", side),
        target_entity_id=_entity_id("test", side),
        category=RelationshipCategory.TEST,
        predicate="tested_by",
    )
    contract = ContractRecord(
        contract_id=f"contract:api:{side}",
        source_state_id=state.state_id,
        kind=ContractKind.API,
        subject_entity_ids=[_entity_id("subject", side)],
        summary=f"API contract {side}",
        terms_digest=f"sha256:contract-{side}",
    )
    candidate = CandidateRecord(
        candidate_id=f"candidate:duplicate:{side}",
        source_state_id=state.state_id,
        kind=CandidateKind.DUPLICATION,
        member_entity_ids=[_entity_id("subject", side), _entity_id("test", side)],
        method="normalized-tree",
        strength=EvidenceStrength.CONSERVATIVE,
        rationale="The normalized structures are similar; intent is not inferred.",
    )
    return RepositoryEvidence(
        producer=EvidenceProducer(version="temporal-test"),
        repository_id=state.repository_id,
        states=[state],
        provider_artifacts=[
            ProviderArtifactRecord(
                artifact_id=artifact_id,
                digest=f"sha256:provider-{side}",
                media_type="application/json",
                schema_version="1.0.0",
                byte_size=10,
                identity_verified=True,
            )
        ],
        provider_runs=[
            ProviderRunRecord(
                provider_run_id=provider_run_id,
                provider_id=provider["provider_id"],
                provider_version=provider["provider_version"],
                source_state_id=state.state_id,
                configuration_digest=provider["configuration_digest"],
                method="known-truth",
                capabilities=provider["capabilities"],
                artifact_ids=[artifact_id],
                status=ProviderRunStatus.COMPLETE,
            )
        ],
        entities=entities,
        edges=[edge],
        contracts=[contract],
        candidates=[candidate],
    )


def _manifest(evidence: RepositoryEvidence, *, history: HistoryStatus = HistoryStatus.COMPLETE) -> StateManifest:
    corpus = _corpus()
    return build_state_manifest(
        evidence,
        source_state_id=evidence.states[0].state_id,
        configuration_digest=corpus["configuration_digest"],
        history_status=history,
        baseline_available=True,
    )


def _lineage() -> list[LineageRecord]:
    corpus = _corpus()
    records = []
    for case in corpus["lineage_cases"]:
        kind = LineageKind(case["kind"])
        certainty = LineageCertainty(case["certainty"])
        before_ids = [_entity_id(f"lineage-{kind.value}", "before", item) for item in range(case["before_count"])]
        after_ids = [_entity_id(f"lineage-{kind.value}", "after", item) for item in range(case["after_count"])]
        reason = {
            LineageKind.UNCHANGED: LineageReason.CONTENT_IDENTITY,
            LineageKind.MODIFIED: LineageReason.CONTENT_CHANGED,
            LineageKind.MOVED: LineageReason.PATH_CHANGED,
            LineageKind.RENAMED: LineageReason.NAME_CHANGED,
            LineageKind.SPLIT: LineageReason.SPLIT_CANDIDATE,
            LineageKind.MERGED: LineageReason.MERGE_CANDIDATE,
            LineageKind.ADDED: LineageReason.ADDED_TO_SCOPE,
            LineageKind.DELETED: LineageReason.REMOVED_FROM_SCOPE,
        }[kind]
        records.append(
            LineageRecord(
                lineage_id=f"lineage:{kind.value}",
                predecessor_state_id="state:before",
                successor_state_id="state:after",
                predecessor_entity_ids=before_ids,
                successor_entity_ids=after_ids,
                kind=kind,
                certainty=certainty,
                method="known-truth-fixture",
                strength=(EvidenceStrength.EXACT if certainty is LineageCertainty.EXACT else EvidenceStrength.DERIVED),
                reason_codes=[reason],
                rationale=f"Known-truth {kind.value} lineage case.",
            )
        )
    return records


def _ref(side: str, record_type: EvidenceRecordType, record_id: str) -> EvidenceTargetReference:
    return EvidenceTargetReference(source_state_id=f"state:{side}", record_type=record_type, record_id=record_id)


def _deltas() -> list[SemanticDelta]:
    targets = {
        DeltaFamily.RELATIONSHIP: (EvidenceRecordType.EDGE, "edge:relationship", DeltaReason.RELATIONSHIP_CHANGED),
        DeltaFamily.API: (EvidenceRecordType.CONTRACT, "contract:api", DeltaReason.CONTRACT_CHANGED),
        DeltaFamily.DUPLICATE: (
            EvidenceRecordType.CANDIDATE,
            "candidate:duplicate",
            DeltaReason.DUPLICATE_MEMBERSHIP_CHANGED,
        ),
        DeltaFamily.TEST: (EvidenceRecordType.ENTITY, "entity:test", DeltaReason.TEST_INTENT_CHANGED),
        DeltaFamily.DOCUMENTATION: (
            EvidenceRecordType.ENTITY,
            "entity:documentation",
            DeltaReason.DOCUMENTATION_CHANGED,
        ),
        DeltaFamily.WORKFLOW: (EvidenceRecordType.ENTITY, "entity:workflow", DeltaReason.WORKFLOW_CHANGED),
        DeltaFamily.DATA: (EvidenceRecordType.ENTITY, "entity:data", DeltaReason.DATA_CHANGED),
        DeltaFamily.ENVIRONMENT: (EvidenceRecordType.ENTITY, "entity:configuration", DeltaReason.ENVIRONMENT_CHANGED),
        DeltaFamily.ARTIFACT: (EvidenceRecordType.ENTITY, "entity:artifact", DeltaReason.ARTIFACT_CHANGED),
    }
    deltas: list[SemanticDelta] = []
    for family, (record_type, stem, reason) in targets.items():
        before_id = f"{stem}:before:0" if record_type is EvidenceRecordType.ENTITY else f"{stem}:before"
        after_id = f"{stem}:after:0" if record_type is EvidenceRecordType.ENTITY else f"{stem}:after"
        deltas.append(
            build_semantic_delta(
                family=family,
                status=DeltaStatus.CHANGED,
                predecessor_refs=[_ref("before", record_type, before_id)],
                successor_refs=[_ref("after", record_type, after_id)],
                lineage_ids=[],
                provider_run_ids=[],
                required_provider_ids=[],
                availability=DeltaAvailability.COMPLETE,
                strength=EvidenceStrength.DERIVED,
                reason_codes=[reason],
                rationale=f"Known-truth {family.value} delta.",
            )
        )
    deltas.append(
        build_semantic_delta(
            family=DeltaFamily.DIAGNOSTIC,
            status=DeltaStatus.UNKNOWN,
            predecessor_refs=[],
            successor_refs=[],
            lineage_ids=[],
            provider_run_ids=[],
            required_provider_ids=["provider.optional-diagnostics"],
            availability=DeltaAvailability.UNAVAILABLE,
            strength=EvidenceStrength.UNKNOWN,
            reason_codes=[DeltaReason.PROVIDER_UNAVAILABLE],
            rationale="The optional diagnostic provider is absent on both states.",
        )
    )
    return deltas


def test_state_manifests_bind_clean_rebuilds_to_exact_provider_and_artifact_state() -> None:
    before = _evidence("before")
    after = _evidence("after")
    before_manifest = _manifest(before)
    after_manifest = _manifest(after)

    assert before_manifest.source_state.revision == "1111111"
    assert after_manifest.source_state.revision == "2222222"
    assert before_manifest.evidence_artifact_digest != after_manifest.evidence_artifact_digest
    assert before_manifest.provider_states[0].artifacts[0].digest == "sha256:provider-before"
    assert _manifest(RepositoryEvidence.model_validate(before.model_dump(mode="json"))) == before_manifest
    assert _manifest(RepositoryEvidence.model_validate(after.model_dump(mode="json"))) == after_manifest


def test_lineage_known_truth_covers_every_shape_and_rejects_false_exactness() -> None:
    records = _lineage()
    corpus = _corpus()

    assert [(item.kind.value, item.certainty.value) for item in records] == [
        (item["kind"], item["certainty"]) for item in corpus["lineage_cases"]
    ]
    with pytest.raises(ValidationError, match="endpoint cardinality"):
        LineageRecord(
            lineage_id="lineage:invalid-split",
            predecessor_state_id="state:before",
            successor_state_id="state:after",
            predecessor_entity_ids=["entity:a"],
            successor_entity_ids=["entity:b"],
            kind=LineageKind.SPLIT,
            certainty=LineageCertainty.CANDIDATE,
            method="invalid",
            strength=EvidenceStrength.HEURISTIC,
            reason_codes=[LineageReason.SPLIT_CANDIDATE],
            rationale="A split needs at least two successors.",
        )
    with pytest.raises(ValidationError, match="exact lineage requires exact"):
        LineageRecord(
            lineage_id="lineage:false-exact",
            predecessor_state_id="state:before",
            successor_state_id="state:after",
            predecessor_entity_ids=["entity:a"],
            successor_entity_ids=["entity:b"],
            kind=LineageKind.RENAMED,
            certainty=LineageCertainty.EXACT,
            method="name-similarity",
            strength=EvidenceStrength.HEURISTIC,
            reason_codes=[LineageReason.NAME_CHANGED],
            rationale="Similarity cannot establish exact lineage.",
        )


def test_all_semantic_delta_families_validate_without_optional_providers() -> None:
    before = _evidence("before")
    after = _evidence("after")
    comparison = build_repository_comparison(
        repository_id=before.repository_id,
        before=_manifest(before),
        after=_manifest(after),
        lineage=_lineage(),
        deltas=_deltas(),
    )

    assert comparison.status is ComparisonStatus.PARTIAL
    assert {item.family.value for item in comparison.deltas} == set(_corpus()["delta_families"])
    diagnostic = next(item for item in comparison.deltas if item.family is DeltaFamily.DIAGNOSTIC)
    assert diagnostic.status is DeltaStatus.UNKNOWN
    assert diagnostic.availability is DeltaAvailability.UNAVAILABLE
    validate_comparison_evidence(comparison, before_evidence=before, after_evidence=after)


def test_exact_matcher_reports_semantic_changes_and_unique_file_moves() -> None:
    before = _evidence("before")
    after = _evidence("after")
    moved_id = _entity_id("lineage-unchanged", "after")
    moved = next(item for item in after.entities if item.entity_id == moved_id)
    assert isinstance(moved, FileEntity)
    after = after.model_copy(
        update={
            "entities": [
                item.model_copy(update={"path": "src/moved.py", "display_name": "src/moved.py"})
                if item.entity_id == moved_id
                else item
                for item in after.entities
            ]
        }
    )

    comparison = compare_repository_evidence(
        before,
        after,
        configuration_digest=_corpus()["configuration_digest"],
    )

    assert {item.family for item in comparison.deltas} == {
        DeltaFamily.API,
        DeltaFamily.ARTIFACT,
        DeltaFamily.DATA,
        DeltaFamily.DIAGNOSTIC,
        DeltaFamily.DOCUMENTATION,
        DeltaFamily.ENTITY,
        DeltaFamily.ENVIRONMENT,
    }
    assert any(item.kind is LineageKind.MOVED for item in comparison.lineage)
    assert not any(item.family is DeltaFamily.TEST for item in comparison.deltas)
    assert not any(item.family is DeltaFamily.WORKFLOW for item in comparison.deltas)


def test_boundary_failures_are_explicit_and_derive_status() -> None:
    before_evidence = _evidence("before")
    after_evidence = _evidence("after")
    before = _manifest(before_evidence)
    after = _manifest(after_evidence)
    observed: set[str] = set()

    absent = build_repository_comparison(
        repository_id=after.repository_id,
        before=None,
        after=after,
        lineage=[],
        deltas=[],
    )
    assert absent.status is ComparisonStatus.UNAVAILABLE
    observed.update(item.code.value for item in absent.issues)

    variants = [
        (before.model_copy(update={"history_status": HistoryStatus.SHALLOW}), after),
        (before.model_copy(update={"history_status": HistoryStatus.UNAVAILABLE}), after),
        (before.model_copy(update={"history_status": HistoryStatus.AMBIGUOUS}), after),
        (
            before,
            after.model_copy(
                update={
                    "source_state": after.source_state.model_copy(update={"dirty": True}),
                    "generated_entity_ids": ["entity:generated:after"],
                }
            ),
        ),
        (
            before,
            after.model_copy(
                update={
                    "provider_states": [
                        ProviderStateBinding(
                            **{
                                **after.provider_states[0].model_dump(),
                                "provider_version": "2.0.0",
                            }
                        )
                    ]
                }
            ),
        ),
        (before, after.model_copy(update={"configuration_digest": "sha256:different"})),
    ]
    for before_variant, after_variant in variants:
        comparison = build_repository_comparison(
            repository_id=after.repository_id,
            before=before_variant,
            after=after_variant,
            lineage=[],
            deltas=[],
        )
        assert comparison.status is ComparisonStatus.PARTIAL
        observed.update(item.code.value for item in comparison.issues)

    assert observed == set(_corpus()["boundary_codes"])


def test_comparison_serialization_is_canonical_atomic_and_fail_closed(tmp_path: Path) -> None:
    before = _evidence("before")
    after = _evidence("after")
    comparison = build_repository_comparison(
        repository_id=before.repository_id,
        before=_manifest(before),
        after=_manifest(after),
        lineage=_lineage(),
        deltas=_deltas(),
    )
    raw = canonical_comparison_bytes(comparison)
    assert canonical_comparison_bytes(parse_comparison(raw)) == raw

    output = tmp_path / "comparison.json"
    write_comparison(comparison, output)
    assert output.read_bytes() == raw

    incompatible = json.loads(raw)
    incompatible["schema_version"] = "2.0.0"
    with pytest.raises(ComparisonArtifactError) as schema:
        parse_comparison(json.dumps(incompatible).encode())
    assert schema.value.code == "comparison_schema_incompatible"

    with pytest.raises(ComparisonArtifactError) as corrupt:
        parse_comparison(b"{invalid")
    assert corrupt.value.code == "comparison_artifact_corrupt"

    tampered = json.loads(raw)
    tampered["deltas"][0]["rationale"] = "tampered without changing the delta identity"
    with pytest.raises(ComparisonArtifactError) as identity:
        parse_comparison(json.dumps(tampered).encode())
    assert identity.value.code == "comparison_artifact_invalid"


def test_comparison_rejects_digest_drift_unknown_targets_and_cross_state_lineage() -> None:
    before = _evidence("before")
    after = _evidence("after")
    comparison = build_repository_comparison(
        repository_id=before.repository_id,
        before=_manifest(before),
        after=_manifest(after),
        lineage=_lineage(),
        deltas=_deltas(),
    )
    changed_after = after.model_copy(update={"producer": EvidenceProducer(version="drift")})
    with pytest.raises(ValueError, match="after evidence digest"):
        validate_comparison_evidence(comparison, before_evidence=before, after_evidence=changed_after)

    unknown_delta = build_semantic_delta(
        family=DeltaFamily.TEST,
        status=DeltaStatus.ADDED,
        predecessor_refs=[],
        successor_refs=[_ref("after", EvidenceRecordType.ENTITY, "entity:missing")],
        lineage_ids=[],
        provider_run_ids=[],
        required_provider_ids=[],
        availability=DeltaAvailability.COMPLETE,
        strength=EvidenceStrength.EXACT,
        reason_codes=[DeltaReason.TEST_INTENT_CHANGED],
        rationale="Unknown target boundary case.",
    )
    unknown_comparison = build_repository_comparison(
        repository_id=before.repository_id,
        before=_manifest(before),
        after=_manifest(after),
        lineage=[],
        deltas=[unknown_delta],
    )
    with pytest.raises(ValueError, match="absent from evidence"):
        validate_comparison_evidence(unknown_comparison, before_evidence=before, after_evidence=after)

    payload = comparison.model_dump(mode="json")
    payload["lineage"][0]["predecessor_state_id"] = "state:after"
    with pytest.raises(ValidationError, match="different source states|does not bind"):
        type(comparison).model_validate(payload)
