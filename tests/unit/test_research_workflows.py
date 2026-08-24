from __future__ import annotations

import json

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.evidence import ContentClass
from anatomize.research import (
    EnvironmentDeclaration,
    ResearchArtifactError,
    ResearchDependency,
    ResearchGraphStatus,
    ResearchResource,
    ResourceAvailability,
    ResourceRole,
    SupplyChainFinding,
    WorkflowStep,
    parse_cyclonedx_sbom,
    parse_renv_lock,
    parse_ro_crate,
    parse_snakemake_workflow,
    parse_targets_manifest,
    research_graph_provider_envelope,
    unavailable_research_graph,
    workflow_lineage_dossier,
)
from tests.unit.test_evidence_models import _known_truth_evidence


def _json(value: object) -> bytes:
    return json.dumps(value).encode()


def _snakemake(**updates: object):  # type: ignore[no-untyped-def]
    value: dict[str, object] = {
        "workflow_id": "trial-pipeline",
        "rules": [
            {
                "name": "prepare",
                "script": "scripts/prepare.R",
                "input": [
                    {
                        "locator": "data/raw.csv",
                        "media_type": "text/csv",
                        "size_bytes": 120,
                        "digest": sha256_digest(b"raw-metadata"),
                        "content_class": "ordinary",
                    }
                ],
                "output": [
                    {
                        "locator": "derived/clean.parquet",
                        "media_type": "application/vnd.apache.parquet",
                        "digest": sha256_digest(b"clean"),
                        "content_class": "generated",
                    }
                ],
                "params": {"cohort": "eligible"},
                "resources": {"mem_mb": 512},
                "environment": {"kind": "conda", "name": "r-analysis", "locator": "envs/r.yml"},
            },
            {
                "name": "report",
                "script": "scripts/report.py",
                "input": [{"locator": "derived/clean.parquet", "content_class": "generated"}],
                "report": [
                    {
                        "locator": "reports/result.html",
                        "media_type": "text/html",
                        "content_class": "generated",
                    }
                ],
                "depends_on": ["prepare"],
            },
        ],
    }
    value.update(updates)
    return parse_snakemake_workflow(
        _json(value),
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:snakemake",
        provider_version="9.17",
    )


def test_snakemake_graph_deduplicates_resources_and_builds_reviewable_lineage_dossier() -> None:
    graph = _snakemake()

    assert graph.status is ResearchGraphStatus.COMPLETE
    resources = [item for item in graph.nodes if isinstance(item, ResearchResource)]
    assert len(resources) == 5
    intermediate = next(item for item in resources if item.locator == "derived/clean.parquet")
    assert intermediate.roles == [ResourceRole.INPUT, ResourceRole.OUTPUT]
    assert intermediate.content_included is False
    assert len([edge for edge in graph.edges if edge.target_node_id == intermediate.node_id]) == 2

    report = next(item for item in resources if item.locator == "reports/result.html")
    dossier = workflow_lineage_dossier(graph, target_node_id=report.node_id)
    selected = {item.node_id: item for item in graph.nodes if item.node_id in dossier.node_ids}
    assert dossier.status is ResearchGraphStatus.COMPLETE
    assert {item.name for item in selected.values() if isinstance(item, WorkflowStep)} == {"prepare", "report"}
    assert {item.locator for item in selected.values() if isinstance(item, ResearchResource)} == {
        "data/raw.csv",
        "derived/clean.parquet",
        "reports/result.html",
        "scripts/prepare.R",
        "scripts/report.py",
    }
    assert any(isinstance(item, EnvironmentDeclaration) for item in selected.values())


