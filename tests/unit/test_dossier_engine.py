from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from anatomize._artifacts import canonical_ordered_json_bytes, sha256_digest
from anatomize.dossiers import (
    DossierArtifactError,
    DossierBudget,
    DossierContext,
    DossierEngine,
    DossierExpansion,
    DossierFilters,
    DossierLocator,
    DossierProfile,
    DossierQueryError,
    DossierRequest,
    DossierStatus,
    EvidenceRole,
    ExpansionKind,
    OmissionReason,
    SourceSlicePolicy,
    TargetKind,
    TargetResolutionStatus,
    TargetSelector,
    build_dossier_cursor,
    build_dossier_request,
    build_dossier_source,
    canonical_dossier_bytes,
    decode_dossier_cursor,
    dossier_json_schema,
    dossier_request_json_schema,
    parse_dossier,
    parse_dossier_request,
)
from anatomize.evidence import ContentClass, RepositoryEvidence, canonical_evidence_bytes
from anatomize.sessions import (
    ArtifactRole,
    BudgetBinding,
    OmissionScope,
    SchemaBinding,
    SessionProviderBinding,
    SessionStatus,
    build_omission,
    build_query,
    build_session_artifact,
    build_session_bundle,
    build_session_manifest,
)
from anatomize.temporal import HistoryStatus, build_state_manifest
from tests.unit.test_evidence_models import _known_truth_evidence

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "dossiers"


def _digest(value: str) -> str:
    return sha256_digest(value.encode("utf-8"))


def _context() -> DossierContext:
    evidence = _known_truth_evidence()
    return DossierContext.from_evidence(
        session_id="session:dossier-known-truth",
        session_manifest_digest=_digest("manifest"),
        repository_id=evidence.repository_id,
        source_state_ids=[item.state_id for item in evidence.states],
        provider_run_ids=[item.provider_run_id for item in evidence.provider_runs],
        policy_digest=_digest("policy"),
        evidence=[evidence],
    )


def _context_with_sources() -> DossierContext:
    base = _context()
    return DossierContext.from_evidence(
        session_id=base.session_id,
        session_manifest_digest=base.session_manifest_digest,
        repository_id=base.repository_id,
        source_state_ids=list(base.source_state_ids),
        provider_run_ids=list(base.provider_run_ids),
        policy_digest=base.policy_digest,
        evidence=list(base.evidence),
        sources=[
            build_dossier_source(
                source_state_id="state:before",
                path="src/pkg/core.py",
                media_type="text/x-python",
                language="python",
                text="def answer():\n    return 41\n# baseline\n",
            ),
            build_dossier_source(
                source_state_id="state:after",
                path="src/pkg/core.py",
                media_type="text/x-python",
                language="python",
                text="def answer():\n    return 42\n# changed\n# trailing context\n",
            ),
            build_dossier_source(
                source_state_id="state:after",
                path="build/generated.py",
                media_type="text/x-python",
                language="python",
                content_class=ContentClass.GENERATED,
                text="x = 42\n",
            ),
        ],
    )


