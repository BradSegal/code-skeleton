"""Bounded dossier parsing, schemas, and atomic deterministic publication."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from anatomize._artifacts import BoundedJsonError, JsonLimits, atomic_write_bytes, parse_bounded_json_object
from anatomize._errors import AnatomizeError
from anatomize.dossiers.models import (
    DOSSIER_ARTIFACT_TYPE,
    DOSSIER_REQUEST_ARTIFACT_TYPE,
    DOSSIER_REQUEST_SCHEMA_VERSION,
    DOSSIER_SCHEMA_VERSION,
    Dossier,
    DossierRequest,
    canonical_dossier_bytes,
)

DEFAULT_MAX_DOSSIER_REQUEST_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_DOSSIER_BYTES = 64 * 1024 * 1024

_ModelT = TypeVar("_ModelT", DossierRequest, Dossier)


class DossierArtifactError(AnatomizeError):
    """Stable bounded-artifact failure with explicit remediation."""

def dossier_request_json_schema() -> dict[str, Any]:
    """Return the generated public request schema."""
    return DossierRequest.model_json_schema(mode="serialization")


def dossier_json_schema() -> dict[str, Any]:
    """Return the generated public response schema."""
    return Dossier.model_json_schema(mode="serialization")


def parse_dossier_request(raw: bytes, *, max_bytes: int = DEFAULT_MAX_DOSSIER_REQUEST_BYTES) -> DossierRequest:
    """Parse one bounded current-version dossier request."""
    return _parse(
        raw,
        model=DossierRequest,
        artifact_type=DOSSIER_REQUEST_ARTIFACT_TYPE,
        schema_version=DOSSIER_REQUEST_SCHEMA_VERSION,
        max_bytes=max_bytes,
        label="dossier request",
    )


def parse_dossier(raw: bytes, *, max_bytes: int = DEFAULT_MAX_DOSSIER_BYTES) -> Dossier:
    """Parse one bounded current-version dossier and verify exact byte accounting."""
    dossier = _parse(
        raw,
        model=Dossier,
        artifact_type=DOSSIER_ARTIFACT_TYPE,
        schema_version=DOSSIER_SCHEMA_VERSION,
        max_bytes=max_bytes,
        label="dossier",
    )
    canonical_size = len(canonical_dossier_bytes(dossier))
    if dossier.budget_use.payload_bytes.used != canonical_size:
        raise DossierArtifactError(
            "dossier_payload_accounting_invalid",
            "Dossier payload byte use does not match its canonical serialization",
            remediation="Regenerate the dossier with the current Anatomize release.",
        )
    return dossier


def load_dossier_request(path: Path, *, max_bytes: int = DEFAULT_MAX_DOSSIER_REQUEST_BYTES) -> DossierRequest:
    """Read one bounded dossier request from a local artifact path."""
    return parse_dossier_request(_read(path, max_bytes=max_bytes, label="dossier request"), max_bytes=max_bytes)


def load_dossier(path: Path, *, max_bytes: int = DEFAULT_MAX_DOSSIER_BYTES) -> Dossier:
    """Read one bounded dossier from a local artifact path."""
    return parse_dossier(_read(path, max_bytes=max_bytes, label="dossier"), max_bytes=max_bytes)


def write_dossier_request(request: DossierRequest, path: Path) -> None:
    """Atomically write canonical ordered request JSON."""
    from anatomize._artifacts import canonical_ordered_json_bytes

    atomic_write_bytes(path, canonical_ordered_json_bytes(request.model_dump(mode="json")))


def write_dossier(dossier: Dossier, path: Path) -> None:
    """Atomically write canonical ordered dossier JSON."""
    atomic_write_bytes(path, canonical_dossier_bytes(dossier))


def _parse(
    raw: bytes,
    *,
    model: type[_ModelT],
    artifact_type: str,
    schema_version: str,
    max_bytes: int,
    label: str,
) -> _ModelT:
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(
                max_bytes=max_bytes,
                max_depth=64,
                max_values=1_000_000,
                max_string_bytes=max_bytes,
            ),
        )
    except BoundedJsonError as error:
        raise DossierArtifactError(
            f"{label.replace(' ', '_')}_{error.code}",
            f"{label.title()} {error}",
            remediation="Regenerate a bounded artifact or raise an explicit trusted limit.",
        ) from error
    if payload.get("artifact_type") != artifact_type:
        raise DossierArtifactError(
            f"{label.replace(' ', '_')}_type_incompatible",
            f"Expected artifact type {artifact_type!r}",
            remediation=f"Supply a current Anatomize {label} artifact.",
        )
    if payload.get("schema_version") != schema_version:
        raise DossierArtifactError(
            f"{label.replace(' ', '_')}_schema_incompatible",
            f"Expected schema version {schema_version!r}",
            remediation=f"Regenerate the {label}; legacy and future schemas are not interpreted.",
        )
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise DossierArtifactError(
            f"{label.replace(' ', '_')}_invalid",
            f"{label.title()} failed validation ({error.error_count()} errors)",
            remediation=f"Inspect and regenerate the {label}.",
        ) from error


def _read(path: Path, *, max_bytes: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise DossierArtifactError(
                f"{label.replace(' ', '_')}_too_large",
                f"{label.title()} is {size} bytes; limit is {max_bytes}",
                remediation="Raise the explicit trusted limit or regenerate a bounded artifact.",
            )
        return path.read_bytes()
    except DossierArtifactError:
        raise
    except OSError as error:
        raise DossierArtifactError(
            f"{label.replace(' ', '_')}_unreadable",
            f"Cannot read {label}: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error
