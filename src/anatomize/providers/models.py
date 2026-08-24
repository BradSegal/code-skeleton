"""Versioned provider envelope and capability contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from anatomize._artifacts import canonical_json_bytes, canonicalize_json, compact_json, content_id, sha256_digest
from anatomize._errors import AnatomizeError
from anatomize.evidence.models import (
    AliasRecord,
    CandidateRecord,
    CompletenessRecord,
    ConflictRecord,
    ContractRecord,
    EdgeRecord,
    EntityRecord,
    EvidenceModel,
    EvidenceProducer,
    LimitationRecord,
    LineageRecord,
    LocationRecord,
    ObservationRecord,
    OmissionRecord,
    ProviderArtifactRecord,
    ProviderRunRecord,
    ProviderRunStatus,
    RepositoryEvidence,
    SourceStateRecord,
    validate_repository_path,
)

PROVIDER_API_VERSION: Literal["1.0.0"] = "1.0.0"
PROVIDER_ENVELOPE_ARTIFACT_TYPE: Literal["anatomize.provider"] = "anatomize.provider"
PROVIDER_ENVELOPE_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
DEFAULT_MAX_PROVIDER_BYTES = 32 * 1024 * 1024


class InvocationMode(str, Enum):
    """How provider bytes were obtained."""

    BUILT_IN = "built_in"
    ARTIFACT_IMPORT = "artifact_import"
    INSTALLED_PROVIDER = "installed_provider"
    DIRECT_COMMAND = "direct_command"
    EXTERNAL_POLICY = "external_policy"


class AuthorityLevel(str, Enum):
    """Capability level granted by a caller, never by repository input."""

    A0_BASELINE = "A0_baseline"
    A1_ARTIFACT = "A1_artifact"
    A2_INSTALLED_CODE = "A2_installed_code"
    A3_COMMAND = "A3_command"
    A4_PROTECTED_CONTENT = "A4_protected_content"
    A5_NETWORK_CREDENTIAL = "A5_network_credential"


class NetworkPolicy(str, Enum):
    """Declared network execution policy and enforcement posture."""

    NOT_APPLICABLE = "not_applicable"
    DENIED = "denied"
    CALLER_ISOLATED = "caller_isolated"
    ALLOWED = "allowed"
    UNSPECIFIED = "unspecified"


class ProviderToolIdentity(EvidenceModel):
    """Native tool identity retained independently from adapter identity."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    executable_digest: str | None = None


class ProviderScope(EvidenceModel):
    """Exact source and evidence scope declared by one provider run."""

    scope_id: str = Field(min_length=1)
    source_state_ids: list[str] = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    evidence_families: list[str] = Field(min_length=1)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, paths: list[str]) -> list[str]:
        for value in paths:
            validate_repository_path(value)
        return paths

    @field_validator("source_state_ids", "paths", "entity_ids", "evidence_families")
    @classmethod
    def reject_duplicate_scope_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("provider scope values must be unique")
        return values


class InvocationAuthority(EvidenceModel):
    """Caller-supplied authority actually used to obtain the artifact."""

    mode: InvocationMode
    level: AuthorityLevel
    policy_digest: str = Field(min_length=1)
    executable_identity: str | None = None
    environment_keys: list[str] = Field(default_factory=list)
    network_policy: NetworkPolicy = NetworkPolicy.NOT_APPLICABLE
    process_isolation: str = "not_applicable"

    @model_validator(mode="after")
    def validate_mode_authority(self) -> InvocationAuthority:
        required = {
            InvocationMode.BUILT_IN: AuthorityLevel.A0_BASELINE,
            InvocationMode.ARTIFACT_IMPORT: AuthorityLevel.A1_ARTIFACT,
            InvocationMode.INSTALLED_PROVIDER: AuthorityLevel.A2_INSTALLED_CODE,
        }
        expected = required.get(self.mode)
        if expected is not None and self.level is not expected:
            raise ValueError(f"{self.mode.value} requires authority {expected.value}")
        if self.mode in {InvocationMode.DIRECT_COMMAND, InvocationMode.EXTERNAL_POLICY} and self.level not in {
            AuthorityLevel.A3_COMMAND,
            AuthorityLevel.A5_NETWORK_CREDENTIAL,
        }:
            raise ValueError(f"{self.mode.value} requires command or network authority")
        if self.mode in {
            InvocationMode.BUILT_IN,
            InvocationMode.ARTIFACT_IMPORT,
            InvocationMode.INSTALLED_PROVIDER,
        }:
            if self.executable_identity is not None or self.environment_keys:
                raise ValueError(f"{self.mode.value} cannot declare executable or environment authority")
            if self.network_policy is not NetworkPolicy.NOT_APPLICABLE:
                raise ValueError(f"{self.mode.value} network policy must be not_applicable")
        return self


