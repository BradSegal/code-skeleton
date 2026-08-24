"""Bounded loading, canonical serialization, and atomic evidence publication."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from anatomize._artifacts import (
    BoundedJsonError,
    JsonLimits,
    atomic_write_bytes,
    canonical_json_bytes,
    parse_bounded_json_object,
    sha256_digest,
)
from anatomize._errors import AnatomizeError
from anatomize.evidence.models import EVIDENCE_ARTIFACT_TYPE, EVIDENCE_SCHEMA_VERSION, RepositoryEvidence

DEFAULT_MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_EVIDENCE_DEPTH = 64
DEFAULT_MAX_EVIDENCE_VALUES = 2_000_000
DEFAULT_MAX_EVIDENCE_STRING_BYTES = 1_000_000


class EvidenceArtifactError(AnatomizeError):
    """Actionable artifact failure with a stable public error code."""

def evidence_json_schema() -> dict[str, Any]:
    """Return the generated public JSON Schema for the canonical artifact."""
    return RepositoryEvidence.model_json_schema(mode="serialization")


def load_evidence(path: Path, *, max_bytes: int = DEFAULT_MAX_EVIDENCE_BYTES) -> RepositoryEvidence:
    """Load one bounded current-schema evidence artifact."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise EvidenceArtifactError(
            "artifact_unreadable",
            f"Cannot read evidence artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
    if size > max_bytes:
        raise EvidenceArtifactError(
            "artifact_too_large",
            f"Evidence artifact is {size} bytes; limit is {max_bytes} bytes",
            remediation="Increase the explicit trusted limit or regenerate a bounded artifact.",
        )
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EvidenceArtifactError(
            "artifact_unreadable",
            f"Cannot read evidence artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
    return parse_evidence(raw, max_bytes=max_bytes)


def parse_evidence(
    raw: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_EVIDENCE_BYTES,
    max_depth: int = DEFAULT_MAX_EVIDENCE_DEPTH,
    max_values: int = DEFAULT_MAX_EVIDENCE_VALUES,
    max_string_bytes: int = DEFAULT_MAX_EVIDENCE_STRING_BYTES,
) -> RepositoryEvidence:
    """Parse, identify, version-check, and validate bounded evidence bytes."""
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(
                max_bytes=max_bytes,
                max_depth=max_depth,
                max_values=max_values,
                max_string_bytes=max_string_bytes,
            ),
        )
    except BoundedJsonError as error:
        raise _evidence_json_error(error) from error

    artifact_type = payload.get("artifact_type")
    if artifact_type != EVIDENCE_ARTIFACT_TYPE:
        rendered = repr(artifact_type) if artifact_type is not None else "missing"
        raise EvidenceArtifactError(
            "artifact_type_incompatible",
            f"Expected artifact_type {EVIDENCE_ARTIFACT_TYPE!r}; found {rendered}",
            remediation="Regenerate the repository evidence with the current Anatomize release.",
        )
    schema_version = payload.get("schema_version")
    if schema_version != EVIDENCE_SCHEMA_VERSION:
        rendered = repr(schema_version) if schema_version is not None else "missing"
        raise EvidenceArtifactError(
            "artifact_schema_incompatible",
            f"Expected evidence schema {EVIDENCE_SCHEMA_VERSION!r}; found {rendered}",
            remediation="Regenerate the repository evidence; legacy and future schemas are not interpreted.",
        )
    try:
        return RepositoryEvidence.model_validate(payload)
    except ValidationError as error:
        raise EvidenceArtifactError(
            "artifact_invalid",
            f"Evidence schema {schema_version} failed validation ({error.error_count()} errors)",
            remediation="Inspect provider diagnostics and regenerate a complete artifact.",
        ) from error


def canonical_evidence_bytes(evidence: RepositoryEvidence) -> bytes:
    """Serialize evidence deterministically independent of set-like input ordering."""
    return canonical_json_bytes(evidence.model_dump(mode="json"))


def write_evidence(evidence: RepositoryEvidence, path: Path) -> None:
    """Atomically publish a validated canonical evidence artifact."""
    atomic_write_bytes(path, canonical_evidence_bytes(evidence))


def evidence_digest(evidence: RepositoryEvidence) -> str:
    """Return the digest used by consumers and derived caches."""
    return sha256_digest(canonical_evidence_bytes(evidence))


def _evidence_json_error(error: BoundedJsonError) -> EvidenceArtifactError:
    codes = {
        "too_large": "artifact_too_large",
        "corrupt": "artifact_corrupt",
        "incomplete": "artifact_incomplete",
        "depth_limit": "artifact_depth_limit",
        "value_limit": "artifact_value_limit",
        "string_limit": "artifact_string_limit",
    }
    return EvidenceArtifactError(
        codes[error.code],
        f"Evidence {error}",
        remediation="Regenerate a bounded artifact or increase the explicit trusted limit.",
    )