def test_snakemake_boundaries_are_visible_content_free_and_degraded() -> None:
    graph = _snakemake(
        dynamic_dag=True,
        rules=[
            {
                "name": "restricted",
                "dynamic": True,
                "input": [
                    {
                        "locator": "confidential/patients.csv",
                        "availability": "ignored",
                        "content_class": "sensitive",
                    },
                    {"locator": "s3://trial/input.parquet", "availability": "remote"},
                    {"locator": "archives/source.zip", "availability": "opaque", "role": "archive"},
                ],
                "output": [
                    {
                        "locator": "derived/missing.csv",
                        "availability": "missing",
                        "content_class": "generated",
                        "stale": True,
                    }
                ],
                "depends_on": ["runtime_rule"],
            }
        ],
    )

    assert graph.status is ResearchGraphStatus.PARTIAL
    assert any("runtime-dynamic DAG" in item for item in graph.limitations)
    assert any("unresolved dependency" in item for item in graph.limitations)
    resources = [item for item in graph.nodes if isinstance(item, ResearchResource)]
    confidential = next(item for item in resources if item.locator.startswith("confidential"))
    assert confidential.content_class is ContentClass.SENSITIVE
    assert confidential.availability is ResourceAvailability.IGNORED
    assert all(item.content_included is False for item in resources)


def test_targets_manifest_joins_declared_dependencies_to_observed_output_metadata() -> None:
    manifest = (
        b"name,command,path,dependencies,dynamic,sensitive\n"
        b"raw,read.csv('x'),derived/raw.rds,,,true\n"
        b"fit,fit(raw),models/fit.rds,raw,false,false\n"
    )
    metadata = b"name,bytes,data,missing,stale\nraw,22,hash-raw,false,false\nfit,84,hash-fit,false,true\n"
    graph = parse_targets_manifest(
        manifest,
        metadata_raw=metadata,
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:targets",
        provider_version="1.11",
    )

    assert graph.status is ResearchGraphStatus.PARTIAL
    assert any("marked stale" in item for item in graph.limitations)
    fit = next(item for item in graph.nodes if isinstance(item, WorkflowStep) and item.name == "fit")
    raw = next(item for item in graph.nodes if isinstance(item, WorkflowStep) and item.name == "raw")
    assert any(
        edge.source_node_id == fit.node_id and edge.target_node_id == raw.node_id and edge.predicate == "depends_on"
        for edge in graph.edges
    )
    raw_output = next(item for item in graph.nodes if isinstance(item, ResearchResource) and "raw.rds" in item.locator)
    assert raw_output.content_class is ContentClass.SENSITIVE


def test_renv_lock_preserves_environment_and_pinned_package_declarations() -> None:
    graph = parse_renv_lock(
        _json(
            {
                "R": {"Version": "4.5.1"},
                "Packages": {
                    "dplyr": {"Package": "dplyr", "Version": "1.1.4", "Source": "Repository", "Hash": "abc"},
                    "localpkg": {"Package": "localpkg", "Source": "Local"},
                },
            }
        ),
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:renv",
        provider_version="1.1",
    )

    assert graph.status is ResearchGraphStatus.PARTIAL
    environment = next(item for item in graph.nodes if isinstance(item, EnvironmentDeclaration))
    packages = [item for item in graph.nodes if isinstance(item, ResearchDependency)]
    assert environment.version == "4.5.1"
    assert {item.package for item in packages} == {"dplyr", "localpkg"}
    assert len([item for item in graph.edges if item.predicate == "declares_dependency"]) == 2
    assert any("no pinned version" in item for item in graph.limitations)


def test_cyclonedx_preserves_dependency_vulnerability_and_completeness_state() -> None:
    graph = parse_cyclonedx_sbom(
        _json(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [
                    {"bom-ref": "app", "type": "application", "name": "trial", "version": "1"},
                    {"bom-ref": "pkg", "type": "library", "name": "numpy", "version": "2", "purl": "pkg:pypi/numpy@2"},
                ],
                "dependencies": [{"ref": "app", "dependsOn": ["pkg"]}],
                "vulnerabilities": [
                    {"id": "CVE-TEST", "ratings": [{"severity": "high"}], "affects": [{"ref": "pkg"}]}
                ],
                "compositions": [{"aggregate": "incomplete"}],
            }
        ),
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:sbom",
        provider_version="1.6",
    )

    assert graph.status is ResearchGraphStatus.PARTIAL
    finding = next(item for item in graph.nodes if isinstance(item, SupplyChainFinding))
    numpy = next(item for item in graph.nodes if isinstance(item, ResearchDependency) and item.package == "numpy")
    assert finding.severity == "high"
    assert any(edge.source_node_id == finding.node_id and edge.target_node_id == numpy.node_id for edge in graph.edges)