def _profile_context() -> DossierContext:
    base = _context_with_sources()
    payload = base.evidence[0].model_dump(mode="json")
    payload["edges"].extend(
        [
            {
                "record_type": "edge",
                "edge_id": edge_id,
                "source_state_id": "state:after",
                "source_entity_id": source,
                "target_entity_id": target,
                "category": category,
                "predicate": predicate,
            }
            for edge_id, source, target, category, predicate in [
                ("edge:caller", "entity:test", "entity:symbol-after", "call", "calls"),
                ("edge:test", "entity:symbol-after", "entity:test", "test", "tested_by"),
                ("edge:callee", "entity:symbol-after", "entity:external", "call", "calls"),
                ("edge:consumer", "entity:documentation", "entity:symbol-after", "reference", "references"),
                (
                    "edge:configuration",
                    "entity:symbol-after",
                    "entity:configuration",
                    "configuration",
                    "configured_by",
                ),
                (
                    "edge:documentation",
                    "entity:symbol-after",
                    "entity:documentation",
                    "documentation",
                    "documented_by",
                ),
                ("edge:data", "entity:symbol-after", "entity:data", "data", "reads"),
                ("edge:workflow", "entity:workflow", "entity:symbol-after", "workflow", "runs"),
                ("edge:artifact", "entity:symbol-after", "entity:artifact", "artifact", "produces"),
                (
                    "edge:dependency",
                    "entity:symbol-after",
                    "entity:dependency",
                    "dependency",
                    "depends_on",
                ),
            ]
        ]
    )
    return DossierContext.from_evidence(
        session_id=base.session_id,
        session_manifest_digest=base.session_manifest_digest,
        repository_id=base.repository_id,
        source_state_ids=list(base.source_state_ids),
        provider_run_ids=list(base.provider_run_ids),
        policy_digest=base.policy_digest,
        evidence=[RepositoryEvidence.model_validate(payload)],
        sources=list(base.sources),
    )


def _request(
    target: TargetSelector,
    *,
    profile: DossierProfile = DossierProfile.LOCALISATION,
    budget: DossierBudget | None = None,
    filters: DossierFilters | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    expansion: DossierExpansion | None = None,
) -> DossierRequest:
    context = _context()
    return build_dossier_request(
        profile=profile,
        question="Locate the selected known-truth target.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[target],
        budget=budget,
        filters=filters,
        include=include,
        exclude=exclude,
        expansion=expansion,
    )


@pytest.mark.parametrize(
    ("selector", "expected_id"),
    [
        (
            TargetSelector(kind=TargetKind.REPOSITORY, identity="repository:fixture", source_state_id="state:after"),
            "entity:repository-after",
        ),
        (
            TargetSelector(
                kind=TargetKind.FILE, locator=DossierLocator(path="src/pkg/core.py"), source_state_id="state:after"
            ),
            "entity:file-after",
        ),
        (
            TargetSelector(
                kind=TargetKind.SYMBOL,
                locator=DossierLocator(qualified_name="pkg.core.answer"),
                source_state_id="state:after",
            ),
            "entity:symbol-after",
        ),
        (
            TargetSelector(
                kind=TargetKind.RANGE,
                locator=DossierLocator(
                    path="src/pkg/core.py", start_line=1, start_column=0, end_line=2, end_column=13
                ),
                source_state_id="state:after",
            ),
            "entity:range",
        ),
        (
            TargetSelector(
                kind=TargetKind.DOCUMENTATION_SECTION,
                locator=DossierLocator(heading="Answer API"),
                source_state_id="state:after",
            ),
            "entity:documentation",
        ),
        (
            TargetSelector(
                kind=TargetKind.TEST, locator=DossierLocator(name="test_answer"), source_state_id="state:after"
            ),
            "entity:test",
        ),
        (
            TargetSelector(
                kind=TargetKind.CONFIGURATION,
                locator=DossierLocator(name="tool.fixture"),
                source_state_id="state:after",
            ),
            "entity:configuration",
        ),
        (
            TargetSelector(kind=TargetKind.DATA, locator=DossierLocator(name="cohort"), source_state_id="state:after"),
            "entity:data",
        ),
        (
            TargetSelector(
                kind=TargetKind.WORKFLOW, locator=DossierLocator(name="analyse"), source_state_id="state:after"
            ),
            "entity:workflow",
        ),
        (
            TargetSelector(
                kind=TargetKind.ARTIFACT, locator=DossierLocator(name="results.csv"), source_state_id="state:after"
            ),
            "entity:artifact",
        ),
        (
            TargetSelector(
                kind=TargetKind.DIAGNOSTIC, locator=DossierLocator(rule_id="F401"), source_state_id="state:after"
            ),
            "entity:diagnostic",
        ),
        (
            TargetSelector(
                kind=TargetKind.DUPLICATE_CANDIDATE, identity="candidate:duplicate", source_state_id="state:after"
            ),
            "candidate:duplicate",
        ),
        (
            TargetSelector(
                kind=TargetKind.REVISION, locator=DossierLocator(revision="abc123"), source_state_id="state:before"
            ),
            "state:before",
        ),
        (
            TargetSelector(
                kind=TargetKind.EXTERNAL_DEPENDENCY,
                locator=DossierLocator(name="requests"),
                source_state_id="state:after",
            ),
            "entity:dependency",
        ),
    ],
)
def test_known_truth_target_families_resolve_exactly(selector: TargetSelector, expected_id: str) -> None:
    request = _request(selector)
    dossier = DossierEngine(_context()).query(request)

    resolution = dossier.boundary.targets[0]
    assert resolution.status is TargetResolutionStatus.EXACT
    assert resolution.resolved_ids == [expected_id]
    assert expected_id in {item.record_id for item in dossier.items}


