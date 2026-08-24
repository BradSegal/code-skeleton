"""Bounded, data-only SARIF 2.1.0 ingestion and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from anatomize._artifacts import BoundedJsonError, JsonLimits, atomic_write_bytes, parse_bounded_json_object
from anatomize._errors import AnatomizeError
from anatomize.diagnostics.models import (
    DEFAULT_MAX_SARIF_BYTES,
    SARIF_VERSION,
    SarifLog,
    canonical_sarif_bytes,
)


@dataclass(frozen=True)
class SarifArtifactLimits:
    """Hard parser limits applied before a SARIF log becomes evidence."""

    max_bytes: int = DEFAULT_MAX_SARIF_BYTES
    max_depth: int = 96
    max_values: int = 1_000_000
    max_string_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_values, self.max_string_bytes) <= 0:
            raise ValueError("SARIF artifact limits must be positive")


class SarifArtifactError(AnatomizeError):
    """Stable actionable SARIF ingestion failure."""

def parse_sarif_log(
    raw: bytes,
    *,
    limits: SarifArtifactLimits = SarifArtifactLimits(),
) -> SarifLog:
    """Parse a saved SARIF 2.1.0 report without loading or running its producer."""
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
        raise _sarif_json_error(error) from error
    version = payload.get("version")
    if version != SARIF_VERSION:
        raise SarifArtifactError(
            "sarif_version_incompatible",
            f"Expected SARIF {SARIF_VERSION!r}; found {version!r}",
            remediation="Export SARIF 2.1.0 from the analysis tool or converter.",
        )
    try:
        return SarifLog.model_validate(payload)
    except ValidationError as error:
        raise SarifArtifactError(
            "sarif_artifact_invalid",
            f"SARIF artifact failed validation ({error.error_count()} errors)",
            remediation="Validate the producer output against SARIF 2.1.0 and retry.",
        ) from error


def load_sarif_log(
    path: Path,
    *,
    limits: SarifArtifactLimits = SarifArtifactLimits(),
) -> SarifLog:
    """Load one SARIF log as untrusted bytes."""
    try:
        size = path.stat().st_size
        if size > limits.max_bytes:
            raise SarifArtifactError(
                "sarif_artifact_too_large",
                f"SARIF artifact is {size} bytes; limit is {limits.max_bytes} bytes",
                remediation="Produce a bounded report or increase the explicit trusted limit.",
            )
        raw = path.read_bytes()
    except SarifArtifactError:
        raise
    except OSError as error:
        raise SarifArtifactError(
            "sarif_artifact_unreadable",
            f"Cannot read SARIF artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
    return parse_sarif_log(raw, limits=limits)


def write_sarif_log(log: SarifLog, path: Path) -> None:
    """Atomically publish canonical SARIF while preserving array order."""
    atomic_write_bytes(path, canonical_sarif_bytes(log))


def _sarif_json_error(error: BoundedJsonError) -> SarifArtifactError:
    codes = {
        "too_large": "sarif_artifact_too_large",
        "corrupt": "sarif_artifact_corrupt",
        "incomplete": "sarif_artifact_incomplete",
        "depth_limit": "sarif_artifact_depth_limit",
        "value_limit": "sarif_artifact_value_limit",
        "string_limit": "sarif_artifact_string_limit",
    }
    return SarifArtifactError(
        codes[error.code],
        f"SARIF {error}",
        remediation="Produce a bounded report or increase the explicit trusted limit.",
    )
