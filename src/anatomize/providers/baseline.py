"""Canonical provider envelope for the built-in deterministic repository index."""

from __future__ import annotations

from anatomize._artifacts import content_id
from anatomize.index import RepositoryIndex
from anatomize.providers.index_projection import repository_index_evidence
from anatomize.providers.models import (
    AuthorityLevel,
    InvocationAuthority,
    InvocationMode,
    NetworkPolicy,
    ProviderEnvelope,
    ProviderEvidenceBatch,
    ProviderScope,
    ProviderToolIdentity,
    build_provider_envelope,
)


def repository_index_provider_envelope(
    index: RepositoryIndex,
    *,
    repository_id: str,
    policy_digest: str,
) -> ProviderEnvelope:
    """Wrap built-in index evidence in the same transport used by optional providers."""
    evidence = repository_index_evidence(index, repository_id=repository_id)
    run = evidence.provider_runs[0]
    state = evidence.states[0]
    payload = ProviderEvidenceBatch(
        locations=evidence.locations,
        entities=evidence.entities,
        edges=evidence.edges,
        contracts=evidence.contracts,
        candidates=evidence.candidates,
        observations=evidence.observations,
        completeness=evidence.completeness,
        limitations=evidence.limitations,
        omissions=evidence.omissions,
        conflicts=evidence.conflicts,
        aliases=evidence.aliases,
        lineage=evidence.lineage,
    )
    return build_provider_envelope(
        provider_run_id=run.provider_run_id,
        provider_id=run.provider_id,
        provider_version=run.provider_version,
        tool=ProviderToolIdentity(name=run.provider_id, version=run.provider_version),
        capabilities=run.capabilities,
        languages=sorted({item.language for item in index.files if item.language}),
        repository_id=repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=run.configuration_digest,
        scope=ProviderScope(
            scope_id=content_id("provider-scope:repository-index", {"run": run.provider_run_id}),
            source_state_ids=[state.state_id],
            paths=sorted(item.path for item in index.files),
            entity_ids=sorted(item.entity_id for item in evidence.entities),
            evidence_families=run.capabilities,
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.BUILT_IN,
            level=AuthorityLevel.A0_BASELINE,
            policy_digest=policy_digest,
            network_policy=NetworkPolicy.NOT_APPLICABLE,
        ),
        status=run.status,
        payload=payload,
    )