def test_projection_preserves_relationship_reasons_observations_conflicts_and_role_order() -> None:
    request = _request(
        TargetSelector(kind=TargetKind.FILE, identity="entity:file-after"),
        profile=DossierProfile.DESIGN,
    )
    dossier = DossierEngine(_context()).query(request)
    symbol = next(item for item in dossier.items if item.record_id == "entity:symbol-after")

    assert symbol.relationship_ids == ["edge:defines"]
    assert {item.observation_id for item in symbol.observations} >= {
        "observation:ast-defines",
        "observation:semantic-conflict",
    }
    assert {item.provider_run_id for item in symbol.observations} == {"provider:ast", "provider:semantic"}
    assert symbol.conflict_ids == ["conflict:defines"]
    assert len(symbol.selection_reasons) == len(
        {(item.code, item.subject_id, item.object_id, item.relationship_id) for item in symbol.selection_reasons}
    )
    section_rank = {"decision_critical": 0, "contradictions_unknowns": 1, "supporting_context": 2}
    assert [section_rank[group.section.value] for group in dossier.groups] == sorted(
        section_rank[group.section.value] for group in dossier.groups
    )
    assert any(group.role is EvidenceRole.CONFLICT for group in dossier.groups)


def test_query_algebra_filters_exclusions_budgets_cursors_and_cache_compose() -> None:
    context = _context()
    engine = DossierEngine(context)
    target = TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")
    first_request = build_dossier_request(
        profile=DossierProfile.IMPLEMENTATION,
        question="Locate the selected known-truth target.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[target],
        include=["entity:configuration"],
        budget=DossierBudget(max_items=2),
    )

    first = engine.query(first_request)
    assert first is engine.query(first_request)
    assert engine.cached_query_count == 1
    assert first.status is DossierStatus.PARTIAL
    cursor_action = next(item for item in first.expansions if item.kind is ExpansionKind.CURSOR)
    assert cursor_action.cursor is not None
    assert decode_dossier_cursor(cursor_action.cursor.token()) == cursor_action.cursor

    second_request = build_dossier_request(
        profile=first_request.profile,
        question=first_request.question,
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=first_request.targets,
        include=first_request.include,
        budget=DossierBudget(max_items=2),
        expansion=DossierExpansion(
            kind=ExpansionKind.CURSOR,
            base_dossier_id=first.dossier_id,
            action_id=cursor_action.action_id,
            cursor=cursor_action.cursor,
        ),
    )
    second = engine.query(second_request)
    assert {item.record_id for item in first.items}.isdisjoint(item.record_id for item in second.items)
    assert second.base_dossier_id == first.dossier_id

    filtered_request = build_dossier_request(
        profile=DossierProfile.IMPLEMENTATION,
        question="Locate the selected known-truth target.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[target],
        filters=DossierFilters(roles=[EvidenceRole.TEST]),
        exclude=["entity:symbol-after"],
    )
    filtered = engine.query(filtered_request)
    assert filtered.status is DossierStatus.PARTIAL
    assert all(item.role is EvidenceRole.TEST for item in filtered.items)
    assert any(item.required and item.reason.value == "scope" for item in filtered.omissions)


