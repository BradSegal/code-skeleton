"""Bounded data-only provider artifact ingestion and publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from anatomize._artifacts import (
    BoundedJsonError,
    JsonLimits,
    atomic_write_bytes,
    parse_bounded_json_object,
)
from anatomize.providers.models import (
    DEFAULT_MAX_PROVIDER_BYTES,
    PROVIDER_API_VERSION,
    PROVIDER_ENVELOPE_ARTIFACT_TYPE,
    PROVIDER_ENVELOPE_SCHEMA_VERSION,
    ProviderEnvelope,
    ProviderEnvelopeError,
    canonical_provider_bytes,
)


@dataclass(frozen=True)
class ProviderArtifactLimits:
    """Hard parser limits applied before an artifact becomes evidence."""

    max_bytes: int = DEFAULT_MAX_PROVIDER_BYTES
    max_depth: int = 64
    max_values: int = 500_000
    max_string_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_values, self.max_string_bytes) <= 0:
            raise ValueError("provider artifact limits must be positive")


def load_provider_envelope(
    path: Path,
    *,
    limits: ProviderArtifactLimits = ProviderArtifactLimits(),
) -> ProviderEnvelope:
    """Load a provider envelope as untrusted bytes without executing code."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ProviderEnvelopeError(
            "provider_artifact_unreadable",
            f"Cannot read provider artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
    if size > limits.max_bytes:
        raise ProviderEnvelopeError(
            "provider_artifact_too_large",
            f"Provider artifact is {size} bytes; limit is {limits.max_bytes} bytes",
            remediation="Regenerate a bounded artifact or increase the explicit trusted limit.",
        )
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ProviderEnvelopeError(
            "provider_artifact_unreadable",
            f"Cannot read provider artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
    return parse_provider_envelope(raw, limits=limits)


def parse_provider_envelope(
    raw: bytes,
    *,
    limits: ProviderArtifactLimits = ProviderArtifactLimits(),
) -> ProviderEnvelope:
    """Parse and validate one exact-version provider envelope."""
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(
                max_bytes=limits.max_bytes,
                max_depth=limits.max_depth,
                max_values=limits.max_values,
                max_string_bytes=limits.max_string_bytes,
            ),
        )
    except BoundedJsonError as error:
        raise _provider_json_error(error) from error

    artifact_type = payload.get("artifact_type")
    if artifact_type != PROVIDER_ENVELOPE_ARTIFACT_TYPE:
        raise ProviderEnvelopeError(
            "provider_artifact_type_incompatible",
            f"Expected artifact_type {PROVIDER_ENVELOPE_ARTIFACT_TYPE!r}; found {artifact_type!r}",
            remediation="Supply an Anatomize provider envelope, not a native or repository artifact.",
        )
    schema_version = payload.get("schema_version")
    if schema_version != PROVIDER_ENVELOPE_SCHEMA_VERSION:
        raise ProviderEnvelopeError(
            "provider_schema_incompatible",
            f"Expected provider schema {PROVIDER_ENVELOPE_SCHEMA_VERSION!r}; found {schema_version!r}",
            remediation="Regenerate the provider artifact with the current provider SDK.",
        )
    api_version = payload.get("provider_api_version")
    if api_version != PROVIDER_API_VERSION:
        raise ProviderEnvelopeError(
            "provider_api_incompatible",
            f"Expected provider API {PROVIDER_API_VERSION!r}; found {api_version!r}",
            remediation="Install a provider built for the current provider API.",
        )
    try:
        return ProviderEnvelope.model_validate(payload)
    except ValidationError as error:
        messages = " ".join(str(item.get("msg", "")) for item in error.errors())
        code = "provider_artifact_digest_mismatch" if "digest mismatch" in messages else "provider_artifact_invalid"
        raise ProviderEnvelopeError(
            code,
            f"Provider artifact failed validation ({error.error_count()} errors)",
            remediation="Inspect provider diagnostics and regenerate a complete source-bound artifact.",
        ) from error


def write_provider_envelope(envelope: ProviderEnvelope, path: Path) -> None:
    """Atomically write one validated canonical provider envelope."""
    atomic_write_bytes(path, canonical_provider_bytes(envelope))


def _provider_json_error(error: BoundedJsonError) -> ProviderEnvelopeError:
    codes = {
        "too_large": "provider_artifact_too_large",
        "corrupt": "provider_artifact_corrupt",
        "incomplete": "provider_artifact_incomplete",
        "depth_limit": "provider_artifact_depth_limit",
        "value_limit": "provider_artifact_value_limit",
        "string_limit": "provider_artifact_string_limit",
    }
    return ProviderEnvelopeError(
        codes[error.code],
        f"Provider {error}",
        remediation="Regenerate a bounded artifact or increase the explicit trusted limit.",
    )
