"""One public application layer for repository review and lifecycle evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, JsonValue

from anatomize._artifacts import canonical_json_bytes, sha256_digest
from anatomize.dossiers import (
    DossierBudget,
    DossierContext,
    DossierEngine,
    DossierFilters,
    DossierLocator,
    DossierProfile,
    EvidenceRole,
    QueryDirection,
    SourceSlicePolicy,
    TargetKind,
    TargetSelector,
    build_dossier_request,
    canonical_dossier_bytes,
    expand_dossier_request,
    session_manifest_digest,
)
from anatomize.evidence import (
    EVIDENCE_ARTIFACT_TYPE,
    EVIDENCE_SCHEMA_VERSION,
    ArtifactEntity,
    CandidateKind,
    CandidateRecord,
    ConfigurationEntity,
    DataEntity,
    DiagnosticEntity,
    DocumentationEntity,
    FileEntity,
    ProviderRunStatus,
    RepositoryEvidence,
    RuntimeEntity,
    RuntimeObservation,
    SymbolEntity,
    TestEntity,
    WorkflowEntity,
    canonical_evidence_bytes,
    merge_repository_evidence,
)
from anatomize.index import RepositoryIndex, build_repository_index
from anatomize.lifecycle import (
    DECISION_OVERLAY_SCHEMA_VERSION,
    CandidateGranularity,
    CandidateRegion,
    ChangeBoundaryKind,
    ChangeDimension,
    ChangeDossier,
    ClosureObservation,
    ClosureReport,
    ConsolidationDossier,
    ConsolidationQuestion,
    DecisionDisposition,
    DecisionOverlay,
    ImplementationIntent,
    ImplementationObligation,
    OverlayEvaluation,
    ReviewEvidenceGroup,
    SimilarityArtifact,
    SimilarityCandidate,
    SimilarityMethod,
    SimilarityQuery,
    build_change_dossier,
    build_change_evidence,
    build_consolidation_dossier,
    build_decision_overlay,
    build_implementation_intent,
    build_similarity_candidate,
    candidate_evidence_digest,
    evaluate_decision_overlay,
    select_similarity_candidates,
    verify_implementation_closure,
)
from anatomize.providers import (
    InvocationMode,
    ProviderEnvelope,
    provider_envelope_evidence,
    repository_index_provider_envelope,
)
from anatomize.review.imports import (
    ProviderArtifactInput,
    normalize_builtin_source_facts,
    normalize_provider_artifact,
)
from anatomize.review.io import artifact_identity, load_review_artifact, parse_review_artifact
from anatomize.review.models import ArtifactCheck, DossierExchange, ReviewApplicationError, build_dossier_exchange
from anatomize.sessions import (
    SESSION_ARTIFACT_TYPE,
    SESSION_SCHEMA_VERSION,
    ArtifactRole,
    BudgetBinding,
    OmissionScope,
    ReviewSessionBundle,
    ReviewSessionManifest,
    SchemaBinding,
    SessionProviderBinding,
    SessionStatus,
    build_omission,
    build_query,
    build_session_artifact,
    build_session_bundle,
    build_session_manifest,
)
from anatomize.temporal import (
    STATE_MANIFEST_ARTIFACT_TYPE,
    STATE_MANIFEST_SCHEMA_VERSION,
    DeltaFamily,
    DeltaStatus,
    HistoryStatus,
    RepositoryComparison,
    StateManifest,
    build_state_manifest,
    compare_repository_evidence,
)

_REVIEW_CONFIGURATION_DIGEST = sha256_digest(b"anatomize.review.application:1.0.0")
_REVIEW_POLICY_DIGEST = sha256_digest(
    b"baseline-content-free;providers-explicit-artifact-import;consumer-owned-judgement"
)
_DEFAULT_QUESTIONS = {
    DossierProfile.ORIENTATION: (
        "What structure, entry points, public surfaces, and limitations orient this repository?"
    ),
    DossierProfile.DESIGN: "What evidence and alternatives constrain this design decision?",
    DossierProfile.AUDIT: "What architecture, dependency, diagnostic, and uncertainty evidence bears on this audit?",
    DossierProfile.LOCALISATION: "Where is this target defined, used, tested, documented, and configured?",
    DossierProfile.IMPLEMENTATION: "What must be understood and preserved to implement this change safely?",
    DossierProfile.CHANGE_REVIEW: "What changed, what is affected, and what remains unknown?",
    DossierProfile.CLOSURE: "Does after-state evidence satisfy every declared implementation obligation?",
}


class ReviewApplication:
    """Stateless façade reused without semantic reconstruction by every interface."""

    def capabilities(self) -> dict[str, Any]:
        """Advertise the exact application operations and interaction contract."""
        return review_capabilities()

    def start(
        self,
        root: Path,
        *,
        repository_id: str | None = None,
        provider_envelopes: Iterable[ProviderEnvelope] = (),
        provider_artifacts: Iterable[ProviderArtifactInput] = (),
        source_paths: Iterable[str] = (),
    ) -> ReviewSessionBundle:
        """Build one deterministic, portable session; never discover or invoke optional providers."""
        resolved = root.resolve()
        index = build_repository_index(resolved)
        selected_repository_id = repository_id or f"repository:{index.root_name}"
        baseline = repository_index_provider_envelope(
            index,
            repository_id=selected_repository_id,
            policy_digest=_REVIEW_POLICY_DIGEST,
        )
        baseline_evidence = provider_envelope_evidence(baseline)
        source_inventory = normalize_builtin_source_facts(
            baseline=baseline_evidence,
            root=resolved,
        )
        normalized_imports = [
            envelope
            for artifact in provider_artifacts
            for envelope in normalize_provider_artifact(
                artifact,
                baseline=baseline_evidence,
                root=resolved,
            )
        ]
        imported = [*normalized_imports, *provider_envelopes]
        _validate_provider_inputs([baseline, *imported], baseline)
        envelopes = [baseline, *([source_inventory] if source_inventory is not None else []), *imported]
        evidence_parts = [provider_envelope_evidence(item) for item in envelopes]
        evidence = merge_repository_evidence(evidence_parts)
        state_id = baseline.primary_source_state_id
        state = build_state_manifest(
            evidence,
            source_state_id=state_id,
            configuration_digest=_REVIEW_CONFIGURATION_DIGEST,
            history_status=(
                HistoryStatus.COMPLETE if baseline.source_states[0].revision is not None else HistoryStatus.UNAVAILABLE
            ),
            baseline_available=False,
        )
        evidence_bytes = canonical_evidence_bytes(evidence)

        artifacts = []
        blobs = []
        evidence_artifact, evidence_blob = build_session_artifact(
            role=ArtifactRole.NORMALIZED_EVIDENCE,
            content=evidence_bytes,
            media_type="application/vnd.anatomize.evidence+json",
            portable_path="evidence/repository.json",
            source_state_ids=[state_id],
        )
        artifacts.append(evidence_artifact)
        blobs.append(evidence_blob)

        selected_sources = _selected_source_paths(index, source_paths)
        for path in selected_sources:
            source_content = (resolved / path).read_bytes()
            source_artifact, source_blob = build_session_artifact(
                role=ArtifactRole.SOURCE_SLICE,
                content=source_content,
                media_type=_source_media_type(path),
                portable_path=f"sources/{state_id}/{path}",
                source_state_ids=[state_id],
                derived_from_ids=[evidence_artifact.artifact_id],
            )
            artifacts.append(source_artifact)
            blobs.append(source_blob)

        providers = [
            SessionProviderBinding(
                provider_run_id=run.provider_run_id,
                provider_id=run.provider_id,
                provider_version=run.provider_version,
                configuration_digest=run.configuration_digest,
                status=run.status,
            )
            for run in evidence.provider_runs
        ]
        omissions = [
            build_omission(
                OmissionScope.REPORT,
                "report_on_demand",
                "Review reports are deterministic projections exported on demand.",
            )
        ]
        if not selected_sources:
            omissions.append(
                build_omission(
                    OmissionScope.SLICE,
                    "content_free_default",
                    "Repository content is not embedded unless exact source paths are explicitly requested.",
                )
            )
        for run in evidence.provider_runs:
            if run.status is not ProviderRunStatus.COMPLETE:
                omissions.append(
                    build_omission(
                        OmissionScope.PROVIDER,
                        "provider_incomplete",
                        f"Provider {run.provider_id} reported {run.status.value} coverage.",
                        provider_id=run.provider_id,
                    )
                )

        manifest = build_session_manifest(
            repository_id=selected_repository_id,
            status=SessionStatus.COMPLETE,
            configuration_digest=_REVIEW_CONFIGURATION_DIGEST,
            policy_digest=_REVIEW_POLICY_DIGEST,
            source_states=[state],
            schemas=_schema_bindings(),
            providers=providers,
            query=build_query(
                "review.start",
                cast(
                    dict[str, JsonValue],
                    {
                    "provider_run_ids": sorted(item.provider_run_id for item in evidence.provider_runs),
                    "source_paths": selected_sources,
                    },
                ),
            ),
            budgets=[
                BudgetBinding(
                    name="portable_session_bytes",
                    unit="bytes",
                    limit=128 * 1024 * 1024,
                    used=sum(len(item.decoded()) for item in blobs),
                )
            ],
            artifacts=artifacts,
            omissions=omissions,
        )
        return build_session_bundle(manifest, blobs)

    def dossier(
        self,
        bundle: ReviewSessionBundle,
        *,
        profile: DossierProfile,
        targets: Iterable[TargetSelector] = (),
        question: str | None = None,
        direction: QueryDirection = QueryDirection.BOTH,
        filters: DossierFilters | None = None,
        include: Iterable[str] = (),
        exclude: Iterable[str] = (),
        budget: DossierBudget | None = None,
        slice_policy: SourceSlicePolicy | None = None,
    ) -> DossierExchange:
        """Answer one source-bound lifecycle question from the immutable session."""
        context = DossierContext.from_bundle(bundle)
        selected_targets = list(targets)
        if not selected_targets and profile in {
            DossierProfile.DESIGN,
            DossierProfile.AUDIT,
            DossierProfile.CHANGE_REVIEW,
            DossierProfile.CLOSURE,
        }:
            selected_targets = [
                TargetSelector(kind=TargetKind.REPOSITORY, identity=bundle.manifest.repository_id)
            ]
        if not selected_targets and profile is not DossierProfile.ORIENTATION:
            raise ReviewApplicationError(
                "dossier_target_required",
                f"The {profile.value} profile requires an explicit target",
                remediation="Supply a typed file, symbol, test, documentation, or other exact target.",
            )
        request = build_dossier_request(
            profile=profile,
            question=question or _DEFAULT_QUESTIONS[profile],
            session_id=bundle.manifest.session_id,
            session_manifest_digest=session_manifest_digest(bundle.manifest),
            targets=selected_targets,
            direction=direction,
            filters=filters,
            include=list(include),
            exclude=list(exclude),
            budget=budget,
            slice_policy=slice_policy,
        )
        return build_dossier_exchange(request, DossierEngine(context).query(request))

    def expand(
        self,
        bundle: ReviewSessionBundle,
        base: DossierExchange,
        *,
        action_id: str,
        budget: DossierBudget | None = None,
        slice_policy: SourceSlicePolicy | None = None,
    ) -> DossierExchange:
        """Apply exactly one action advertised by a base dossier."""
        context = DossierContext.from_bundle(bundle)
        engine = DossierEngine(context)
        registered = engine.query(base.request)
        if registered != base.dossier:
            raise ReviewApplicationError(
                "dossier_base_mismatch",
                "The supplied base dossier is not the deterministic answer for this session",
                remediation="Use the exact session that produced the dossier exchange.",
            )
        action = next((item for item in base.dossier.expansions if item.action_id == action_id), None)
        if action is None:
            raise ReviewApplicationError(
                "dossier_action_unknown",
                f"The base dossier did not advertise action {action_id!r}",
                remediation="Select an action_id from the dossier expansions list.",
            )
        request = expand_dossier_request(
            base.request,
            base.dossier,
            action,
            budget=budget,
            slice_policy=slice_policy,
        )
        return build_dossier_exchange(request, engine.query(request))

    def change(
        self,
        before: ReviewSessionBundle,
        after: ReviewSessionBundle,
    ) -> ChangeDossier:
        """Compare exact normalized indexes and bind differences to review dossiers."""
        _require_same_repository(before, after)
        before_state = _single_state(before)
        after_state = _single_state(after)
        if before_state.source_state.state_id == after_state.source_state.state_id:
            raise ReviewApplicationError(
                "change_states_identical",
                "Two-state change review requires distinct source states",
                remediation="Rebuild the after session after repository content changes.",
            )
        target = TargetSelector(kind=TargetKind.REPOSITORY, identity=before.manifest.repository_id)
        before_exchange = self.dossier(
            before,
            profile=DossierProfile.CHANGE_REVIEW,
            targets=[target],
        )
        after_exchange = self.dossier(
            after,
            profile=DossierProfile.CHANGE_REVIEW,
            targets=[target],
        )
        comparison = compare_repository_evidence(
            _session_evidence(before),
            _session_evidence(after),
            configuration_digest=_REVIEW_CONFIGURATION_DIGEST,
            history_status=(
                HistoryStatus.COMPLETE
                if before_state.source_state.revision is not None and after_state.source_state.revision is not None
                else HistoryStatus.UNAVAILABLE
            ),
        )
        changes = _comparison_changes(comparison)
        before_provider_digests = _provider_digests(before)
        after_provider_digests = _provider_digests(after)
        if before_provider_digests.keys() != after_provider_digests.keys():
            changes.append(
                build_change_evidence(
                    dimension=ChangeDimension.CONFIGURATION,
                    kinds=[ChangeBoundaryKind.PROVIDER_MISMATCH],
                    predecessor_refs=[f"provider:{item}" for item in sorted(before_provider_digests)],
                    successor_refs=[f"provider:{item}" for item in sorted(after_provider_digests)],
                    evidence_refs=[],
                    lineage="exact",
                    rationale="The exact provider set differs between comparison sides.",
                )
            )
        affected = sorted(
            {
                ref
                for change in changes
                for ref in [*change.predecessor_refs, *change.successor_refs]
            }
        )
        blast_radius = sorted(
            {
                relationship_id
                for item in after_exchange.dossier.items
                for relationship_id in item.relationship_ids
            }
        )
        limitations = sorted(
            set(
                [
                    "Change detection compares every supported canonical evidence family using stable domain "
                    "keys and exact file-content moves; speculative renames, splits, and merges remain explicit "
                    "limitations.",
                    *before_exchange.dossier.limitations,
                    *after_exchange.dossier.limitations,
                ]
            )
        )
        return build_change_dossier(
            repository_id=before.manifest.repository_id,
            before_source_state_id=before_state.source_state.state_id,
            after_source_state_id=after_state.source_state.state_id,
            before_dossier_id=before_exchange.dossier.dossier_id,
            after_dossier_id=after_exchange.dossier.dossier_id,
            before_evidence_digest=before_state.evidence_artifact_digest,
            after_evidence_digest=after_state.evidence_artifact_digest,
            before_provider_digests=before_provider_digests,
            after_provider_digests=after_provider_digests,
            changes=changes,
            affected_evidence_refs=affected,
            blast_radius_refs=blast_radius,
            limitations=limitations,
        )

    def similarity(
        self,
        bundle: ReviewSessionBundle,
        *,
        query: SimilarityQuery | None = None,
    ) -> SimilarityArtifact:
        """Project baseline duplicate candidates from an exact portable session."""
        selected_query = query or SimilarityQuery()
        state = _single_state(bundle)
        evidence = _session_evidence(bundle)
        baseline = next(
            (item for item in bundle.manifest.providers if item.provider_id == "anatomize.repository-index"),
            None,
        )
        if baseline is None:
            raise ReviewApplicationError(
                "similarity_baseline_missing",
                "Session has no baseline repository-index provider",
                remediation="Rebuild the session with review start.",
            )
        candidates = []
        for record in evidence.candidates:
            if record.kind is CandidateKind.DUPLICATION:
                candidates.append(_similarity_candidate(record, evidence, baseline))
        return SimilarityArtifact(
            repository_id=bundle.manifest.repository_id,
            source_state_id=state.source_state.state_id,
            query=selected_query,
            candidates=select_similarity_candidates(candidates, selected_query),
            limitations=[
                "Candidates are evidence for qualitative review, never automatic merge or deletion instructions.",
                *(item.summary for item in evidence.limitations),
            ],
        )

    def consolidation(
        self,
        base: DossierExchange,
        candidate: SimilarityCandidate,
    ) -> ConsolidationDossier:
        """Project one candidate and one task dossier into the complete review-question surface."""
        if candidate.source_state_id not in base.dossier.source_state_ids:
            raise ReviewApplicationError(
                "candidate_state_mismatch",
                "Similarity candidate belongs to a state outside the base dossier",
                remediation="Rebuild similarity and dossier evidence from the same source state.",
            )
        refs_by_role: dict[EvidenceRole, list[str]] = {}
        for item in base.dossier.items:
            refs_by_role.setdefault(item.role, []).append(item.record_id)
        groups = [
            _consolidation_group(question, candidate, refs_by_role, base)
            for question in ConsolidationQuestion
        ]
        return build_consolidation_dossier(
            repository_id=base.dossier.repository_id,
            base_dossier_id=base.dossier.dossier_id,
            base_dossier_digest=sha256_digest(canonical_dossier_bytes(base.dossier)),
            candidate=candidate,
            groups=groups,
        )

    def decision_overlay(
        self,
        bundle: ReviewSessionBundle,
        candidate: SimilarityCandidate,
        *,
        owner_namespace: str,
        disposition: DecisionDisposition,
        rationale: str,
        preserved_divergence: Iterable[str] = (),
        review_conditions: Iterable[str] = (),
    ) -> DecisionOverlay:
        """Create a consumer-owned decision over exact current evidence."""
        _require_candidate_state(bundle, candidate)
        return build_decision_overlay(
            candidate=candidate,
            relevant_source_digest=_candidate_source_digest(candidate),
            provider_digests=_candidate_provider_digests(candidate),
            evidence_digests={"candidate": candidate_evidence_digest(candidate)},
            decision_schema_version=DECISION_OVERLAY_SCHEMA_VERSION,
            decision_policy_digest=_REVIEW_POLICY_DIGEST,
            owner_namespace=owner_namespace,
            disposition=disposition,
            rationale=rationale,
            preserved_divergence=list(preserved_divergence),
            review_conditions=list(review_conditions),
        )

    def evaluate_overlay(
        self,
        overlay: DecisionOverlay,
        bundle: ReviewSessionBundle,
        candidate: SimilarityCandidate,
    ) -> OverlayEvaluation:
        """Explain whether a consumer decision is current for exact relevant inputs."""
        _require_candidate_state(bundle, candidate)
        return evaluate_decision_overlay(
            overlay,
            candidate=candidate,
            relevant_source_digest=_candidate_source_digest(candidate),
            provider_digests=_candidate_provider_digests(candidate),
            evidence_digests={"candidate": candidate_evidence_digest(candidate)},
            decision_schema_version=DECISION_OVERLAY_SCHEMA_VERSION,
            decision_policy_digest=_REVIEW_POLICY_DIGEST,
        )

    def implementation_intent(
        self,
        bundle: ReviewSessionBundle,
        base: DossierExchange,
        *,
        obligations: Iterable[ImplementationObligation],
        decision_overlay_id: str | None = None,
        declared_unknowns: Iterable[str] = (),
    ) -> ImplementationIntent:
        """Bind consumer-declared obligations to the exact before state and dossier."""
        if base.dossier.session_id != bundle.manifest.session_id:
            raise ReviewApplicationError(
                "intent_session_mismatch",
                "Implementation dossier belongs to another session",
                remediation="Use the session that produced the base dossier exchange.",
            )
        state = _single_state(bundle)
        return build_implementation_intent(
            repository_id=bundle.manifest.repository_id,
            before_source_state_id=state.source_state.state_id,
            before_dossier_id=base.dossier.dossier_id,
            before_evidence_digest=state.evidence_artifact_digest,
            before_provider_digests=_provider_digests(bundle),
            decision_overlay_id=decision_overlay_id,
            obligations=list(obligations),
            declared_unknowns=sorted(set(declared_unknowns)),
        )

    def verify(
        self,
        intent: ImplementationIntent,
        after: ReviewSessionBundle,
        *,
        observations: Iterable[ClosureObservation],
    ) -> ClosureReport:
        """Evaluate only declared closure obligations against one exact after state."""
        if intent.repository_id != after.manifest.repository_id:
            raise ReviewApplicationError(
                "closure_repository_mismatch",
                "Implementation intent and after session belong to different repositories",
                remediation="Rebuild both artifacts with one stable repository identity.",
            )
        state = _single_state(after)
        completeness: dict[str, Literal["complete", "partial", "unavailable", "unknown"]] = {}
        for provider in after.manifest.providers:
            completeness[provider.provider_id] = (
                provider.status.value
                if provider.status in {
                    ProviderRunStatus.COMPLETE,
                    ProviderRunStatus.PARTIAL,
                    ProviderRunStatus.UNAVAILABLE,
                }
                else "unavailable"
            )
        return verify_implementation_closure(
            intent,
            after_source_state_id=state.source_state.state_id,
            after_source_state_digest=_source_digest(after),
            after_provider_digests=_provider_digests(after),
            provider_completeness=completeness,
            observations=list(observations),
            evidence_kinds_by_ref=_closure_evidence_kinds(after),
        )

    def check(self, path: Path) -> ArtifactCheck:
        """Validate one public artifact and return a stable machine result."""
        artifact = load_review_artifact(path)
        return self.check_model(artifact)

    def check_bytes(self, raw: bytes, *, max_bytes: int) -> ArtifactCheck:
        """Validate bounded transport bytes through the same artifact dispatch."""
        return self.check_model(parse_review_artifact(raw, max_bytes=max_bytes))

    def check_model(self, artifact: BaseModel) -> ArtifactCheck:
        """Return the common successful check result for an already validated model."""
        return ArtifactCheck(
            checked_artifact_type=str(getattr(artifact, "artifact_type")),
            checked_schema_version=str(getattr(artifact, "schema_version")),
            identity=artifact_identity(artifact),
        )


def review_capabilities() -> dict[str, Any]:
    """Machine-readable, versioned capabilities of the converged application layer."""
    return {
        "application": "anatomize.review",
        "application_api_version": "1.0.0",
        "operations": [
            "capabilities",
            "start",
            "dossier",
            "expand",
            "similarity",
            "change",
            "consolidation",
            "decision_overlay",
            "evaluate_overlay",
            "implementation_intent",
            "verify",
            "check",
            "export",
            "recover",
        ],
        "profiles": [item.value for item in DossierProfile],
        "target_kinds": [item.value for item in TargetKind],
        "directions": [item.value for item in QueryDirection],
        "interaction": {
            "pagination": "typed expansion actions and self-validating cursors",
            "optional_provider_invocation": "never implicit; explicit artifact import only",
            "human_output": "plain text or Markdown; no ANSI required",
            "machine_output": "canonical JSON with Python schema parity",
        },
        "schemas": {
            "session": SESSION_SCHEMA_VERSION,
            "evidence": EVIDENCE_SCHEMA_VERSION,
            "state_manifest": STATE_MANIFEST_SCHEMA_VERSION,
            "dossier_exchange": "1.0.0",
            "similarity": "1.0.0",
            "consolidation_dossier": "1.0.0",
            "decision_overlay": DECISION_OVERLAY_SCHEMA_VERSION,
            "overlay_evaluation": "1.0.0",
            "change_dossier": "1.0.0",
            "implementation_intent": "1.0.0",
            "closure_report": "1.0.0",
            "artifact_check": "1.0.0",
        },
    }


def target_selector(value: str, *, kind: TargetKind) -> TargetSelector:
    """Convert concise CLI syntax into the same typed selector accepted by Python."""
    if kind is TargetKind.REPOSITORY:
        return TargetSelector(kind=kind, identity=value)
    if kind is TargetKind.RANGE:
        path, line, column = _parse_range(value)
        return TargetSelector(
            kind=kind,
            locator=DossierLocator(path=path, start_line=line, start_column=column),
        )
    if kind is TargetKind.SYMBOL:
        return TargetSelector(kind=kind, locator=DossierLocator(qualified_name=value))
    if kind is TargetKind.DOCUMENTATION_SECTION:
        path, separator, heading = value.partition("#")
        return TargetSelector(
            kind=kind,
            locator=DossierLocator(path=path, heading=heading if separator else None),
        )
    if kind in {
        TargetKind.FILE,
        TargetKind.TEST,
        TargetKind.CONFIGURATION,
        TargetKind.DATA,
        TargetKind.WORKFLOW,
        TargetKind.ARTIFACT,
    }:
        return TargetSelector(kind=kind, locator=DossierLocator(path=value))
    return TargetSelector(kind=kind, identity=value)


def _validate_provider_inputs(envelopes: list[ProviderEnvelope], baseline: ProviderEnvelope) -> None:
    run_ids = [item.provider_run_id for item in envelopes]
    if len(run_ids) != len(set(run_ids)):
        raise ReviewApplicationError(
            "provider_run_duplicate",
            "Provider imports repeat a provider-run identity",
            remediation="Import each exact provider run once.",
        )
    for envelope in envelopes[1:]:
        if envelope.invocation.mode is not InvocationMode.ARTIFACT_IMPORT:
            raise ReviewApplicationError(
                "provider_authority_untrusted",
                f"Imported provider {envelope.provider_id} self-declares {envelope.invocation.mode.value} authority",
                remediation=(
                    "Normalize external output into an artifact-import envelope; execution authority must be "
                    "granted and recorded by the caller that actually ran the provider."
                ),
            )
        if envelope.repository_id != baseline.repository_id:
            raise ReviewApplicationError(
                "provider_repository_mismatch",
                f"Provider {envelope.provider_id} belongs to another repository",
                remediation="Regenerate the provider envelope with the selected repository identity.",
            )
        if envelope.primary_source_state_id != baseline.primary_source_state_id:
            raise ReviewApplicationError(
                "provider_state_mismatch",
                f"Provider {envelope.provider_id} belongs to another source state",
                remediation="Regenerate the provider envelope after indexing the exact current state.",
            )
        primary = next(item for item in envelope.source_states if item.state_id == envelope.primary_source_state_id)
        if primary != baseline.source_states[0]:
            raise ReviewApplicationError(
                "provider_state_conflict",
                f"Provider {envelope.provider_id} reuses the state identity for different facts",
                remediation="Reject the artifact and regenerate it from the exact repository state.",
            )


def _schema_bindings() -> list[SchemaBinding]:
    values: list[tuple[str, str, type[BaseModel]]] = [
        (SESSION_ARTIFACT_TYPE, SESSION_SCHEMA_VERSION, ReviewSessionManifest),
        (EVIDENCE_ARTIFACT_TYPE, EVIDENCE_SCHEMA_VERSION, RepositoryEvidence),
        (STATE_MANIFEST_ARTIFACT_TYPE, STATE_MANIFEST_SCHEMA_VERSION, StateManifest),
    ]
    return [
        SchemaBinding(
            artifact_type=artifact_type,
            schema_version=version,
            schema_digest=sha256_digest(canonical_json_bytes(model.model_json_schema(mode="serialization"))),
        )
        for artifact_type, version, model in values
    ]


def _selected_source_paths(index: RepositoryIndex, paths: Iterable[str]) -> list[str]:
    selected = sorted(set(paths))
    known = {item.path for item in index.files}
    for value in selected:
        if value not in known:
            raise ReviewApplicationError(
                "source_path_unknown",
                f"Source path is absent from the normalized index: {value}",
                remediation="Select an exact repository-relative path reported by the index.",
            )
    return selected


def _source_media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "text/x-python",
        ".r": "text/x-r-source",
        ".md": "text/markdown",
        ".qmd": "text/markdown",
        ".rst": "text/x-rst",
    }.get(suffix, "text/plain")


def _single_state(bundle: ReviewSessionBundle) -> StateManifest:
    if len(bundle.manifest.source_states) != 1:
        raise ReviewApplicationError(
            "single_state_required",
            "This lifecycle operation requires a one-state session",
            remediation="Supply a session built by review start for one exact repository state.",
        )
    return bundle.manifest.source_states[0]


def _require_same_repository(before: ReviewSessionBundle, after: ReviewSessionBundle) -> None:
    if before.manifest.repository_id != after.manifest.repository_id:
        raise ReviewApplicationError(
            "change_repository_mismatch",
            "Comparison sessions belong to different repositories",
            remediation="Use the same explicit --repository-id when building both sessions.",
        )


def _session_evidence(bundle: ReviewSessionBundle) -> RepositoryEvidence:
    evidence = list(DossierContext.from_bundle(bundle).evidence)
    return evidence[0] if len(evidence) == 1 else merge_repository_evidence(evidence)


def _provider_digests(bundle: ReviewSessionBundle) -> dict[str, str]:
    by_provider: dict[str, list[str]] = defaultdict(list)
    for state in bundle.manifest.source_states:
        for run in state.provider_states:
            by_provider[run.provider_id].extend(item.digest for item in run.artifacts)
    return {
        provider_id: sha256_digest(canonical_json_bytes(sorted(digests)))
        for provider_id, digests in sorted(by_provider.items())
    }


def _closure_evidence_kinds(bundle: ReviewSessionBundle) -> dict[str, set[str]]:
    """Index after-session evidence identities by independently derived kind."""
    kinds: dict[str, set[str]] = defaultdict(set)
    for artifact in bundle.manifest.artifacts:
        kinds[artifact.artifact_id].update({"session_artifact", artifact.role.value})
    for state in bundle.manifest.source_states:
        kinds[state.source_state.state_id].add("source_state")
    for evidence in DossierContext.from_bundle(bundle).evidence:
        for provider_artifact in evidence.provider_artifacts:
            kinds[provider_artifact.artifact_id].add("provider_artifact")
        for provider_run in evidence.provider_runs:
            kinds[provider_run.provider_run_id].add("provider_run")
        for location in evidence.locations:
            kinds[location.location_id].add("location")
        for edge in evidence.edges:
            kinds[edge.edge_id].add("relationship")
        for contract in evidence.contracts:
            kinds[contract.contract_id].add("contract")
        for candidate in evidence.candidates:
            kinds[candidate.candidate_id].add("candidate")
        for observation in evidence.observations:
            kinds[observation.observation_id].add("observation")
            if isinstance(observation, RuntimeObservation):
                kinds[observation.observation_id].update({"runtime", "test_runtime"})
        for entity in evidence.entities:
            entity_kinds: set[str]
            if isinstance(entity, FileEntity):
                roles = set(entity.roles)
                entity_kinds = {"file"}
                if "source" in roles:
                    entity_kinds.update({"source", "definition"})
                if "test" in roles:
                    entity_kinds.add("test_source")
                if "documentation" in roles:
                    entity_kinds.add("documentation")
                if "configuration" in roles:
                    entity_kinds.add("configuration")
            elif isinstance(entity, SymbolEntity):
                entity_kinds = {"definition"}
            elif isinstance(entity, DocumentationEntity):
                entity_kinds = {"documentation"}
            elif isinstance(entity, ConfigurationEntity):
                entity_kinds = {"configuration"}
            elif isinstance(entity, TestEntity):
                entity_kinds = {"test_source"}
            elif isinstance(entity, RuntimeEntity):
                entity_kinds = {"runtime", "test_runtime"}
            elif isinstance(entity, WorkflowEntity):
                entity_kinds = {"workflow"}
            elif isinstance(entity, DataEntity):
                entity_kinds = {"data"}
            elif isinstance(entity, ArtifactEntity):
                entity_kinds = {"artifact"}
            elif isinstance(entity, DiagnosticEntity):
                entity_kinds = {"diagnostic"}
            else:
                entity_kinds = {"entity"}
            kinds[entity.entity_id].update(entity_kinds)
    return {reference: set(sorted(values)) for reference, values in sorted(kinds.items())}


def _source_digest(bundle: ReviewSessionBundle) -> str:
    return sha256_digest(_single_state(bundle).source_state.content_digest.encode("utf-8"))


def _candidate_source_digest(candidate: SimilarityCandidate) -> str:
    """Digest only the source coordinates and content reviewed by a decision."""
    return sha256_digest(
        canonical_json_bytes(
            sorted(
                (
                    {
                        "path": member.path,
                        "start_line": member.start_line,
                        "end_line": member.end_line,
                        "entity_id": member.entity_id,
                        "content_digest": member.content_digest,
                    }
                    for member in candidate.members
                ),
                key=lambda item: (
                    str(item["path"]),
                    int(item["start_line"]),
                    int(item["end_line"]),
                ),
            )
        )
    )


def _candidate_provider_digests(candidate: SimilarityCandidate) -> dict[str, str]:
    """Bind acquisition semantics without coupling a decision to an unrelated tree state."""
    digest = sha256_digest(
        canonical_json_bytes(
            {
                "method": candidate.method.value,
                "method_version": candidate.method_version,
                "configuration_digest": candidate.configuration_digest,
                "threshold": candidate.threshold,
            }
        )
    )
    return {"candidate_acquisition": digest}


def _require_candidate_state(bundle: ReviewSessionBundle, candidate: SimilarityCandidate) -> None:
    if candidate.source_state_id != _single_state(bundle).source_state.state_id:
        raise ReviewApplicationError(
            "candidate_state_mismatch",
            "Similarity candidate and session source states differ",
            remediation="Use candidate evidence generated for this exact session state.",
        )


def _similarity_candidate(
    record: CandidateRecord,
    evidence: RepositoryEvidence,
    baseline: SessionProviderBinding,
) -> SimilarityCandidate:
    entities = {item.entity_id: item for item in evidence.entities}
    locations = {item.location_id: item for item in evidence.locations}
    files = {item.entity_id: item for item in evidence.entities if isinstance(item, FileEntity)}
    members: list[CandidateRegion] = []
    for entity_id in record.member_entity_ids:
        entity = entities[entity_id]
        location = next(
            (
                locations[location_id]
                for location_id in entity.location_ids
                if location_id in locations and locations[location_id].path is not None
            ),
            None,
        )
        if location is None or location.path is None:
            continue
        source_range = location.source_range
        file = files.get(location.file_id or "")
        roles = set(file.roles) if file is not None else set()
        role = (
            "documentation"
            if isinstance(entity, DocumentationEntity)
            else "test"
            if "test" in roles
            else "implementation"
        )
        digest = getattr(entity, "digest", None)
        members.append(
            CandidateRegion(
                source_state_id=record.source_state_id,
                path=location.path,
                start_line=source_range.start.line if source_range is not None else 1,
                end_line=source_range.end.line if source_range is not None else 1,
                entity_id=entity.entity_id,
                role=role,
                language=getattr(entity, "language", None),
                content_digest=(
                    digest if isinstance(digest, str) and digest.startswith("sha256:")
                    else f"sha256:{digest}"
                    if isinstance(digest, str) and digest
                    else None
                ),
            )
        )
    if len(members) < 2:
        raise ReviewApplicationError(
            "similarity_candidate_unlocatable",
            f"Candidate {record.candidate_id} has fewer than two repository-located members",
            remediation="Regenerate candidate evidence with exact member locations.",
        )
    observations = {item.observation_id: item for item in evidence.observations}
    observation = next((observations[item] for item in record.observation_ids if item in observations), None)
    runs = {item.provider_run_id: item for item in evidence.provider_runs}
    run = runs.get(observation.provider_run_id) if observation is not None else None
    method = (
        SimilarityMethod.EXTERNAL_NEAR_MATCH
        if "jscpd" in record.method.casefold()
        else SimilarityMethod.EXACT
        if record.strength.value == "exact"
        else SimilarityMethod.NORMALIZED
    )
    return build_similarity_candidate(
        source_state_id=record.source_state_id,
        granularity=(
            CandidateGranularity.BLOCK
            if method is SimilarityMethod.EXTERNAL_NEAR_MATCH
            or all(isinstance(entities[item], DocumentationEntity) for item in record.member_entity_ids)
            else CandidateGranularity.DEFINITION
        ),
        method=method,
        method_version=run.provider_version if run is not None else baseline.provider_version,
        configuration_digest=run.configuration_digest if run is not None else baseline.configuration_digest,
        provider_run_id=run.provider_run_id if run is not None else baseline.provider_run_id,
        threshold=getattr(observation, "score", None) if method is SimilarityMethod.EXTERNAL_NEAR_MATCH else None,
        token_count=None,
        members=members,
        aligned_hunks=[],
        unmatched_member_regions=[],
        strength=record.strength,
        limitations=[
            "Structural similarity does not establish interchangeable intent or safe deletion.",
        ],
        rationale=record.rationale,
    )


_DELTA_DIMENSIONS = {
    DeltaFamily.ENTITY: ChangeDimension.ENTITY,
    DeltaFamily.RELATIONSHIP: ChangeDimension.RELATIONSHIP,
    DeltaFamily.API: ChangeDimension.API,
    DeltaFamily.DIAGNOSTIC: ChangeDimension.DIAGNOSTIC,
    DeltaFamily.DUPLICATE: ChangeDimension.CANDIDATE,
    DeltaFamily.TEST: ChangeDimension.TEST,
    DeltaFamily.DOCUMENTATION: ChangeDimension.DOCUMENTATION,
    DeltaFamily.WORKFLOW: ChangeDimension.WORKFLOW,
    DeltaFamily.DATA: ChangeDimension.DATA,
    DeltaFamily.ENVIRONMENT: ChangeDimension.ENVIRONMENT,
    DeltaFamily.ARTIFACT: ChangeDimension.ARTIFACT,
}


def _comparison_changes(comparison: RepositoryComparison) -> list[Any]:
    lineage = {item.lineage_id: item for item in comparison.lineage}
    changes = []
    for delta in comparison.deltas:
        kinds = {
            DeltaStatus.ADDED: [ChangeBoundaryKind.ADDED],
            DeltaStatus.REMOVED: [ChangeBoundaryKind.DELETED],
            DeltaStatus.CHANGED: [ChangeBoundaryKind.MODIFIED],
        }.get(delta.status)
        if kinds is None:
            continue
        if any(lineage[item].kind.value == "moved" for item in delta.lineage_ids if item in lineage):
            kinds.append(ChangeBoundaryKind.MOVED)
        changes.append(
            build_change_evidence(
                dimension=_DELTA_DIMENSIONS[delta.family],
                kinds=kinds,
                predecessor_refs=[item.record_id for item in delta.predecessor_refs],
                successor_refs=[item.record_id for item in delta.successor_refs],
                evidence_refs=[delta.delta_id, *delta.lineage_ids],
                lineage="exact",
                rationale=delta.rationale,
            )
        )
    return changes


_QUESTION_ROLES: dict[ConsolidationQuestion, tuple[EvidenceRole, ...]] = {
    ConsolidationQuestion.MEMBERS_AND_DIFFERENCES: (EvidenceRole.DUPLICATE_CANDIDATE, EvidenceRole.DEFINITION),
    ConsolidationQuestion.PUBLIC_CONTRACTS: (EvidenceRole.CONTRACT, EvidenceRole.PUBLIC_SURFACE),
    ConsolidationQuestion.CALLERS_AND_CONSUMERS: (EvidenceRole.CALLER, EvidenceRole.CONSUMER, EvidenceRole.REFERENCE),
    ConsolidationQuestion.TEST_INTENT: (EvidenceRole.TEST,),
    ConsolidationQuestion.RUNTIME_CONTEXT: (EvidenceRole.RUNTIME,),
    ConsolidationQuestion.DOCUMENTATION: (EvidenceRole.DOCUMENTATION,),
    ConsolidationQuestion.WORKFLOWS: (EvidenceRole.WORKFLOW, EvidenceRole.CONFIGURATION),
    ConsolidationQuestion.HISTORY: (EvidenceRole.CHANGE,),
    ConsolidationQuestion.PROVENANCE: (EvidenceRole.PROVENANCE,),
    ConsolidationQuestion.CONFLICTS: (EvidenceRole.CONFLICT, EvidenceRole.UNKNOWN),
    ConsolidationQuestion.OMISSIONS: (),
    ConsolidationQuestion.PRIOR_DECISIONS: (EvidenceRole.DECISION_CONTEXT,),
}


def _consolidation_group(
    question: ConsolidationQuestion,
    candidate: SimilarityCandidate,
    refs_by_role: dict[EvidenceRole, list[str]],
    base: DossierExchange,
) -> ReviewEvidenceGroup:
    refs = sorted(
        {
            ref
            for role in _QUESTION_ROLES[question]
            for ref in refs_by_role.get(role, [])
        }
    )
    if question is ConsolidationQuestion.MEMBERS_AND_DIFFERENCES:
        refs = sorted({candidate.candidate_id, *refs})
    if question is ConsolidationQuestion.OMISSIONS:
        refs = sorted(item.omission_id for item in base.dossier.omissions)
    limitations = [] if refs else ["No evidence for this review question is present in the bounded base dossier."]
    return ReviewEvidenceGroup(
        question=question,
        evidence_refs=refs,
        summary=(
            f"{len(refs)} exact evidence reference(s) are available."
            if refs
            else "Evidence is absent; no consolidation verdict can be inferred for this question."
        ),
        limitations=limitations,
    )


def _parse_range(value: str) -> tuple[str, int, int]:
    parts = value.rsplit(":", 2)
    if len(parts) < 2:
        raise ReviewApplicationError(
            "range_target_invalid",
            f"Range target requires PATH:LINE[:COLUMN], found {value!r}",
            remediation="Use a repository-relative path and one-based line number.",
        )
    try:
        if len(parts) == 2:
            return parts[0], int(parts[1]), 0
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError as error:
        raise ReviewApplicationError(
            "range_target_invalid",
            f"Range line and column must be integers: {value!r}",
            remediation="Use PATH:LINE or PATH:LINE:COLUMN.",
        ) from error