def test_lifecycle_profiles_project_coherent_available_proof_roles() -> None:
    context = _profile_context()
    engine = DossierEngine(context)
    orientation = engine.query(
        build_dossier_request(
            profile=DossierProfile.ORIENTATION,
            question="Orient this repository.",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
        )
    )
    orientation_roles = {item.role for item in orientation.items}
    assert orientation.boundary.targets[0].resolved_ids == ["entity:repository-after"]
    assert orientation_roles >= {
        EvidenceRole.STATE,
        EvidenceRole.TOPOLOGY,
        EvidenceRole.OWNERSHIP,
        EvidenceRole.PUBLIC_SURFACE,
        EvidenceRole.ENTRY_POINT,
        EvidenceRole.TEST,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.DATA,
        EvidenceRole.WORKFLOW,
        EvidenceRole.ARTIFACT,
        EvidenceRole.DEPENDENCY,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.RUNTIME,
        EvidenceRole.DUPLICATE_CANDIDATE,
    }

    target = [TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")]
    dossiers = {
        profile: engine.query(
            build_dossier_request(
                profile=profile,
                question=f"Exercise {profile.value}.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=target,
            )
        )
        for profile in DossierProfile
        if profile is not DossierProfile.ORIENTATION
    }
    design_roles = {item.role for item in dossiers[DossierProfile.DESIGN].items}
    audit_roles = {item.role for item in dossiers[DossierProfile.AUDIT].items}
    assert design_roles >= {
        EvidenceRole.CONTRACT,
        EvidenceRole.CALLEE,
        EvidenceRole.CONSUMER,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.CONFLICT,
        EvidenceRole.DUPLICATE_CANDIDATE,
    }
    assert audit_roles >= {EvidenceRole.CONTRACT, EvidenceRole.DIAGNOSTIC, EvidenceRole.CONFLICT}
    assert all("score" not in item.model_dump(mode="json") for item in dossiers[DossierProfile.AUDIT].items)

    for profile in (DossierProfile.LOCALISATION, DossierProfile.IMPLEMENTATION):
        roles = {item.role for item in dossiers[profile].items}
        assert roles >= {
            EvidenceRole.DEFINITION,
            EvidenceRole.CALLER,
            EvidenceRole.CALLEE,
            EvidenceRole.CONSUMER,
            EvidenceRole.TEST,
            EvidenceRole.DOCUMENTATION,
            EvidenceRole.CONFIGURATION,
            EvidenceRole.RUNTIME,
            EvidenceRole.CHANGE,
        }

    review = dossiers[DossierProfile.CHANGE_REVIEW]
    review_roles = {item.role for item in review.items}
    assert review_roles >= {
        EvidenceRole.STATE,
        EvidenceRole.CHANGE,
        EvidenceRole.TEST,
        EvidenceRole.DOCUMENTATION,
        EvidenceRole.CONFIGURATION,
        EvidenceRole.WORKFLOW,
        EvidenceRole.ARTIFACT,
        EvidenceRole.DIAGNOSTIC,
        EvidenceRole.CONFLICT,
    }
    assert {"state:before", "state:after", "entity:symbol-before", "entity:symbol-after"} <= {
        item.record_id for item in review.items
    }
    assert {ExpansionKind.HISTORY, ExpansionKind.VALIDATION} <= {item.kind for item in review.expansions}


def test_source_slices_are_exact_bounded_and_deduplicated_across_roles() -> None:
    context = _context_with_sources()
    request = build_dossier_request(
        profile=DossierProfile.IMPLEMENTATION,
        question="Inspect the exact implementation source.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")],
        budget=DossierBudget(max_content_block_bytes=16_384, max_inline_content_bytes=32_768),
        slice_policy=SourceSlicePolicy(context_before=0, context_after=0),
    )
    dossier = DossierEngine(context).query(request)
    content_by_id = {item.content_id: item for item in dossier.content}
    linked = [item.content_id for item in dossier.items if item.content_id is not None]

    assert linked
    assert len(dossier.content) == len(content_by_id)
    assert len(linked) > len(set(linked))
    symbol = next(item for item in dossier.items if item.record_id == "entity:symbol-after")
    assert symbol.content_id is not None
    source = content_by_id[symbol.content_id]
    assert source.path == "src/pkg/core.py"
    assert source.source_state_id == "state:after"
    assert source.language == "python"
    assert source.enclosing_entity_id == "entity:symbol-after"
    assert source.source_range is not None and source.source_range.start_line == 1
    assert source.text == "def answer():\n    return 42\n"
    assert source.digest.startswith("sha256:") and source.full_content_digest.startswith("sha256:")
    assert dossier.budget_use.inline_content_bytes.used == sum(
        len(item.text.encode("utf-8")) for item in dossier.content if item.text is not None
    )
    full_file_bytes = len(b"def answer():\n    return 42\n# changed\n# trailing context\n")
    assert dossier.budget_use.inline_content_bytes.used < full_file_bytes