class ProviderEvidenceBatch(EvidenceModel):
    """Self-contained normalized evidence payload produced by one provider run."""

    locations: list[LocationRecord] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    edges: list[EdgeRecord] = Field(default_factory=list)
    contracts: list[ContractRecord] = Field(default_factory=list)
    candidates: list[CandidateRecord] = Field(default_factory=list)
    observations: list[ObservationRecord] = Field(default_factory=list)
    completeness: list[CompletenessRecord] = Field(min_length=1)
    limitations: list[LimitationRecord] = Field(default_factory=list)
    omissions: list[OmissionRecord] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    aliases: list[AliasRecord] = Field(default_factory=list)
    lineage: list[LineageRecord] = Field(default_factory=list)


class ProviderEnvelope(EvidenceModel):
    """Artifact-first provider interchange envelope."""

    artifact_type: Literal["anatomize.provider"] = PROVIDER_ENVELOPE_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = PROVIDER_ENVELOPE_SCHEMA_VERSION
    provider_api_version: Literal["1.0.0"] = PROVIDER_API_VERSION
    provider_run_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    tool: ProviderToolIdentity
    capabilities: list[str] = Field(min_length=1)
    languages: list[str] = Field(default_factory=list)
    repository_id: str = Field(min_length=1)
    source_states: list[SourceStateRecord] = Field(min_length=1)
    primary_source_state_id: str = Field(min_length=1)
    configuration_digest: str = Field(min_length=1)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scope: ProviderScope
    invocation: InvocationAuthority
    status: ProviderRunStatus
    payload: ProviderEvidenceBatch

    @model_validator(mode="after")
    def validate_envelope(self) -> ProviderEnvelope:
        state_ids = {state.state_id for state in self.source_states}
        if self.primary_source_state_id not in state_ids:
            raise ValueError("primary_source_state_id is not declared in source_states")
        if set(self.scope.source_state_ids).difference(state_ids):
            raise ValueError("provider scope references an undeclared source state")
        unknown_families = sorted(set(self.scope.evidence_families).difference(self.capabilities))
        if unknown_families:
            raise ValueError(f"provider scope requests undeclared capabilities: {unknown_families}")
        if any(state.repository_id != self.repository_id for state in self.source_states):
            raise ValueError("provider source states belong to another repository")
        expected_digest = provider_payload_digest(self.payload)
        if self.artifact_digest != expected_digest:
            raise ValueError(
                f"provider payload digest mismatch: declared {self.artifact_digest}, computed {expected_digest}"
            )

        limitation_ids = [item.limitation_id for item in self.payload.limitations]
        run = ProviderRunRecord(
            provider_run_id=self.provider_run_id,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_state_id=self.primary_source_state_id,
            configuration_digest=self.configuration_digest,
            method=f"{self.tool.name}:{self.tool.version}",
            capabilities=self.capabilities,
            status=self.status,
            limitation_ids=limitation_ids,
        )
        RepositoryEvidence(
            producer=EvidenceProducer(version=f"provider-validation:{PROVIDER_API_VERSION}"),
            repository_id=self.repository_id,
            states=self.source_states,
            provider_runs=[run],
            locations=self.payload.locations,
            entities=self.payload.entities,
            edges=self.payload.edges,
            contracts=self.payload.contracts,
            candidates=self.payload.candidates,
            observations=self.payload.observations,
            completeness=self.payload.completeness,
            limitations=self.payload.limitations,
            omissions=self.payload.omissions,
            conflicts=self.payload.conflicts,
            aliases=self.payload.aliases,
            lineage=self.payload.lineage,
        )
        entity_ids = {entity.entity_id for entity in self.payload.entities}
        unknown_scope_entities = sorted(set(self.scope.entity_ids).difference(entity_ids))
        if unknown_scope_entities:
            raise ValueError(f"provider scope references undeclared entities: {unknown_scope_entities}")
        covered_families = {
            family for completeness in self.payload.completeness for family in completeness.evidence_families
        }
        uncovered_families = sorted(set(self.scope.evidence_families).difference(covered_families))
        if uncovered_families:
            raise ValueError(f"provider scope lacks completeness records: {uncovered_families}")
        for observation in self.payload.observations:
            if observation.provider_run_id != self.provider_run_id:
                raise ValueError(f"observation {observation.observation_id} belongs to another provider run")
        for completeness in self.payload.completeness:
            if completeness.provider_run_id != self.provider_run_id:
                raise ValueError(f"completeness {completeness.completeness_id} belongs to another provider run")
        for limitation in self.payload.limitations:
            if limitation.provider_run_id not in {None, self.provider_run_id}:
                raise ValueError(f"limitation {limitation.limitation_id} belongs to another provider run")
        for omission in self.payload.omissions:
            if omission.provider_run_id not in {None, self.provider_run_id}:
                raise ValueError(f"omission {omission.omission_id} belongs to another provider run")
        for alias in self.payload.aliases:
            candidate_runs = {
                provider_run_id for candidate in alias.candidates for provider_run_id in candidate.provider_run_ids
            }
            if set(alias.provider_run_ids).union(candidate_runs).difference({self.provider_run_id}):
                raise ValueError(f"alias {alias.alias_id} contains another provider run")
        return self


