from __future__ import annotations

from dataclasses import dataclass

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.dossiers import (
    DossierBudget,
    DossierContext,
    DossierEngine,
    DossierLocator,
    DossierProfile,
    DossierStatus,
    EvidenceRole,
    OmissionReason,
    SourceSlicePolicy,
    TargetKind,
    TargetResolutionStatus,
    TargetSelector,
    build_dossier_request,
    build_dossier_source,
    canonical_dossier_bytes,
)
from anatomize.sessions import OmissionScope, build_omission
from tests.unit.test_dossier_engine import _context_with_sources, _profile_context


@dataclass(frozen=True)
class _HeldOutTask:
    prompt: str
    profile: DossierProfile
    required_roles: frozenset[EvidenceRole]


_TASKS = (
    _HeldOutTask(
        "Find the implementation boundary and everything that must be inspected before changing answer().",
        DossierProfile.IMPLEMENTATION,
        frozenset(
            {
                EvidenceRole.DEFINITION,
                EvidenceRole.CALLER,
                EvidenceRole.CALLEE,
                EvidenceRole.CONSUMER,
                EvidenceRole.CONTRACT,
                EvidenceRole.TEST,
                EvidenceRole.DOCUMENTATION,
                EvidenceRole.CONFIGURATION,
                EvidenceRole.RUNTIME,
                EvidenceRole.CHANGE,
            }
        ),
    ),
    _HeldOutTask(
        "Review the answer() change, including state, downstream evidence, risks, and verification gaps.",
        DossierProfile.CHANGE_REVIEW,
        frozenset(
            {
                EvidenceRole.STATE,
                EvidenceRole.CHANGE,
                EvidenceRole.CONSUMER,
                EvidenceRole.TEST,
                EvidenceRole.DOCUMENTATION,
                EvidenceRole.CONFIGURATION,
                EvidenceRole.WORKFLOW,
                EvidenceRole.ARTIFACT,
                EvidenceRole.DIAGNOSTIC,
                EvidenceRole.CONFLICT,
            }
        ),
    ),
    _HeldOutTask(
        "Audit answer() architecture without inventing a quality or centrality score.",
        DossierProfile.AUDIT,
        frozenset(
            {
                EvidenceRole.CONTRACT,
                EvidenceRole.DEPENDENCY,
                EvidenceRole.CONSUMER,
                EvidenceRole.DIAGNOSTIC,
                EvidenceRole.CONFLICT,
            }
        ),
    ),
)


@pytest.mark.integration
@pytest.mark.parametrize("task", _TASKS, ids=lambda item: item.profile.value)
def test_context_isolated_tasks_retain_required_evidence_with_public_explanations(task: _HeldOutTask) -> None:
    context = _profile_context()
    request = build_dossier_request(
        profile=task.profile,
        question=task.prompt,
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")],
        slice_policy=SourceSlicePolicy(context_before=0, context_after=0),
    )
    dossier = DossierEngine(context).query(request)
    repeated = DossierEngine(context).query(request)

    assert dossier.status is DossierStatus.COMPLETE
    assert dossier.boundary.targets[0].status is TargetResolutionStatus.EXACT
    assert {item.role for item in dossier.items} >= task.required_roles
    assert all(item.selection_reasons for item in dossier.items)
    assert all(item.source_state_id in context.source_state_ids for item in dossier.content)
    assert canonical_dossier_bytes(dossier) == canonical_dossier_bytes(repeated)
    source = next(
        item for item in context.sources if item.source_state_id == "state:after" and item.path == "src/pkg/core.py"
    )
    assert source.text is not None
    assert dossier.budget_use.inline_content_bytes.used < len(source.text.encode("utf-8"))


@pytest.mark.integration
def test_adversarial_dossier_cases_fail_visible_without_implicit_ranking_or_source_access() -> None:
    base = _context_with_sources()
    large_text = "def answer():\n" + "    value += 1\n" * 4_000
    large_source = build_dossier_source(
        source_state_id="state:after",
        path="src/pkg/core.py",
        media_type="text/x-python",
        language="python",
        text=large_text,
    )
    context = DossierContext.from_evidence(
        session_id=base.session_id,
        session_manifest_digest=base.session_manifest_digest,
        repository_id=base.repository_id,
        source_state_ids=list(base.source_state_ids),
        provider_run_ids=list(base.provider_run_ids),
        policy_digest=base.policy_digest,
        evidence=list(base.evidence),
        sources=[
            item
            for item in base.sources
            if item.path != "src/pkg/core.py" or item.source_state_id != "state:after"
        ]
        + [large_source],
        session_omissions=[
            build_omission(
                OmissionScope.PROVIDER,
                "provider_unavailable",
                "The optional runtime provider was unavailable.",
                provider_id="runtime",
            )
        ],
    )
    engine = DossierEngine(context)
    target = TargetSelector(kind=TargetKind.SYMBOL, identity="entity:symbol-after")
    large = engine.query(
        build_dossier_request(
            profile=DossierProfile.IMPLEMENTATION,
            question="Inspect a target in a very large file.",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=[target],
            budget=DossierBudget(max_content_block_bytes=64, max_inline_content_bytes=64),
        )
    )
    ambiguous = engine.query(
        build_dossier_request(
            profile=DossierProfile.LOCALISATION,
            question="Resolve a duplicate qualified name without guessing.",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=[
                TargetSelector(
                    kind=TargetKind.SYMBOL,
                    locator=DossierLocator(qualified_name="pkg.core.answer"),
                )
            ],
        )
    )
    stale = engine.query(
        build_dossier_request(
            profile=DossierProfile.IMPLEMENTATION,
            question="Reject a stale source binding.",
            session_id=context.session_id,
            session_manifest_digest=sha256_digest(b"stale"),
            targets=[target],
        )
    )
    budgeted = engine.query(
        build_dossier_request(
            profile=DossierProfile.IMPLEMENTATION,
            question="Disclose deterministic budget exhaustion.",
            session_id=context.session_id,
            session_manifest_digest=context.session_manifest_digest,
            targets=[target],
            budget=DossierBudget(max_items=1),
        )
    )

    assert any(item.truncated for item in large.content)
    assert {item.reason for item in large.omissions} >= {
        OmissionReason.BUDGET,
        OmissionReason.PROVIDER_LIMITATION,
        OmissionReason.UNSUPPORTED,
    }
    assert ambiguous.boundary.targets[0].status is TargetResolutionStatus.AMBIGUOUS
    assert stale.status is DossierStatus.BLOCKED
    assert any(item.reason is OmissionReason.SOURCE_DRIFT for item in stale.omissions)
    assert budgeted.status is DossierStatus.PARTIAL
    assert all("score" not in item.model_dump(mode="json") for item in large.items)
    assert any(item.content_class.value == "generated" for item in context.sources)