def test_all_expansion_kinds_use_the_same_engine_and_preserve_the_base() -> None:
    context = _context_with_sources()
    engine = DossierEngine(context)
    target = TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")
    request = build_dossier_request(
        profile=DossierProfile.IMPLEMENTATION,
        question="Exercise the complete expansion algebra.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[target],
        slice_policy=SourceSlicePolicy(context_before=0, context_after=0),
    )
    base = engine.query(request)
    omission_id = base.omissions[0].omission_id
    group_id = base.groups[0].group_id
    cases = [
        (ExpansionKind.GROUP, group_id, None),
        (ExpansionKind.ENTITY, "entity:configuration", None),
        (ExpansionKind.RELATIONSHIP, "edge:defines", None),
        (ExpansionKind.ROLE, None, EvidenceRole.TEST),
        (ExpansionKind.DEPTH, None, None),
        (ExpansionKind.ADJACENT_CONTEXT, "entity:symbol-after", None),
        (ExpansionKind.COMPLETE_FILE, "entity:symbol-after", None),
        (ExpansionKind.PROVIDER, "ast", None),
        (ExpansionKind.OMISSION, omission_id, None),
        (ExpansionKind.HISTORY, "entity:symbol-after", None),
        (ExpansionKind.VALIDATION, None, None),
        (ExpansionKind.REFRESH, None, None),
    ]
    results = [
        engine.expand(request, base, kind, target_id=target_id, role=role, context_lines=2)
        for kind, target_id, role in cases
    ]

    assert all(item.base_dossier_id == base.dossier_id for item in results)
    adjacent = results[5]
    symbol = next(item for item in adjacent.items if item.record_id == "entity:symbol-after")
    block = next(item for item in adjacent.content if item.content_id == symbol.content_id)
    assert "# changed" in (block.text or "")
    assert results[-1].status is DossierStatus.PARTIAL

    page_request = build_dossier_request(
        profile=request.profile,
        question=request.question,
        session_id=request.session_id,
        session_manifest_digest=request.session_manifest_digest,
        targets=request.targets,
        budget=DossierBudget(max_items=1),
    )
    page = engine.query(page_request)
    continued = engine.expand(page_request, page, ExpansionKind.CURSOR)
    assert continued.base_dossier_id == page.dossier_id
    assert {item.item_id for item in page.items}.isdisjoint(item.item_id for item in continued.items)


