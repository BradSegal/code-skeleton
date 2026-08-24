"""Bounded data-only parsing and atomic publication of semantic captures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from anatomize._artifacts import BoundedJsonError, JsonLimits, atomic_write_bytes, parse_bounded_json_object
from anatomize._errors import AnatomizeError
from anatomize.semantic.models import (
    DEFAULT_MAX_LSP_SEMANTIC_BYTES,
    LSP_SEMANTIC_ARTIFACT_TYPE,
    LSP_SEMANTIC_SCHEMA_VERSION,
    LspSemanticArtifact,
    canonical_lsp_semantic_bytes,
)


@dataclass(frozen=True)
class LspSemanticArtifactLimits:
    """Hard resource bounds applied before semantic artifact validation."""

    max_bytes: int = DEFAULT_MAX_LSP_SEMANTIC_BYTES
    max_depth: int = 64
    max_values: int = 750_000
    max_string_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_values, self.max_string_bytes) <= 0:
            raise ValueError("semantic artifact limits must be positive")


class LspSemanticArtifactError(AnatomizeError):
    """Stable actionable semantic-artifact failure."""

def parse_lsp_semantic_artifact(
    raw: bytes,
    *,
    limits: LspSemanticArtifactLimits = LspSemanticArtifactLimits(),
) -> LspSemanticArtifact:
    """Parse one exact-version semantic artifact without executing provider code."""
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
        raise _semantic_json_error(error) from error
    artifact_type = payload.get("artifact_type")
    if artifact_type != LSP_SEMANTIC_ARTIFACT_TYPE:
        raise LspSemanticArtifactError(
            "semantic_artifact_type_incompatible",
            f"Expected artifact_type {LSP_SEMANTIC_ARTIFACT_TYPE!r}; found {artifact_type!r}",
            remediation="Supply a captured Anatomize LSP semantic artifact.",
        )
    schema_version = payload.get("schema_version")
    if schema_version != LSP_SEMANTIC_SCHEMA_VERSION:
        raise LspSemanticArtifactError(
            "semantic_schema_incompatible",
            f"Expected semantic schema {LSP_SEMANTIC_SCHEMA_VERSION!r}; found {schema_version!r}",
            remediation="Regenerate the capture with the current semantic artifact SDK.",
        )
    try:
        return LspSemanticArtifact.model_validate(payload)
    except ValidationError as error:
        raise LspSemanticArtifactError(
            "semantic_artifact_invalid",
            f"Semantic artifact failed validation ({error.error_count()} errors)",
            remediation="Inspect the capture producer and regenerate a complete source-bound artifact.",
        ) from error


def load_lsp_semantic_artifact(
    path: Path,
    *,
    limits: LspSemanticArtifactLimits = LspSemanticArtifactLimits(),
) -> LspSemanticArtifact:
    """Load one semantic artifact as untrusted bytes."""
    try:
        size = path.stat().st_size
        if size > limits.max_bytes:
            raise LspSemanticArtifactError(
                "semantic_artifact_too_large",
                f"Semantic artifact is {size} bytes; limit is {limits.max_bytes} bytes",
                remediation="Regenerate a bounded capture or increase the explicit trusted limit.",
            )
        raw = path.read_bytes()
    except LspSemanticArtifactError:
        raise
    except OSError as error:
        raise LspSemanticArtifactError(
            "semantic_artifact_unreadable",
            f"Cannot read semantic artifact: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
    return parse_lsp_semantic_artifact(raw, limits=limits)


def write_lsp_semantic_artifact(artifact: LspSemanticArtifact, path: Path) -> None:
    """Atomically write one validated canonical semantic artifact."""
    atomic_write_bytes(path, canonical_lsp_semantic_bytes(artifact))


def _semantic_json_error(error: BoundedJsonError) -> LspSemanticArtifactError:
    codes = {
        "too_large": "semantic_artifact_too_large",
        "corrupt": "semantic_artifact_corrupt",
        "incomplete": "semantic_artifact_incomplete",
        "depth_limit": "semantic_artifact_depth_limit",
        "value_limit": "semantic_artifact_value_limit",
        "string_limit": "semantic_artifact_string_limit",
    }
    return LspSemanticArtifactError(
        codes[error.code],
        f"Semantic {error}",
        remediation="Regenerate a bounded capture or increase the explicit trusted limit.",
    )
