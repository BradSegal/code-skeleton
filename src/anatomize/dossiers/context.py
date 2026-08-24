"""Validated, reusable dossier query context over canonical session evidence."""

from __future__ import annotations

from dataclasses import dataclass

from anatomize._artifacts import canonical_json_bytes, sha256_digest
from anatomize._errors import AnatomizeError
from anatomize.dossiers.slicing import DossierSource, build_dossier_source, source_identity
from anatomize.evidence import EvidenceArtifactError, RepositoryEvidence, parse_evidence
from anatomize.sessions import ArtifactRole, ReviewSessionBundle, ReviewSessionManifest, SessionOmission


class DossierContextError(AnatomizeError):
    """Stable context-construction failure without ambient source fallback."""

@dataclass(frozen=True)
class DossierContext:
    """Exact session bindings and parsed evidence reused by many dossier queries."""

    session_id: str
    session_manifest_digest: str
    repository_id: str
    source_state_ids: tuple[str, ...]
    provider_run_ids: tuple[str, ...]
    policy_digest: str
    evidence: tuple[RepositoryEvidence, ...]
    sources: tuple[DossierSource, ...] = ()
    session_omissions: tuple[SessionOmission, ...] = ()

    @classmethod
    def from_bundle(cls, bundle: ReviewSessionBundle) -> DossierContext:
        """Load only normalized evidence bytes already bound by a valid bundle."""
        manifest = bundle.manifest
        blobs = {item.artifact_id: item for item in bundle.blobs}
        parsed: list[RepositoryEvidence] = []
        sources: list[DossierSource] = []
        for artifact in sorted(manifest.artifacts, key=lambda item: item.artifact_id):
            if artifact.role is ArtifactRole.SOURCE_SLICE:
                # Portable source snapshots use sources/<state-id>/<repository-path>.
                # Other slice artifacts remain session outputs and are not silently
                # treated as authoritative repository source.
                parts = artifact.portable_path.split("/")
                if len(parts) >= 3 and parts[0] == "sources" and artifact.source_state_ids == [parts[1]]:
                    raw = blobs[artifact.artifact_id].decoded()
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        text = None
                    sources.append(
                        build_dossier_source(
                            source_state_id=parts[1],
                            path="/".join(parts[2:]),
                            media_type=artifact.media_type,
                            text=text,
                            digest=artifact.digest,
                            size_bytes=len(raw),
                        )
                    )
                continue
            if artifact.role is not ArtifactRole.NORMALIZED_EVIDENCE:
                continue
            try:
                evidence = parse_evidence(blobs[artifact.artifact_id].decoded())
            except (EvidenceArtifactError, ValueError) as error:
                raise DossierContextError(
                    "normalized_evidence_invalid",
                    f"Session normalized evidence is invalid: {artifact.portable_path}",
                    remediation="Regenerate the source-bound session evidence.",
                ) from error
            if evidence.repository_id != manifest.repository_id:
                raise DossierContextError(
                    "evidence_repository_mismatch",
                    "Normalized evidence belongs to another repository",
                    remediation="Regenerate the complete session from one repository identity.",
                )
            evidence_states = {item.state_id for item in evidence.states}
            if evidence_states != set(artifact.source_state_ids):
                raise DossierContextError(
                    "evidence_source_state_mismatch",
                    "Normalized evidence source states differ from the session artifact binding",
                    remediation="Regenerate the evidence and session manifest together.",
                )
            parsed.append(evidence)
        if not parsed:
            raise DossierContextError(
                "normalized_evidence_missing",
                "Session contains no normalized evidence artifact",
                remediation="Build a complete evidence session before querying dossiers.",
            )
        return cls.from_evidence(
            session_id=manifest.session_id,
            session_manifest_digest=session_manifest_digest(manifest),
            repository_id=manifest.repository_id,
            source_state_ids=[item.source_state.state_id for item in manifest.source_states],
            provider_run_ids=[item.provider_run_id for item in manifest.providers],
            policy_digest=manifest.policy_digest,
            evidence=parsed,
            sources=sources,
            session_omissions=manifest.omissions,
        )

    @classmethod
    def from_evidence(
        cls,
        *,
        session_id: str,
        session_manifest_digest: str,
        repository_id: str,
        source_state_ids: list[str],
        provider_run_ids: list[str],
        policy_digest: str,
        evidence: list[RepositoryEvidence],
        sources: list[DossierSource] | None = None,
        session_omissions: list[SessionOmission] | None = None,
    ) -> DossierContext:
        """Construct a query context from already validated canonical evidence."""
        if not evidence:
            raise ValueError("dossier context requires canonical evidence")
        if len(source_state_ids) not in {1, 2} or len(source_state_ids) != len(set(source_state_ids)):
            raise ValueError("dossier context requires one or two unique source states")
        if len(provider_run_ids) != len(set(provider_run_ids)):
            raise ValueError("dossier context provider runs must be unique")
        declared_states = set(source_state_ids)
        observed_states: set[str] = set()
        observed_runs: set[str] = set()
        for artifact in evidence:
            if artifact.repository_id != repository_id:
                raise ValueError("dossier evidence belongs to another repository")
            observed_states.update(item.state_id for item in artifact.states)
            observed_runs.update(item.provider_run_id for item in artifact.provider_runs)
        if observed_states != declared_states:
            raise ValueError("dossier evidence must exactly cover the declared source states")
        if observed_runs.difference(provider_run_ids):
            raise ValueError("dossier evidence references provider runs outside the session")
        source_values = sources or []
        if len({source_identity(item) for item in source_values}) != len(source_values):
            raise ValueError("dossier sources must have unique source-state and path identities")
        if any(item.source_state_id not in declared_states for item in source_values):
            raise ValueError("dossier source belongs to a state outside the session")
        return cls(
            session_id=session_id,
            session_manifest_digest=session_manifest_digest,
            repository_id=repository_id,
            source_state_ids=tuple(source_state_ids),
            provider_run_ids=tuple(sorted(provider_run_ids)),
            policy_digest=policy_digest,
            evidence=tuple(sorted(evidence, key=lambda item: tuple(state.state_id for state in item.states))),
            sources=tuple(sorted(source_values, key=source_identity)),
            session_omissions=tuple(sorted(session_omissions or [], key=lambda item: item.omission_id)),
        )


def session_manifest_digest(manifest: ReviewSessionManifest) -> str:
    """Return the exact digest used by dossier requests and cursor bindings."""
    return sha256_digest(canonical_json_bytes(manifest.model_dump(mode="json")))