def test_omission_taxonomy_keeps_exclusions_explicit_and_actionable() -> None:
    base = _context_with_sources()
    payload = base.evidence[0].model_dump(mode="json")
    payload["omissions"].append(
        {
            "record_type": "omission",
            "omission_id": "omission:failure",
            "source_state_id": "state:after",
            "provider_run_id": "provider:semantic",
            "reason": "provider failed while reading target",
            "scope_type": "entity",
            "scope_id": "entity:symbol-after",
            "recoverable": True,
            "remediation": "Retry the separately authorized provider.",
        }
    )
    evidence = RepositoryEvidence.model_validate(payload)
    context = DossierContext.from_evidence(
        session_id=base.session_id,
        session_manifest_digest=base.session_manifest_digest,
        repository_id=base.repository_id,
        source_state_ids=list(base.source_state_ids),
        provider_run_ids=list(base.provider_run_ids),
        policy_digest=base.policy_digest,
        evidence=[evidence],
        sources=list(base.sources),
        session_omissions=[
            build_omission(
                OmissionScope.PROVIDER,
                "partial_semantics",
                "A provider supplied only partial semantics.",
                provider_id="semantic",
            )
        ],
    )
    engine = DossierEngine(context)
    symbol = TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")
    results = [
        engine.query(
            build_dossier_request(
                profile=DossierProfile.IMPLEMENTATION,
                question="Inspect omission semantics.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=[symbol],
            )
        ),
        engine.query(
            build_dossier_request(
                profile=DossierProfile.IMPLEMENTATION,
                question="Force a scope exclusion.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=[symbol],
                filters=DossierFilters(roles=[EvidenceRole.TEST]),
            )
        ),
        engine.query(
            build_dossier_request(
                profile=DossierProfile.IMPLEMENTATION,
                question="Force a payload omission.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=[symbol],
                budget=DossierBudget(max_items=1),
            )
        ),
        engine.query(
            build_dossier_request(
                profile=DossierProfile.IMPLEMENTATION,
                question="Respect generated-content policy.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=[TargetSelector(kind=TargetKind.FILE, identity="entity:file-generated")],
            )
        ),
        engine.query(
            build_dossier_request(
                profile=DossierProfile.LOCALISATION,
                question="Do not guess between source states.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=[
                    TargetSelector(
                        kind=TargetKind.SYMBOL,
                        locator=DossierLocator(qualified_name="pkg.core.answer"),
                    )
                ],
            )
        ),
    ]
    reasons = {omission.reason for result in results for omission in result.omissions}

    assert reasons >= {
        OmissionReason.SCOPE,
        OmissionReason.UNSUPPORTED,
        OmissionReason.POLICY,
        OmissionReason.BUDGET,
        OmissionReason.PROVIDER_LIMITATION,
        OmissionReason.AMBIGUITY,
        OmissionReason.FAILURE,
    }
    recoverable = [
        omission
        for result in results
        for omission in result.omissions
        if omission.reason in {OmissionReason.BUDGET, OmissionReason.FAILURE}
    ]
    assert recoverable and all(item.recovery_action_ids for item in recoverable)