def test_ro_crate_preserves_provenance_without_dereferencing_or_embedding_data() -> None:
    graph = parse_ro_crate(
        _json(
            {
                "@context": "https://w3id.org/ro/crate/1.2/context",
                "@graph": [
                    {
                        "@id": "./",
                        "@type": "Dataset",
                        "name": "Trial crate",
                        "hasPart": [{"@id": "data.csv"}, {"@id": "s3://bucket/raw.csv"}, {"@id": "archive.zip"}],
                    },
                    {
                        "@id": "data.csv",
                        "@type": "File",
                        "encodingFormat": "text/csv",
                        "contentSize": "42",
                        "anatomize:sensitive": True,
                    },
                    {"@id": "s3://bucket/raw.csv", "@type": "File", "encodingFormat": "text/csv"},
                    {"@id": "archive.zip", "@type": "File", "encodingFormat": "application/zip"},
                ],
            }
        ),
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:crate",
        provider_version="1.2",
    )

    assert graph.status is ResearchGraphStatus.PARTIAL
    resources = [item for item in graph.nodes if isinstance(item, ResearchResource)]
    assert next(item for item in resources if item.locator == "data.csv").content_class is ContentClass.SENSITIVE
    assert (
        next(item for item in resources if item.locator.startswith("s3:")).availability is ResourceAvailability.REMOTE
    )
    assert (
        next(item for item in resources if item.locator == "archive.zip").availability is ResourceAvailability.OPAQUE
    )
    assert all(item.content_included is False for item in resources)
    assert len([item for item in graph.edges if item.predicate == "has_part"]) == 3


def test_research_graph_normalizes_into_common_provider_contract() -> None:
    graph = _snakemake()
    envelope = research_graph_provider_envelope(
        graph,
        baseline=_known_truth_evidence(),
        policy_digest=sha256_digest(b"artifact-import-only"),
    )

    assert envelope.status.value == "complete"
    assert {item.entity_type.value for item in envelope.payload.entities}.issuperset(
        {"repository", "workflow", "data", "artifact", "configuration"}
    )
    assert len(envelope.payload.edges) == len(graph.edges)
    assert envelope.payload.completeness[0].status.value == "complete"


def test_invalid_workflow_artifacts_fail_with_stable_actionable_errors() -> None:
    with pytest.raises(ResearchArtifactError) as error:
        parse_snakemake_workflow(
            _json({"workflow_id": "trial"}),
            repository_id="repository:fixture",
            source_state_id="state:after",
            provider_run_id="run:bad",
            provider_version="1",
        )
    assert error.value.code == "snakemake_rules_missing"
    assert "Include" in error.value.remediation


def test_missing_research_tool_is_explicit_unavailable_provider_evidence() -> None:
    graph = unavailable_research_graph(
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="run:missing-snakemake",
        provider_id="snakemake-summary",
        provider_version="unavailable",
        evidence_families=["workflow", "artifact"],
        reason="Snakemake is not installed and no captured summary was supplied.",
    )
    envelope = research_graph_provider_envelope(
        graph,
        baseline=_known_truth_evidence(),
        policy_digest=sha256_digest(b"artifact-import-only"),
    )

    assert graph.status is ResearchGraphStatus.UNAVAILABLE
    assert envelope.status.value == "unavailable"
    assert envelope.payload.completeness[0].status.value == "unavailable"
    assert envelope.payload.omissions and envelope.payload.limitations
