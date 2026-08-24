"""Bounded parsing and atomic publication for comparison artifacts."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from anatomize._artifacts import BoundedJsonError, JsonLimits, atomic_write_bytes, parse_bounded_json_object
from anatomize._errors import AnatomizeError
from anatomize.temporal.models import (
    COMPARISON_ARTIFACT_TYPE,
    COMPARISON_SCHEMA_VERSION,
    RepositoryComparison,
    canonical_comparison_bytes,
)

DEFAULT_MAX_COMPARISON_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_COMPARISON_DEPTH = 64
DEFAULT_MAX_COMPARISON_VALUES = 1_000_000
DEFAULT_MAX_COMPARISON_STRING_BYTES = 1_000_000


class ComparisonArtifactError(AnatomizeError):
    """Stable comparison artifact error with safe remediation."""

def parse_comparison(
    raw: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_COMPARISON_BYTES,
    max_depth: int = DEFAULT_MAX_COMPARISON_DEPTH,
    max_values: int = DEFAULT_MAX_COMPARISON_VALUES,
    max_string_bytes: int = DEFAULT_MAX_COMPARISON_STRING_BYTES,
) -> RepositoryComparison:
    """Parse one exact comparison artifact without executing repository content."""
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
        raise _comparison_json_error(error) from error
    if payload.get("artifact_type") != COMPARISON_ARTIFACT_TYPE:
        raise ComparisonArtifactError(
            "comparison_artifact_type_incompatible",
            f"Expected artifact type {COMPARISON_ARTIFACT_TYPE!r}",
            remediation="Supply an Anatomize comparison artifact.",
        )
    if payload.get("schema_version") != COMPARISON_SCHEMA_VERSION:
        raise ComparisonArtifactError(
            "comparison_schema_incompatible",
            f"Expected comparison schema {COMPARISON_SCHEMA_VERSION!r}",
            remediation="Regenerate both state manifests and the comparison with the current library.",
        )
    try:
        return RepositoryComparison.model_validate(payload)
    except ValidationError as error:
        raise ComparisonArtifactError(
            "comparison_artifact_invalid",
            f"Comparison artifact failed validation ({error.error_count()} errors)",
            remediation="Inspect boundary issues and regenerate from exact evidence artifacts.",
        ) from error


def load_comparison(path: Path, *, max_bytes: int = DEFAULT_MAX_COMPARISON_BYTES) -> RepositoryComparison:
    """Load one bounded comparison artifact from disk."""
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise ComparisonArtifactError(
                "comparison_artifact_too_large",
                f"Comparison artifact is {size} bytes; limit is {max_bytes} bytes",
                remediation="Split the comparison scope or raise an explicit trusted limit.",
            )
        return parse_comparison(path.read_bytes(), max_bytes=max_bytes)
    except ComparisonArtifactError:
        raise
    except OSError as error:
        raise ComparisonArtifactError(
            "comparison_artifact_unreadable",
            f"Cannot read comparison artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error


def write_comparison(comparison: RepositoryComparison, path: Path) -> None:
    """Atomically publish one canonical comparison artifact."""
    atomic_write_bytes(path, canonical_comparison_bytes(comparison))


def _comparison_json_error(error: BoundedJsonError) -> ComparisonArtifactError:
    codes = {
        "too_large": "comparison_artifact_too_large",
        "corrupt": "comparison_artifact_corrupt",
        "incomplete": "comparison_artifact_incomplete",
        "depth_limit": "comparison_artifact_depth_limit",
        "value_limit": "comparison_artifact_value_limit",
        "string_limit": "comparison_artifact_string_limit",
    }
    return ComparisonArtifactError(
        codes[error.code],
        f"Comparison {error}",
        remediation="Regenerate a bounded comparison or increase the explicit trusted limit.",
    )