def test_ambiguity_stale_cursor_unknown_target_and_impossible_budget_fail_explicitly() -> None:
    context = _context()
    engine = DossierEngine(context)
    ambiguous = engine.query(
        build_dossier_request(
            profile=DossierProfile.LOCALISATION,
            question="Locate answer without choosing a source state.",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=[TargetSelector(kind=TargetKind.SYMBOL, locator=DossierLocator(qualified_name="pkg.core.answer"))],
        )
    )
    assert ambiguous.status is DossierStatus.PARTIAL
    assert ambiguous.boundary.targets[0].status is TargetResolutionStatus.AMBIGUOUS
    assert {item.record_id for item in ambiguous.items} >= {"entity:symbol-before", "entity:symbol-after"}

    unknown = engine.query(
        build_dossier_request(
            profile=DossierProfile.LOCALISATION,
            question="Locate a missing symbol.",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=[TargetSelector(kind=TargetKind.SYMBOL, identity="entity:missing")],
        )
    )
    assert unknown.status is DossierStatus.BLOCKED
    assert unknown.boundary.targets[0].status is TargetResolutionStatus.UNRESOLVED

    page_request = build_dossier_request(
        profile=DossierProfile.IMPLEMENTATION,
        question="Page implementation evidence.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")],
        budget=DossierBudget(max_items=1),
    )
    page = engine.query(page_request)
    action = next(item for item in page.expansions if item.kind is ExpansionKind.CURSOR)
    assert action.cursor is not None
    stale_cursor = build_dossier_cursor(
        base_dossier_id=action.cursor.base_dossier_id,
        base_dossier_digest=action.cursor.base_dossier_digest,
        base_request_id=action.cursor.base_request_id,
        query_digest=action.cursor.query_digest,
        session_id=action.cursor.session_id,
        session_manifest_digest=_digest("stale"),
        policy_digest=action.cursor.policy_digest,
        expansion_kind=action.cursor.expansion_kind,
        ordered_after=action.cursor.ordered_after,
    )
    stale = engine.query(
        build_dossier_request(
            profile=page_request.profile,
            question=page_request.question,
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=page_request.targets,
            budget=page_request.budget,
            expansion=DossierExpansion(
                kind=ExpansionKind.CURSOR,
                base_dossier_id=page.dossier_id,
                action_id=action.action_id,
                cursor=stale_cursor,
            ),
        )
    )
    assert stale.status is DossierStatus.BLOCKED
    assert stale.omissions[0].reason.value == "source_drift"
    assert stale.expansions[0].kind is ExpansionKind.REFRESH

    with pytest.raises(DossierQueryError, match="envelope"):
        engine.query(
            build_dossier_request(
                profile=DossierProfile.LOCALISATION,
                question="Use an impossible response envelope.",
                session_id=context.session_id,
                session_manifest_digest=context.session_manifest_digest,
                targets=[TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")],
                budget=DossierBudget(max_items=1, max_payload_bytes=4096),
            )
        )


def test_bounded_io_schemas_and_canonical_payload_accounting() -> None:
    context = _context()
    request = build_dossier_request(
        profile=DossierProfile.LOCALISATION,
        question="Locate exact answer evidence.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")],
    )
    dossier = DossierEngine(context).query(request)
    request_raw = canonical_ordered_json_bytes(request.model_dump(mode="json"))
    dossier_raw = canonical_dossier_bytes(dossier)

    assert parse_dossier_request(request_raw) == request
    assert parse_dossier(dossier_raw) == dossier
    assert dossier.budget_use.payload_bytes.used == len(dossier_raw)
    assert dossier_request_json_schema()["title"] == "DossierRequest"
    assert dossier_json_schema()["title"] == "Dossier"
    with pytest.raises(DossierArtifactError, match="schema version"):
        parse_dossier_request(request_raw.replace(b'"1.0.0"', b'"2.0.0"', 1))
    with pytest.raises(ValueError, match="base64url"):
        decode_dossier_cursor("not valid !!!")


@pytest.mark.parametrize(
    ("fixture_name", "parser"),
    [
        ("request-legacy-v0.json", parse_dossier_request),
        ("request-future-v2.json", parse_dossier_request),
        ("dossier-legacy-v0.json", parse_dossier),
        ("dossier-future-v2.json", parse_dossier),
    ],
)
def test_legacy_and_future_dossier_artifacts_fail_closed(
    fixture_name: str,
    parser: Callable[[bytes], object],
) -> None:
    raw = (FIXTURE_ROOT / fixture_name).read_bytes()

    with pytest.raises(DossierArtifactError, match="schema version"):
        parser(raw)


