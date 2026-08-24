"""Reproducible review-session manifests and portable content bundles."""

from __future__ import annotations

import base64
import zlib
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from anatomize._artifacts import canonical_ordered_json_bytes, content_id, sha256_digest
from anatomize.evidence import EVIDENCE_SCHEMA_VERSION, EvidenceModel, ProviderRunStatus, validate_repository_path
from anatomize.temporal import STATE_MANIFEST_SCHEMA_VERSION, StateManifest

SESSION_ARTIFACT_TYPE: Literal["anatomize.session"] = "anatomize.session"
SESSION_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
SESSION_BUNDLE_ARTIFACT_TYPE: Literal["anatomize.session-bundle"] = "anatomize.session-bundle"
SESSION_BUNDLE_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
MAX_DECODED_ARTIFACT_BYTES = 128 * 1024 * 1024


class SessionStatus(str, Enum):
    """Terminal state of an attempted review session."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactRole(str, Enum):
    """Lifecycle role of bytes bound into a review session."""

    NORMALIZED_EVIDENCE = "normalized_evidence"
    COMPARISON = "comparison"
    REPORT = "report"
    SOURCE_SLICE = "source_slice"
    AUXILIARY = "auxiliary"


class OmissionScope(str, Enum):
    """Session surface deliberately absent or incomplete."""

    PROVIDER = "provider"
    SOURCE = "source"
    EVIDENCE = "evidence"
    INDEX = "index"
    REPORT = "report"
    SLICE = "slice"
    BUDGET = "budget"


class SchemaBinding(EvidenceModel):
    """Exact public schema used by one session artifact family."""

    artifact_type: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SessionProviderBinding(EvidenceModel):
    """Provider identity projected from the canonical state manifest."""

    provider_run_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    configuration_digest: str = Field(min_length=1)
    status: ProviderRunStatus


class QueryBinding(EvidenceModel):
    """Exact normalized request whose products the session contains."""

    query_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_query_id(self) -> QueryBinding:
        if self.query_id != content_id(
            "query",
            {"operation": self.operation, "parameters": self.parameters},
        ):
            raise ValueError("query identifier does not match its content")
        return self


class BudgetBinding(EvidenceModel):
    """One declared resource or output budget and its final use."""

    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    limit: int = Field(ge=0)
    used: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_usage(self) -> BudgetBinding:
        if self.used > self.limit:
            raise ValueError("budget use cannot exceed its declared limit")
        return self


class SessionArtifact(EvidenceModel):
    """Digest-bound portable artifact produced or consumed by a session."""

    artifact_id: str = Field(min_length=1)
    role: ArtifactRole
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    portable_path: str = Field(min_length=1)
    source_state_ids: list[str] = Field(default_factory=list)
    provider_run_id: str | None = None
    derived_from_ids: list[str] = Field(default_factory=list)
    deterministic: bool = True
    generated_at: datetime | None = None

    @field_validator("portable_path")
    @classmethod
    def validate_portable_path(cls, value: str) -> str:
        return validate_repository_path(value)

    @model_validator(mode="after")
    def validate_artifact(self) -> SessionArtifact:
        for values, label in [
            (self.source_state_ids, "source state"),
            (self.derived_from_ids, "derived artifact"),
        ]:
            if len(values) != len(set(values)):
                raise ValueError(f"session artifact {label} identifiers must be unique")
        if self.deterministic and self.generated_at is not None:
            raise ValueError("deterministic session artifacts must not contain a generation timestamp")
        if not self.deterministic:
            if self.generated_at is None or self.generated_at.utcoffset() is None:
                raise ValueError("nondeterministic session artifacts require a timezone-aware timestamp")
        if self.artifact_id != content_id("session-artifact", _artifact_identity(self)):
            raise ValueError("session artifact identifier does not match its content")
        return self


class SessionOmission(EvidenceModel):
    """Explicitly bounded absence retained in a session manifest."""

    omission_id: str = Field(min_length=1)
    scope: OmissionScope
    code: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    provider_id: str | None = None

    @model_validator(mode="after")
    def validate_omission_id(self) -> SessionOmission:
        payload = {
            "scope": self.scope.value,
            "code": self.code,
            "rationale": self.rationale,
            "provider_id": self.provider_id,
        }
        if self.omission_id != content_id("session-omission", payload):
            raise ValueError("session omission identifier does not match its content")
        return self


class ReviewSessionManifest(EvidenceModel):
    """Complete provenance for one reproducible agent review session."""

    artifact_type: Literal["anatomize.session"] = SESSION_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = SESSION_SCHEMA_VERSION
    session_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    status: SessionStatus
    deterministic: bool = True
    recorded_at: datetime | None = None
    configuration_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_states: list[StateManifest] = Field(min_length=1, max_length=2)
    schemas: list[SchemaBinding] = Field(min_length=1)
    providers: list[SessionProviderBinding] = Field(min_length=1)
    query: QueryBinding
    budgets: list[BudgetBinding] = Field(min_length=1)
    artifacts: list[SessionArtifact] = Field(min_length=1)
    omissions: list[SessionOmission] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest(self) -> ReviewSessionManifest:
        state_ids = [item.source_state.state_id for item in self.source_states]
        if len(state_ids) != len(set(state_ids)):
            raise ValueError("session source states must be unique")
        if any(item.repository_id != self.repository_id for item in self.source_states):
            raise ValueError("session source states belong to another repository")
        _require_unique([item.provider_run_id for item in self.providers], "session provider runs")
        _require_unique([item.artifact_id for item in self.artifacts], "session artifacts")
        _require_unique([item.portable_path for item in self.artifacts], "session artifact paths")
        _require_unique([item.name for item in self.budgets], "session budgets")
        _require_unique([item.omission_id for item in self.omissions], "session omissions")
        schema_keys = [(item.artifact_type, item.schema_version) for item in self.schemas]
        _require_unique(schema_keys, "session schemas")

        if self.deterministic and self.recorded_at is not None:
            raise ValueError("deterministic sessions must not contain a recorded timestamp")
        if not self.deterministic:
            if self.recorded_at is None or self.recorded_at.utcoffset() is None:
                raise ValueError("nondeterministic sessions require a timezone-aware recorded timestamp")
        if self.deterministic and any(not item.deterministic for item in self.artifacts):
            raise ValueError("a session containing nondeterministic artifacts cannot claim deterministic identity")

        artifacts = {item.artifact_id: item for item in self.artifacts}
        providers = {item.provider_run_id: item for item in self.providers}
        state_set = set(state_ids)
        for artifact in self.artifacts:
            unknown_states = sorted(set(artifact.source_state_ids).difference(state_set))
            if unknown_states:
                raise ValueError(f"session artifact references unknown source states: {unknown_states}")
            if artifact.provider_run_id is not None and artifact.provider_run_id not in providers:
                raise ValueError(f"session artifact references unknown provider run: {artifact.provider_run_id}")
            unknown_inputs = sorted(set(artifact.derived_from_ids).difference(artifacts))
            if unknown_inputs:
                raise ValueError(f"session artifact references unknown inputs: {unknown_inputs}")
            if artifact.artifact_id in artifact.derived_from_ids:
                raise ValueError("session artifact cannot derive from itself")
        _reject_artifact_cycles(artifacts)

        manifest_runs = {
            run.provider_run_id: run
            for state in self.source_states
            for run in state.provider_states
        }
        if set(manifest_runs) != set(providers):
            raise ValueError("session providers must exactly match state-manifest provider runs")
        for provider_run_id, state_run in manifest_runs.items():
            provider = providers[provider_run_id]
            if (
                provider.provider_id,
                provider.provider_version,
                provider.configuration_digest,
                provider.status,
            ) != (
                state_run.provider_id,
                state_run.provider_version,
                state_run.configuration_digest,
                state_run.status,
            ):
                raise ValueError(f"session provider differs from state manifest: {provider_run_id}")

        required_schemas = {
            (SESSION_ARTIFACT_TYPE, SESSION_SCHEMA_VERSION),
            ("anatomize.evidence", EVIDENCE_SCHEMA_VERSION),
            ("anatomize.state-manifest", STATE_MANIFEST_SCHEMA_VERSION),
        }
        missing_schemas = sorted(required_schemas.difference(schema_keys))
        if missing_schemas:
            raise ValueError(f"session is missing required schema bindings: {missing_schemas}")
        roles = {item.role for item in self.artifacts}
        if ArtifactRole.NORMALIZED_EVIDENCE not in roles:
            raise ValueError("complete session requires a normalized_evidence artifact")
        for state in self.source_states:
            evidence_artifacts = [
                item
                for item in self.artifacts
                if item.role is ArtifactRole.NORMALIZED_EVIDENCE
                and item.source_state_ids == [state.source_state.state_id]
            ]
            if len(evidence_artifacts) != 1:
                raise ValueError("each session source state requires exactly one normalized evidence artifact")
            if evidence_artifacts[0].digest != state.evidence_artifact_digest:
                raise ValueError("normalized evidence artifact digest differs from its state manifest")
        omission_scopes = {item.scope for item in self.omissions}
        for role, omission_scope in [
            (ArtifactRole.REPORT, OmissionScope.REPORT),
            (ArtifactRole.SOURCE_SLICE, OmissionScope.SLICE),
        ]:
            if role not in roles and omission_scope not in omission_scopes:
                raise ValueError(f"session must bind or explicitly omit its {role.value} output")
        for provider in self.providers:
            if provider.status is not ProviderRunStatus.COMPLETE and not any(
                item.scope is OmissionScope.PROVIDER and item.provider_id == provider.provider_id
                for item in self.omissions
            ):
                raise ValueError("incomplete provider run requires an explicit provider omission")

        if self.session_id != content_id("session", _session_identity(self)):
            raise ValueError("session identifier does not match its content")
        return self


class ArtifactBlob(EvidenceModel):
    """Compressed base64 transport for one exact session artifact."""

    artifact_id: str = Field(min_length=1)
    encoding: Literal["base64+zlib"] = "base64+zlib"
    content: str

    def decoded(self) -> bytes:
        """Decode strictly, rejecting malformed portable content."""
        return _decode_blob(self.content)


class ReviewSessionBundle(EvidenceModel):
    """Self-contained deterministic export from which the derived store is rebuilt."""

    artifact_type: Literal["anatomize.session-bundle"] = SESSION_BUNDLE_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = SESSION_BUNDLE_SCHEMA_VERSION
    bundle_id: str = Field(min_length=1)
    manifest: ReviewSessionManifest
    blobs: list[ArtifactBlob] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bundle(self) -> ReviewSessionBundle:
        _require_unique([item.artifact_id for item in self.blobs], "session bundle blobs")
        artifacts = {item.artifact_id: item for item in self.manifest.artifacts}
        blobs = {item.artifact_id: item for item in self.blobs}
        if set(artifacts) != set(blobs):
            raise ValueError("session bundle must contain exactly one blob for every manifest artifact")
        for artifact_id, artifact in artifacts.items():
            decoded = blobs[artifact_id].decoded()
            if len(decoded) != artifact.size_bytes:
                raise ValueError(f"session artifact byte size mismatch: {artifact_id}")
            if sha256_digest(decoded) != artifact.digest:
                raise ValueError(f"session artifact content digest mismatch: {artifact_id}")
        expected = content_id(
            "session-bundle",
            {
                "session_id": self.manifest.session_id,
                "artifacts": sorted((item.artifact_id, item.digest) for item in artifacts.values()),
            },
        )
        if self.bundle_id != expected:
            raise ValueError("session bundle identifier does not match its content")
        return self


def build_query(operation: str, parameters: dict[str, JsonValue] | None = None) -> QueryBinding:
    """Build one content-addressed normalized query."""
    values = parameters or {}
    return QueryBinding(
        query_id=content_id("query", {"operation": operation, "parameters": values}),
        operation=operation,
        parameters=values,
    )


def build_session_artifact(
    *,
    role: ArtifactRole,
    content: bytes,
    media_type: str,
    portable_path: str,
    source_state_ids: list[str] | None = None,
    provider_run_id: str | None = None,
    derived_from_ids: list[str] | None = None,
    deterministic: bool = True,
    generated_at: datetime | None = None,
) -> tuple[SessionArtifact, ArtifactBlob]:
    """Bind exact bytes to portable metadata and transport content."""
    digest = sha256_digest(content)
    values = {
        "role": role.value,
        "digest": digest,
        "size_bytes": len(content),
        "media_type": media_type,
        "portable_path": portable_path,
        "source_state_ids": sorted(source_state_ids or []),
        "provider_run_id": provider_run_id,
        "derived_from_ids": sorted(derived_from_ids or []),
        "deterministic": deterministic,
        "generated_at": generated_at.isoformat() if generated_at is not None else None,
    }
    artifact_id = content_id("session-artifact", values)
    artifact = SessionArtifact(artifact_id=artifact_id, **values)
    blob = ArtifactBlob(
        artifact_id=artifact_id,
        content=base64.b64encode(zlib.compress(content, level=9)).decode("ascii"),
    )
    return artifact, blob


def build_omission(
    scope: OmissionScope,
    code: str,
    rationale: str,
    *,
    provider_id: str | None = None,
) -> SessionOmission:
    """Create one content-addressed explicit omission."""
    payload = {
        "scope": scope.value,
        "code": code,
        "rationale": rationale,
        "provider_id": provider_id,
    }
    return SessionOmission(omission_id=content_id("session-omission", payload), **payload)


def build_session_manifest(
    *,
    repository_id: str,
    status: SessionStatus,
    configuration_digest: str,
    policy_digest: str,
    source_states: list[StateManifest],
    schemas: list[SchemaBinding],
    providers: list[SessionProviderBinding],
    query: QueryBinding,
    budgets: list[BudgetBinding],
    artifacts: list[SessionArtifact],
    omissions: list[SessionOmission] | None = None,
    deterministic: bool = True,
    recorded_at: datetime | None = None,
) -> ReviewSessionManifest:
    """Build a content-addressed session manifest from its complete provenance."""
    values = {
        "repository_id": repository_id,
        "status": status,
        "deterministic": deterministic,
        "recorded_at": recorded_at,
        "configuration_digest": configuration_digest,
        "policy_digest": policy_digest,
        "source_states": sorted(source_states, key=lambda item: item.source_state.state_id),
        "schemas": sorted(schemas, key=lambda item: (item.artifact_type, item.schema_version)),
        "providers": sorted(providers, key=lambda item: item.provider_run_id),
        "query": query,
        "budgets": sorted(budgets, key=lambda item: item.name),
        "artifacts": sorted(artifacts, key=lambda item: item.artifact_id),
        "omissions": sorted(omissions or [], key=lambda item: item.omission_id),
    }
    return ReviewSessionManifest(
        session_id=content_id("session", _json_values(values)),
        **values,
    )


def build_session_bundle(
    manifest: ReviewSessionManifest,
    blobs: list[ArtifactBlob],
) -> ReviewSessionBundle:
    """Build and validate one self-contained portable export."""
    bundle_id = content_id(
        "session-bundle",
        {
            "session_id": manifest.session_id,
            "artifacts": sorted((item.artifact_id, item.digest) for item in manifest.artifacts),
        },
    )
    return ReviewSessionBundle(
        bundle_id=bundle_id,
        manifest=manifest,
        blobs=sorted(blobs, key=lambda item: item.artifact_id),
    )


def canonical_session_bundle_bytes(bundle: ReviewSessionBundle) -> bytes:
    """Serialize one bundle deterministically for export, hashing, and storage."""
    return canonical_ordered_json_bytes(bundle.model_dump(mode="json"))


def _artifact_identity(artifact: SessionArtifact) -> dict[str, object]:
    return {
        "role": artifact.role.value,
        "digest": artifact.digest,
        "size_bytes": artifact.size_bytes,
        "media_type": artifact.media_type,
        "portable_path": artifact.portable_path,
        "source_state_ids": sorted(artifact.source_state_ids),
        "provider_run_id": artifact.provider_run_id,
        "derived_from_ids": sorted(artifact.derived_from_ids),
        "deterministic": artifact.deterministic,
        "generated_at": artifact.generated_at.isoformat() if artifact.generated_at is not None else None,
    }


def _session_identity(session: ReviewSessionManifest) -> dict[str, object]:
    return _json_values(
        {
            "repository_id": session.repository_id,
            "status": session.status,
            "deterministic": session.deterministic,
            "recorded_at": session.recorded_at,
            "configuration_digest": session.configuration_digest,
            "policy_digest": session.policy_digest,
            "source_states": session.source_states,
            "schemas": session.schemas,
            "providers": session.providers,
            "query": session.query,
            "budgets": session.budgets,
            "artifacts": session.artifacts,
            "omissions": session.omissions,
        }
    )


def _json_values(values: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, Enum):
            normalized[key] = value.value
        elif isinstance(value, datetime):
            normalized[key] = value.isoformat()
        elif isinstance(value, list):
            normalized[key] = [
                item.model_dump(mode="json") if isinstance(item, EvidenceModel) else item for item in value
            ]
        elif isinstance(value, EvidenceModel):
            normalized[key] = value.model_dump(mode="json")
        else:
            normalized[key] = value
    return normalized


def _require_unique(values: Sequence[object], label: str) -> None:
    rendered = [repr(item) for item in values]
    if len(rendered) != len(set(rendered)):
        raise ValueError(f"{label} must be unique")


@lru_cache(maxsize=16)
def _decode_blob(content: str) -> bytes:
    try:
        compressed = base64.b64decode(content.encode("ascii"), validate=True)
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(compressed, MAX_DECODED_ARTIFACT_BYTES + 1)
        if len(decoded) > MAX_DECODED_ARTIFACT_BYTES or decompressor.unconsumed_tail:
            raise ValueError("session artifact exceeds the decoded byte limit")
        decoded += decompressor.flush()
        if not decompressor.eof or decompressor.unused_data or len(decoded) > MAX_DECODED_ARTIFACT_BYTES:
            raise ValueError("session artifact compression stream is invalid")
        return decoded
    except (UnicodeEncodeError, ValueError, zlib.error) as error:
        raise ValueError("session artifact blob is not valid bounded base64+zlib") from error


def _reject_artifact_cycles(artifacts: dict[str, SessionArtifact]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in visiting:
            raise ValueError("session artifact derivation graph contains a cycle")
        if artifact_id in visited:
            return
        visiting.add(artifact_id)
        for dependency in artifacts[artifact_id].derived_from_ids:
            visit(dependency)
        visiting.remove(artifact_id)
        visited.add(artifact_id)

    for artifact_id in artifacts:
        visit(artifact_id)