class ProviderEnvelopeError(AnatomizeError):
    """Stable, actionable provider envelope failure."""

def build_provider_envelope(
    *,
    provider_run_id: str,
    provider_id: str,
    provider_version: str,
    tool: ProviderToolIdentity,
    capabilities: list[str],
    languages: list[str],
    repository_id: str,
    source_states: list[SourceStateRecord],
    primary_source_state_id: str,
    configuration_digest: str,
    scope: ProviderScope,
    invocation: InvocationAuthority,
    status: ProviderRunStatus,
    payload: ProviderEvidenceBatch,
) -> ProviderEnvelope:
    """Seal a validated batch with its deterministic payload digest."""
    return ProviderEnvelope(
        provider_run_id=provider_run_id,
        provider_id=provider_id,
        provider_version=provider_version,
        tool=tool,
        capabilities=sorted(set(capabilities)),
        languages=sorted(set(languages)),
        repository_id=repository_id,
        source_states=source_states,
        primary_source_state_id=primary_source_state_id,
        configuration_digest=configuration_digest,
        artifact_digest=provider_payload_digest(payload),
        scope=scope,
        invocation=invocation,
        status=status,
        payload=payload,
    )


def provider_envelope_evidence(envelope: ProviderEnvelope) -> RepositoryEvidence:
    """Project one validated provider envelope into the canonical evidence aggregate."""
    envelope_bytes = canonical_provider_bytes(envelope)
    native_artifact = ProviderArtifactRecord(
        artifact_id=content_id(
            "provider-envelope-artifact",
            {
                "provider_run_id": envelope.provider_run_id,
                "digest": sha256_digest(envelope_bytes),
            },
        ),
        digest=sha256_digest(envelope_bytes),
        media_type="application/vnd.anatomize.provider+json",
        schema_version=envelope.schema_version,
        byte_size=len(envelope_bytes),
        identity_verified=True,
    )
    run = ProviderRunRecord(
        provider_run_id=envelope.provider_run_id,
        provider_id=envelope.provider_id,
        provider_version=envelope.provider_version,
        source_state_id=envelope.primary_source_state_id,
        configuration_digest=envelope.configuration_digest,
        method=f"{envelope.tool.name}:{envelope.tool.version}",
        capabilities=envelope.capabilities,
        artifact_ids=[native_artifact.artifact_id],
        status=envelope.status,
        limitation_ids=[item.limitation_id for item in envelope.payload.limitations],
    )
    payload = envelope.payload
    return RepositoryEvidence(
        producer=EvidenceProducer(version=f"provider-import:{PROVIDER_API_VERSION}"),
        repository_id=envelope.repository_id,
        states=envelope.source_states,
        provider_artifacts=[native_artifact],
        provider_runs=[run],
        locations=payload.locations,
        entities=payload.entities,
        edges=payload.edges,
        contracts=payload.contracts,
        candidates=payload.candidates,
        observations=payload.observations,
        completeness=payload.completeness,
        limitations=payload.limitations,
        omissions=payload.omissions,
        conflicts=payload.conflicts,
        aliases=payload.aliases,
        lineage=payload.lineage,
    )


def provider_payload_digest(payload: ProviderEvidenceBatch) -> str:
    """Content identity of the normalized provider payload."""
    raw = compact_json(canonicalize_json(payload.model_dump(mode="json"))).encode("utf-8")
    return sha256_digest(raw)


def provider_cache_key(envelope: ProviderEnvelope) -> str:
    """Cache identity covering provider, source, configuration, artifact, and authority."""
    payload = {
        "provider_api_version": envelope.provider_api_version,
        "provider_id": envelope.provider_id,
        "provider_version": envelope.provider_version,
        "tool": envelope.tool.model_dump(mode="json"),
        "source_states": [state.model_dump(mode="json") for state in envelope.source_states],
        "configuration_digest": envelope.configuration_digest,
        "artifact_digest": envelope.artifact_digest,
        "scope": envelope.scope.model_dump(mode="json"),
        "invocation": envelope.invocation.model_dump(mode="json"),
    }
    return content_id("provider-cache", payload)


def canonical_provider_bytes(envelope: ProviderEnvelope) -> bytes:
    """Serialize a provider envelope deterministically."""
    return canonical_json_bytes(envelope.model_dump(mode="json"))


def provider_envelope_json_schema() -> dict[str, Any]:
    """Return the generated public JSON Schema for provider artifacts."""
    return ProviderEnvelope.model_json_schema(mode="serialization")