def test_context_loads_normalized_evidence_from_a_valid_session_bundle() -> None:
    evidence = _after_evidence()
    state = build_state_manifest(
        evidence,
        source_state_id="state:after",
        configuration_digest=_digest("configuration"),
        history_status=HistoryStatus.COMPLETE,
        baseline_available=False,
    )
    evidence_artifact, evidence_blob = build_session_artifact(
        role=ArtifactRole.NORMALIZED_EVIDENCE,
        content=canonical_evidence_bytes(evidence),
        media_type="application/vnd.anatomize.evidence+json",
        portable_path="normalized/evidence.json",
        source_state_ids=["state:after"],
    )
    report_artifact, report_blob = build_session_artifact(
        role=ArtifactRole.REPORT,
        content=b"# Report\n",
        media_type="text/markdown",
        portable_path="reports/review.md",
        source_state_ids=["state:after"],
        derived_from_ids=[evidence_artifact.artifact_id],
    )
    slice_artifact, slice_blob = build_session_artifact(
        role=ArtifactRole.SOURCE_SLICE,
        content=b"def answer(): ...\n",
        media_type="text/x-python",
        portable_path="slices/core.py",
        source_state_ids=["state:after"],
        derived_from_ids=[report_artifact.artifact_id],
    )
    manifest = build_session_manifest(
        repository_id=evidence.repository_id,
        status=SessionStatus.COMPLETE,
        configuration_digest=_digest("configuration"),
        policy_digest=_digest("policy"),
        source_states=[state],
        schemas=[
            SchemaBinding(
                artifact_type="anatomize.session", schema_version="1.0.0", schema_digest=_digest("session-schema")
            ),
            SchemaBinding(
                artifact_type="anatomize.evidence", schema_version="1.0.0", schema_digest=_digest("evidence-schema")
            ),
            SchemaBinding(
                artifact_type="anatomize.state-manifest", schema_version="1.0.0", schema_digest=_digest("state-schema")
            ),
        ],
        providers=[
            SessionProviderBinding(
                provider_run_id=item.provider_run_id,
                provider_id=item.provider_id,
                provider_version=item.provider_version,
                configuration_digest=item.configuration_digest,
                status=item.status,
            )
            for item in evidence.provider_runs
        ],
        query=build_query("dossier-known-truth"),
        budgets=[BudgetBinding(name="items", unit="items", limit=128, used=0)],
        artifacts=[evidence_artifact, report_artifact, slice_artifact],
        omissions=[
            build_omission(
                OmissionScope.PROVIDER,
                "partial_semantics",
                "The semantic provider is partial.",
                provider_id="semantic",
            )
        ],
    )
    bundle = build_session_bundle(manifest, [evidence_blob, report_blob, slice_blob])

    context = DossierContext.from_bundle(bundle)
    assert context.repository_id == evidence.repository_id
    assert context.source_state_ids == ("state:after",)
    assert canonical_evidence_bytes(context.evidence[0]) == canonical_evidence_bytes(evidence)


def test_public_request_and_result_match_golden_fixture() -> None:
    fixture = json.loads((FIXTURE_ROOT / "dossier-known-truth.json").read_text(encoding="utf-8"))
    request = build_dossier_request(
        profile=DossierProfile.CHANGE_REVIEW,
        question="Bind the exact baseline revision.",
        session_id=_context().session_id,
        session_manifest_digest=_context().session_manifest_digest,
        targets=[TargetSelector(kind=TargetKind.REVISION, identity="state:before")],
        budget=DossierBudget(
            max_items=8,
            max_payload_bytes=49_152,
            max_depth=0,
            max_disclosed_omission_ids=32,
        ),
    )
    dossier = DossierEngine(_context()).query(request)

    assert request.model_dump(mode="json") == fixture["request"]
    assert dossier.model_dump(mode="json") == fixture["dossier"]


def _after_evidence() -> RepositoryEvidence:
    payload = _known_truth_evidence().model_dump(mode="json")
    payload["states"] = [item for item in payload["states"] if item["state_id"] == "state:after"]
    for key in (
        "locations",
        "entities",
        "edges",
        "contracts",
        "candidates",
        "observations",
        "completeness",
        "omissions",
        "conflicts",
        "aliases",
    ):
        payload[key] = [item for item in payload[key] if item["source_state_id"] == "state:after"]
    payload["lineage"] = []
    return RepositoryEvidence.model_validate(payload)
