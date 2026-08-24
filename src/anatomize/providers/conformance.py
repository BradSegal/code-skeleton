"""Reusable black-box provider conformance harness."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from anatomize.providers.io import ProviderArtifactLimits, parse_provider_envelope
from anatomize.providers.models import (
    ProviderEnvelope,
    ProviderEnvelopeError,
    ProviderEvidenceBatch,
    canonical_provider_bytes,
    provider_cache_key,
    provider_payload_digest,
)


@dataclass(frozen=True)
class ProviderConformanceExpectation:
    """Identity and state a provider claims to implement."""

    provider_id: str
    repository_id: str
    source_state_ids: frozenset[str]


@dataclass(frozen=True)
class ProviderConformanceCheck:
    """One independently reported conformance property."""

    check_id: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ProviderConformanceReport:
    """Complete provider qualification report without an aggregate score."""

    provider_id: str
    checks: tuple[ProviderConformanceCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def run_provider_conformance(
    produce: Callable[[], bytes],
    *,
    expectation: ProviderConformanceExpectation,
    failure_probe: Callable[[], object] | None = None,
) -> ProviderConformanceReport:
    """Challenge one provider producer at deterministic and hostile boundaries."""
    checks: list[ProviderConformanceCheck] = []
    try:
        first_raw = produce()
        second_raw = produce()
    except Exception as error:
        return ProviderConformanceReport(
            provider_id=expectation.provider_id,
            checks=(
                ProviderConformanceCheck(
                    check_id="provider_production",
                    passed=False,
                    detail=f"provider raised {type(error).__name__}",
                ),
            ),
        )
    checks.append(
        ProviderConformanceCheck(
            check_id="deterministic_bytes",
            passed=first_raw == second_raw,
            detail="two identical requests produced byte-identical artifacts",
        )
    )
    try:
        first = parse_provider_envelope(first_raw)
        second = parse_provider_envelope(second_raw)
    except ProviderEnvelopeError as error:
        checks.append(
            ProviderConformanceCheck(
                check_id="current_envelope",
                passed=False,
                detail=f"{error.code}: {error}",
            )
        )
        return ProviderConformanceReport(expectation.provider_id, tuple(checks))
    checks.append(
        ProviderConformanceCheck(
            check_id="current_envelope",
            passed=True,
            detail="artifact type, schema, API, digest, records, coordinates and references validate",
        )
    )
    state_ids = {state.state_id for state in first.source_states}
    checks.extend(
        [
            ProviderConformanceCheck(
                check_id="provider_identity",
                passed=first.provider_id == expectation.provider_id,
                detail=f"observed provider_id {first.provider_id}",
            ),
            ProviderConformanceCheck(
                check_id="source_binding",
                passed=(
                    first.repository_id == expectation.repository_id and state_ids == set(expectation.source_state_ids)
                ),
                detail=f"repository {first.repository_id}; states {sorted(state_ids)}",
            ),
            ProviderConformanceCheck(
                check_id="canonical_serialization",
                passed=canonical_provider_bytes(first) == canonical_provider_bytes(second),
                detail="validated envelopes canonicalize identically",
            ),
            ProviderConformanceCheck(
                check_id="cache_identity",
                passed=(
                    provider_cache_key(first) == provider_cache_key(second)
                    and provider_cache_key(first)
                    != provider_cache_key(first.model_copy(update={"configuration_digest": "different"}))
                ),
                detail="identical evidence reuses a key and configuration drift changes it",
            ),
        ]
    )
    checks.extend(_hostile_checks(first_raw, first))
    if failure_probe is not None:
        failed_as_expected = False
        try:
            failure_probe()
        except Exception:
            failed_as_expected = True
        recovered = False
        if failed_as_expected:
            try:
                recovered = produce() == first_raw
            except Exception:
                recovered = False
        checks.append(
            ProviderConformanceCheck(
                check_id="failure_isolation",
                passed=failed_as_expected and recovered,
                detail="a failed attempt did not change the next deterministic artifact",
            )
        )
    return ProviderConformanceReport(expectation.provider_id, tuple(checks))


def _hostile_checks(raw: bytes, envelope: ProviderEnvelope) -> list[ProviderConformanceCheck]:
    payload = json.loads(raw)
    results: list[ProviderConformanceCheck] = []
    results.append(_expect_error("corrupt_json", b"{not-json", "provider_artifact_corrupt"))

    incompatible = dict(payload)
    incompatible["schema_version"] = "2.0.0"
    results.append(
        _expect_error(
            "incompatible_schema",
            json.dumps(incompatible).encode(),
            "provider_schema_incompatible",
        )
    )

    tampered = json.loads(raw)
    tampered["payload"]["entities"][0]["display_name"] = "tampered"
    results.append(
        _expect_error(
            "artifact_digest",
            json.dumps(tampered).encode(),
            "provider_artifact_digest_mismatch",
        )
    )

    escaped = json.loads(raw)
    escaped["scope"]["paths"] = ["../outside"]
    results.append(
        _expect_error(
            "coordinate_containment",
            json.dumps(escaped).encode(),
            "provider_artifact_invalid",
        )
    )

    invalid_conflict = json.loads(raw)
    invalid_conflict["payload"]["conflicts"] = [
        {
            "record_type": "conflict",
            "conflict_id": "conflict:invalid",
            "source_state_id": envelope.primary_source_state_id,
            "target_type": "edge",
            "target_id": "edge:missing",
            "observation_ids": ["observation:missing-a", "observation:missing-b"],
            "status": "unresolved",
            "summary": "invalid conformance conflict",
        }
    ]
    batch = ProviderEvidenceBatch.model_validate(invalid_conflict["payload"])
    invalid_conflict["artifact_digest"] = provider_payload_digest(batch)
    results.append(
        _expect_error(
            "conflict_references",
            json.dumps(invalid_conflict).encode(),
            "provider_artifact_invalid",
        )
    )

    results.append(
        _expect_error(
            "value_limit",
            raw,
            "provider_artifact_value_limit",
            limits=ProviderArtifactLimits(max_values=2),
        )
    )
    return results


def _expect_error(
    check_id: str,
    raw: bytes,
    expected_code: str,
    *,
    limits: ProviderArtifactLimits = ProviderArtifactLimits(),
) -> ProviderConformanceCheck:
    try:
        parse_provider_envelope(raw, limits=limits)
    except ProviderEnvelopeError as error:
        return ProviderConformanceCheck(
            check_id=check_id,
            passed=error.code == expected_code,
            detail=f"observed {error.code}; expected {expected_code}",
        )
    return ProviderConformanceCheck(
        check_id=check_id,
        passed=False,
        detail=f"malicious artifact was accepted; expected {expected_code}",
    )
