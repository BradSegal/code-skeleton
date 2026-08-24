"""Bounded parsing and atomic publication for public review artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from anatomize._artifacts import (
    BoundedJsonError,
    JsonLimits,
    atomic_write_bytes,
    canonical_ordered_json_bytes,
    parse_bounded_json_object,
    sha256_digest,
)
from anatomize.dossiers import Dossier, DossierRequest
from anatomize.lifecycle import (
    ChangeDossier,
    ClosureReport,
    ConsolidationDossier,
    DecisionOverlay,
    ImplementationIntent,
    OverlayEvaluation,
    SimilarityArtifact,
)
from anatomize.review.models import ArtifactCheck, DossierExchange, ReviewApplicationError
from anatomize.sessions import ReviewSessionBundle
from anatomize.temporal import RepositoryComparison

DEFAULT_MAX_REVIEW_ARTIFACT_BYTES = 128 * 1024 * 1024

_ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "anatomize.session-bundle": ReviewSessionBundle,
    "anatomize.dossier-request": DossierRequest,
    "anatomize.dossier": Dossier,
    "anatomize.dossier-exchange": DossierExchange,
    "anatomize.artifact-check": ArtifactCheck,
    "anatomize.comparison": RepositoryComparison,
    "anatomize.similarity": SimilarityArtifact,
    "anatomize.consolidation-dossier": ConsolidationDossier,
    "anatomize.decision-overlay": DecisionOverlay,
    "anatomize.overlay-evaluation": OverlayEvaluation,
    "anatomize.change-dossier": ChangeDossier,
    "anatomize.implementation-intent": ImplementationIntent,
    "anatomize.closure-report": ClosureReport,
}


def parse_review_artifact(
    raw: bytes,
    *,
    expected_type: str | None = None,
    max_bytes: int = DEFAULT_MAX_REVIEW_ARTIFACT_BYTES,
) -> BaseModel:
    """Parse one exact current-schema public artifact without compatibility guessing."""
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(
                max_bytes=max_bytes,
                max_depth=64,
                max_values=2_000_000,
                # Session bundles intentionally carry base64-encoded artifacts.
                # The whole-artifact byte bound is the governing resource limit;
                # a lower per-string cap makes writer-approved bundles unreadable.
                max_string_bytes=max_bytes,
            ),
        )
    except BoundedJsonError as error:
        raise ReviewApplicationError(
            f"review_artifact_{error.code}",
            f"Review artifact {error}",
            remediation="Regenerate a bounded artifact with the current Anatomize release.",
        ) from error
    artifact_type = payload.get("artifact_type")
    if not isinstance(artifact_type, str) or artifact_type not in _ARTIFACT_MODELS:
        raise ReviewApplicationError(
            "review_artifact_type_unsupported",
            f"Unsupported review artifact type: {artifact_type!r}",
            remediation="Supply a current Anatomize session, dossier, or lifecycle artifact.",
        )
    if expected_type is not None and artifact_type != expected_type:
        raise ReviewApplicationError(
            "review_artifact_type_mismatch",
            f"Expected {expected_type!r}; found {artifact_type!r}",
            remediation="Supply the artifact type required by this operation.",
        )
    try:
        return _ARTIFACT_MODELS[artifact_type].model_validate(payload)
    except ValidationError as error:
        raise ReviewApplicationError(
            "review_artifact_invalid",
            f"{artifact_type} failed validation ({error.error_count()} errors)",
            remediation="Inspect the schema, identities, and digests, then regenerate the artifact.",
        ) from error


def load_review_artifact(
    path: Path,
    *,
    expected_type: str | None = None,
    max_bytes: int = DEFAULT_MAX_REVIEW_ARTIFACT_BYTES,
) -> BaseModel:
    """Read and validate one bounded public review artifact."""
    try:
        if path.stat().st_size > max_bytes:
            raise ReviewApplicationError(
                "review_artifact_too_large",
                f"Review artifact exceeds the {max_bytes}-byte limit: {path.name}",
                remediation="Use a narrower dossier or raise an explicit trusted limit.",
            )
        raw = path.read_bytes()
    except ReviewApplicationError:
        raise
    except OSError as error:
        raise ReviewApplicationError(
            "review_artifact_unreadable",
            f"Cannot read review artifact: {path.name}",
            remediation="Check the path and permissions, then retry.",
        ) from error
    return parse_review_artifact(raw, expected_type=expected_type, max_bytes=max_bytes)


def canonical_review_bytes(artifact: BaseModel) -> bytes:
    """Return canonical bytes for any validated public review artifact."""
    return canonical_ordered_json_bytes(artifact.model_dump(mode="json"))


def write_review_artifact(artifact: BaseModel, path: Path) -> None:
    """Atomically write one validated public review artifact."""
    atomic_write_bytes(path, canonical_review_bytes(artifact))


def artifact_identity(artifact: BaseModel) -> str:
    """Select the stable primary identity of any supported review artifact."""
    for name in (
        "identity",
        "bundle_id",
        "exchange_id",
        "request_id",
        "dossier_id",
        "comparison_id",
        "artifact_id",
        "evaluation_id",
        "decision_id",
        "intent_id",
        "report_id",
    ):
        value = getattr(artifact, name, None)
        if isinstance(value, str):
            return value
    if isinstance(artifact, SimilarityArtifact):
        return "similarity:" + sha256_digest(canonical_review_bytes(artifact)).removeprefix("sha256:")
    raise ReviewApplicationError(
        "review_artifact_identity_missing",
        "Validated artifact has no supported primary identity",
        remediation="Regenerate the artifact with the current Anatomize release.",
    )


def json_object(path: Path, *, max_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    """Load a bounded auxiliary JSON object used by explicit lifecycle commands."""
    try:
        raw = path.read_bytes()
        value = parse_bounded_json_object(
            raw,
            limits=JsonLimits(max_bytes=max_bytes, max_depth=32, max_values=100_000, max_string_bytes=1_000_000),
        )
    except (OSError, BoundedJsonError) as error:
        raise ReviewApplicationError(
            "review_input_invalid",
            f"Cannot load bounded JSON input: {path.name}",
            remediation="Supply one valid JSON object within the documented limits.",
        ) from error
    return value
