from __future__ import annotations

import json
import shutil
from pathlib import Path

from anatomize._artifacts import canonical_json_bytes, sha256_digest
from anatomize.dossiers import (
    DossierBudget,
    DossierContext,
    DossierEngine,
    DossierProfile,
    DossierStatus,
    TargetKind,
    TargetResolutionStatus,
    TargetSelector,
    build_dossier_request,
    canonical_dossier_bytes,
)
from anatomize.evidence import RepositoryEvidence, merge_repository_evidence
from anatomize.index import (
    RepositoryIndex,
    build_repository_index,
)
from anatomize.lifecycle import (
    ChangeBoundaryKind,
    ChangeDimension,
    ClosureObservation,
    ClosureOutcome,
    ObligationKind,
    ObligationStatus,
    build_change_dossier,
    build_change_evidence,
    build_implementation_intent,
    build_implementation_obligation,
    extract_python_test_intent,
    parse_junit_xml,
    verify_implementation_closure,
)
from anatomize.providers import provider_envelope_evidence, repository_index_evidence
from anatomize.research import (
    EnvironmentDeclaration,
    NotebookArtifact,
    NotebookCellKind,
    OutputState,
    ResearchGraphArtifact,
    ResearchGraphStatus,
    ResearchResource,
    ResourceAvailability,
    SupplyChainFinding,
    WorkflowStep,
    compare_notebooks,
    extract_r_repository,
    notebook_execution_provider_envelope,
    parse_cyclonedx_sbom,
    parse_executable_document,
    parse_jupyter_notebook,
    parse_notebook_execution,
    parse_renv_lock,
    parse_ro_crate,
    parse_snakemake_workflow,
    parse_targets_manifest,
    research_graph_provider_envelope,
    unavailable_research_graph,
    workflow_lineage_dossier,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "research" / "mixed_project"
REPOSITORY_ID = "repository:mixed-research-fixture"


def test_mixed_research_project_supports_bounded_journeys_and_exact_change_closure(tmp_path: Path) -> None:
    project = tmp_path / "mixed-project"
    shutil.copytree(FIXTURE, project)
    before_index = build_repository_index(project)
    before_state = f"state:{before_index.source_state.fact_digest}"
    before_evidence = _baseline_with_notebook(before_index, project)

    duplicate = next(
        group
        for group in before_index.duplicate_groups
        if {member.path for member in group.members} == {"src/trialtools/legacy.py", "src/trialtools/normalise.py"}
    )
    normalise = next(item for item in before_index.symbols if item.name == "normalise_group")
    assert any(
        item.symbol_id == normalise.symbol_id and item.path == "tests/normalise_checks.py"
        for item in before_index.occurrences
    )

    r_artifact = extract_r_repository(
        _r_sources(project),
        repository_id=REPOSITORY_ID,
        source_state_id=before_state,
    )
    assert {item.qualified_name for item in r_artifact.functions}.issuperset(
        {"trialtools::estimate_effect", "trialtools::print.trial_effect", "trialtools::dynamic_helper"}
    )
    assert r_artifact.tests and any(item.dynamic for item in r_artifact.calls)

    notebook = parse_jupyter_notebook(
        (project / "analysis.ipynb").read_bytes(),
        repository_id=REPOSITORY_ID,
        source_state_id=before_state,
        path="analysis.ipynb",
    )
    qmd_before = parse_executable_document(
        (project / "analysis.qmd").read_text(encoding="utf-8"),
        repository_id=REPOSITORY_ID,
        source_state_id=before_state,
        path="analysis.qmd",
    )
    assert notebook.cells[1].output_state is OutputState.STALE
    assert notebook.cells[2].native_cell_id is None
    assert {item.language for item in qmd_before.cells if item.kind is NotebookCellKind.CODE} == {"python", "r"}
    execution = parse_notebook_execution(
        (project / "artifacts/notebook-execution.json").read_bytes(),
        repository_id=REPOSITORY_ID,
        source_state_id=before_state,
        notebook_document_digest=notebook.document_digest,
        provider_run_id="run:notebook-before",
        provider_id="nbclient",
        provider_version="0.10",
        environment_digest=_digest((project / "environment.yml").read_bytes()),
    )
    assert execution.status == "failed" and execution.observations[1].error_type == "RuntimeError"

    graphs = _research_graphs(project, before_state)
    clean = graphs["workflow"]
    boundary = graphs["boundary"]
    assert clean.status is ResearchGraphStatus.COMPLETE
    assert boundary.status is ResearchGraphStatus.PARTIAL
    report = _resource(clean, "reports/result.html")
    lineage = workflow_lineage_dossier(clean, target_node_id=report.node_id)
    assert lineage.status is ResearchGraphStatus.COMPLETE
    assert len(lineage.node_ids) < len(clean.nodes) + 1
    assert any(isinstance(node, EnvironmentDeclaration) and node.node_id in lineage.node_ids for node in clean.nodes)
    assert any(isinstance(node, SupplyChainFinding) for node in graphs["sbom"].nodes)
    assert any(
        isinstance(node, ResearchResource) and node.availability is ResourceAvailability.REMOTE
        for node in graphs["crate"].nodes
    )

    envelopes = [
        research_graph_provider_envelope(
            graph,
            baseline=before_evidence,
            policy_digest=_digest(b"offline-artifact-import"),
        )
        for graph in (clean, boundary, graphs["sbom"])
    ]
    missing = unavailable_research_graph(
        repository_id=REPOSITORY_ID,
        source_state_id=before_state,
        provider_run_id="run:missing-r-languageserver",
        provider_id="r-languageserver",
        provider_version="unavailable",
        evidence_families=["r_semantics"],
        reason="R languageserver was deliberately absent in the offline fixture run.",
    )
    envelopes.append(
        research_graph_provider_envelope(
            missing,
            baseline=before_evidence,
            policy_digest=_digest(b"offline-artifact-import"),
        )
    )
    envelopes.append(
        notebook_execution_provider_envelope(
            execution,
            notebook=notebook,
            baseline=before_evidence,
            configuration_digest=_digest(b"notebook-import-configuration"),
            policy_digest=_digest(b"offline-artifact-import"),
        )
    )
    assert {item.status.value for item in envelopes}.issuperset({"complete", "partial", "unavailable"})
    assert all(item.invocation.mode.value == "artifact_import" for item in envelopes)
    assert all(item.invocation.network_policy.value == "not_applicable" for item in envelopes)

    evidence = [before_evidence, *(provider_envelope_evidence(item) for item in envelopes)]
    merged_evidence = merge_repository_evidence(evidence)
    context = DossierContext.from_evidence(
        session_id="session:mixed-research-before",
        session_manifest_digest=_digest(b"mixed-research-manifest"),
        repository_id=REPOSITORY_ID,
        source_state_ids=[before_state],
        provider_run_ids=[run.provider_run_id for artifact in evidence for run in artifact.provider_runs],
        policy_digest=_digest(b"ordinary-source-no-data-content"),
        evidence=[merged_evidence],
    )
    journey_targets = _journey_targets(before_evidence, clean, boundary)
    dossiers = {}
    engine = DossierEngine(context)
    for journey, (kind, identity) in journey_targets.items():
        request = build_dossier_request(
            profile=DossierProfile.AUDIT if journey in {"large-data", "sensitive-data"} else DossierProfile.DESIGN,
            question=f"What evidence governs the {journey} decision?",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=[TargetSelector(kind=kind, identity=identity, source_state_id=before_state)],
            budget=DossierBudget(max_items=32, max_payload_bytes=96_000, max_depth=3),
        )
        dossier = engine.query(request)
        dossiers[journey] = dossier
        assert dossier.status in {DossierStatus.COMPLETE, DossierStatus.PARTIAL}
        assert dossier.boundary.targets[0].status is TargetResolutionStatus.EXACT
        assert len(canonical_dossier_bytes(dossier)) <= 96_000
        assert dossier.source_state_ids == [before_state]
    assert set(dossiers) == {
        "package-led",
        "analysis-led",
        "mixed-python-r",
        "notebook",
        "pipeline",
        "generated-report",
        "large-data",
        "sensitive-data",
    }
    assert dossiers["sensitive-data"].omissions

    obligations = [
        build_implementation_obligation(
            kind=kind,
            subject_ref=f"mixed-project:{kind.value}",
            expectation=f"Revalidate {kind.value} after consolidating the duplicate helper.",
            required_evidence_kinds=[kind.value],
        )
        for kind in (
            ObligationKind.OWNER,
            ObligationKind.CONTRACT,
            ObligationKind.CONSUMER,
            ObligationKind.CANDIDATE,
            ObligationKind.TEST,
            ObligationKind.DOCUMENTATION,
            ObligationKind.WORKFLOW,
        )
    ]
    before_provider_digests = _change_provider_digests(project, before_index, clean, qmd_before)
    intent = build_implementation_intent(
        repository_id=REPOSITORY_ID,
        before_source_state_id=before_state,
        before_dossier_id=dossiers["package-led"].dossier_id,
        before_evidence_digest=_digest(canonical_json_bytes(before_evidence.model_dump(mode="json"))),
        before_provider_digests=before_provider_digests,
        decision_overlay_id=None,
        obligations=obligations,
        declared_unknowns=[],
    )

    _apply_controlled_change(project)
    after_index = build_repository_index(project)
    after_state = f"state:{after_index.source_state.fact_digest}"
    after_workflow = parse_snakemake_workflow(
        (project / "workflow/snakemake-summary.json").read_bytes(),
        repository_id=REPOSITORY_ID,
        source_state_id=after_state,
        provider_run_id="run:workflow-after",
        provider_version="9.17",
    )
    qmd_after = parse_executable_document(
        (project / "analysis.qmd").read_text(encoding="utf-8"),
        repository_id=REPOSITORY_ID,
        source_state_id=after_state,
        path="analysis.qmd",
    )
    after_test = extract_python_test_intent(
        (project / "tests/normalise_checks.py").read_text(encoding="utf-8"),
        repository_id=REPOSITORY_ID,
        source_state_id=after_state,
        path="tests/normalise_checks.py",
    )
    after_runtime = parse_junit_xml(
        (project / "artifacts/pytest-junit.xml").read_bytes(),
        repository_id=REPOSITORY_ID,
        source_state_id=after_state,
        provider_run_id="run:pytest-after",
        provider_version="9.1",
        environment_digest=_digest((project / "environment.yml").read_bytes()),
        selection=["tests/normalise_checks.py"],
    )
    assert after_index.duplicate_groups == []
    assert after_test.intents[0].targets == ["normalise_group"]
    assert after_runtime.observations[0].outcome == "passed"
    assert any(item.invalidates_execution for item in compare_notebooks(qmd_before, qmd_after))
    assert after_workflow.source_artifact_digest != clean.source_artifact_digest
    assert _resource(after_workflow, "derived/effect.rds").digest != _resource(clean, "derived/effect.rds").digest

    changes = [
        build_change_evidence(
            dimension=dimension,
            kinds=[kind],
            predecessor_refs=[before_ref],
            successor_refs=[after_ref],
            evidence_refs=evidence_refs,
            lineage="exact",
            rationale=rationale,
        )
        for dimension, kind, before_ref, after_ref, evidence_refs, rationale in (
            (
                ChangeDimension.ENTITY,
                ChangeBoundaryKind.REEXPORTED,
                duplicate.group_id,
                "trialtools.legacy:alias",
                ["src/trialtools/legacy.py"],
                "Duplicate implementation became an explicit alias.",
            ),
            (
                ChangeDimension.TEST,
                ChangeBoundaryKind.MODIFIED,
                "test:before",
                after_test.intents[0].intent_id,
                [after_runtime.observations[0].observation_id],
                "Static intent and selected runtime evidence were refreshed.",
            ),
            (
                ChangeDimension.DOCUMENTATION,
                ChangeBoundaryKind.MODIFIED,
                "README:before",
                "README:after",
                ["README.md"],
                "The ownership claim now names one implementation and one alias.",
            ),
            (
                ChangeDimension.WORKFLOW,
                ChangeBoundaryKind.MODIFIED,
                clean.source_artifact_digest,
                after_workflow.source_artifact_digest,
                ["workflow/snakemake-summary.json"],
                "Workflow metadata binds the refreshed environment and output.",
            ),
            (
                ChangeDimension.ENVIRONMENT,
                ChangeBoundaryKind.MODIFIED,
                before_provider_digests["environment"],
                _digest((project / "environment.yml").read_bytes()),
                ["environment.yml"],
                "The environment declaration was refreshed.",
            ),
            (
                ChangeDimension.ARTIFACT,
                ChangeBoundaryKind.MODIFIED,
                "effect-v1",
                "effect-v2",
                ["derived/effect.metadata.json"],
                "Generated artifact identity was refreshed.",
            ),
            (
                ChangeDimension.DOCUMENTATION,
                ChangeBoundaryKind.MODIFIED,
                qmd_before.document_digest,
                qmd_after.document_digest,
                ["analysis.qmd"],
                "Executable-document source was refreshed and prior execution invalidated.",
            ),
        )
    ]
    after_provider_digests = _change_provider_digests(project, after_index, after_workflow, qmd_after)
    changed = build_change_dossier(
        repository_id=REPOSITORY_ID,
        before_source_state_id=before_state,
        after_source_state_id=after_state,
        before_dossier_id=dossiers["package-led"].dossier_id,
        after_dossier_id=f"dossier:after:{after_index.source_state.fact_digest}",
        before_evidence_digest=intent.before_evidence_digest,
        after_evidence_digest=_digest(after_index.model_dump_json().encode()),
        before_provider_digests=before_provider_digests,
        after_provider_digests=after_provider_digests,
        changes=changes,
        affected_evidence_refs=[
            "src/trialtools/legacy.py",
            "tests/normalise_checks.py",
            "README.md",
            "analysis.qmd",
            "workflow/snakemake-summary.json",
            "environment.yml",
            "derived/effect.metadata.json",
        ],
        blast_radius_refs=["normalise_group", "estimate", "report"],
        limitations=["Runtime evidence covers the declared selected test only."],
    )
    closure = verify_implementation_closure(
        intent,
        after_source_state_id=after_state,
        after_source_state_digest=_digest(after_index.model_dump_json().encode()),
        after_provider_digests=after_provider_digests,
        provider_completeness={provider: "complete" for provider in after_provider_digests},
        observations=[
            ClosureObservation(
                obligation_id=obligation.obligation_id,
                source_state_id=after_state,
                status=ObligationStatus.SATISFIED,
                evidence_refs=[f"after:{obligation.kind.value}", changed.dossier_id],
                observed=f"Revalidated {obligation.kind.value} against after-state evidence.",
            )
            for obligation in obligations
        ],
        evidence_kinds_by_ref={
            **{
                f"after:{obligation.kind.value}": {obligation.kind.value}
                for obligation in obligations
            },
            changed.dossier_id: {"change"},
        },
    )
    assert before_state != after_state
    assert len(changed.changes) == 7
    assert closure.outcome is ClosureOutcome.CLOSED


def _baseline_with_notebook(index: RepositoryIndex, project: Path) -> RepositoryEvidence:
    del project
    return repository_index_evidence(index, repository_id=REPOSITORY_ID)


def _research_graphs(project: Path, state: str) -> dict[str, ResearchGraphArtifact]:
    common = {"repository_id": REPOSITORY_ID, "source_state_id": state, "provider_version": "fixture-1"}
    return {
        "workflow": parse_snakemake_workflow(
            (project / "workflow/snakemake-summary.json").read_bytes(),
            provider_run_id="run:workflow-before",
            **common,
        ),
        "boundary": parse_snakemake_workflow(
            (project / "workflow/snakemake-boundaries.json").read_bytes(),
            provider_run_id="run:workflow-boundary",
            **common,
        ),
        "targets": parse_targets_manifest(
            (project / "workflow/targets-manifest.csv").read_bytes(),
            metadata_raw=(project / "workflow/targets-metadata.csv").read_bytes(),
            provider_run_id="run:targets-before",
            repository_id=REPOSITORY_ID,
            source_state_id=state,
            provider_version="fixture-1",
        ),
        "renv": parse_renv_lock(
            (project / "renv.lock").read_bytes(),
            provider_run_id="run:renv-before",
            **common,
        ),
        "sbom": parse_cyclonedx_sbom(
            (project / "artifacts/sbom.cdx.json").read_bytes(),
            provider_run_id="run:sbom-before",
            **common,
        ),
        "crate": parse_ro_crate(
            (project / "ro-crate-metadata.json").read_bytes(),
            provider_run_id="run:crate-before",
            **common,
        ),
    }


def _journey_targets(
    baseline: RepositoryEvidence,
    workflow: ResearchGraphArtifact,
    boundary: ResearchGraphArtifact,
) -> dict[str, tuple[TargetKind, str]]:
    normalise = next(
        item for item in baseline.entities if getattr(item, "qualified_name", "").endswith("normalise_group")
    )
    estimate = next(item for item in workflow.nodes if isinstance(item, WorkflowStep) and item.name == "estimate")
    preprocess = next(item for item in workflow.nodes if isinstance(item, WorkflowStep) and item.name == "preprocess")
    report_step = next(item for item in workflow.nodes if isinstance(item, WorkflowStep) and item.name == "report")
    remote = next(
        item
        for item in boundary.nodes
        if isinstance(item, ResearchResource) and item.availability is ResourceAvailability.REMOTE
    )
    sensitive = next(
        item
        for item in boundary.nodes
        if isinstance(item, ResearchResource) and item.content_class.value == "sensitive"
    )
    return {
        "package-led": (TargetKind.SYMBOL, normalise.entity_id),
        "analysis-led": (TargetKind.WORKFLOW, estimate.node_id),
        "mixed-python-r": (TargetKind.WORKFLOW, preprocess.node_id),
        "notebook": (TargetKind.FILE, "file:analysis.ipynb"),
        "pipeline": (TargetKind.WORKFLOW, report_step.node_id),
        "generated-report": (TargetKind.ARTIFACT, _resource(workflow, "reports/result.html").node_id),
        "large-data": (TargetKind.DATA, remote.node_id),
        "sensitive-data": (TargetKind.DATA, sensitive.node_id),
    }


def _resource(graph: ResearchGraphArtifact, locator: str) -> ResearchResource:
    return next(item for item in graph.nodes if isinstance(item, ResearchResource) and item.locator == locator)


def _r_sources(project: Path) -> dict[str, str]:
    paths = [
        "DESCRIPTION",
        "NAMESPACE",
        "R/estimate.R",
        "analysis/run_analysis.R",
        "tests/testthat/test-estimate.R",
        "analysis.qmd",
    ]
    return {path: (project / path).read_text(encoding="utf-8") for path in paths}


def _digest(raw: bytes) -> str:
    return sha256_digest(raw)


def _change_provider_digests(
    project: Path,
    index: RepositoryIndex,
    workflow: ResearchGraphArtifact,
    qmd: NotebookArtifact,
) -> dict[str, str]:
    return {
        "baseline": _digest(index.model_dump_json().encode()),
        "workflow": workflow.source_artifact_digest,
        "environment": _digest((project / "environment.yml").read_bytes()),
        "executable_document": qmd.document_digest,
        "artifact": _digest((project / "derived/effect.metadata.json").read_bytes()),
    }


def _apply_controlled_change(project: Path) -> None:
    (project / "src/trialtools/legacy.py").write_text(
        "from trialtools.normalise import normalise_group\n\ncanonicalise_group = normalise_group\n"
    )
    (project / "tests/normalise_checks.py").write_text(
        "from trialtools.normalise import normalise_group\n\n\n"
        "def test_normalise_group() -> None:\n"
        "    assert normalise_group(' Treatment A ') == 'treatment-a'\n"
    )
    (project / "README.md").write_text(
        "# Trialtools fixture\n\n"
        "`trialtools.normalise_group()` is the sole cohort-key implementation; "
        "`trialtools.legacy.canonicalise_group` is a compatibility alias.\n"
    )
    qmd = (
        (project / "analysis.qmd")
        .read_text(encoding="utf-8")
        .replace(
            "The report consumes the generated cohort and effect estimate.",
            "The report consumes the regenerated cohort and effect estimate under the v2 environment.",
        )
    )
    (project / "analysis.qmd").write_text(qmd)
    workflow_path = project / "workflow/snakemake-summary.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["rules"][1]["environment"]["digest"] = _digest(b"renv-v2")
    workflow["rules"][1]["output"][0]["digest"] = _digest(b"effect-v2")
    workflow_path.write_text(json.dumps(workflow, indent=2) + "\n")
    (project / "environment.yml").write_text(
        "name: trial-analysis-v2\nchannels: [conda-forge]\ndependencies:\n"
        "  - python=3.12\n  - r-base=4.5\n  - pandas=2.3.2\n"
    )
    (project / "derived/effect.metadata.json").write_text(
        '{"locator":"derived/effect.rds","media_type":"application/x-r-rds",'
        '"size_bytes":96,"digest":"effect-v2","generated":true,"content_included":false}\n'
    )
    (project / "artifacts/pytest-junit.xml").write_text(
        '<testsuite name="pytest" tests="1" failures="0">'
        '<testcase classname="tests.normalise_checks" name="test_normalise_group"/></testsuite>\n'
    )
