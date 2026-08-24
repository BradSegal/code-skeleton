"""Public application results shared by Python, CLI, and optional transports."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from anatomize._artifacts import content_id
from anatomize._errors import AnatomizeError
from anatomize.dossiers import Dossier, DossierRequest
from anatomize.evidence import EvidenceModel

DOSSIER_EXCHANGE_ARTIFACT_TYPE: Literal["anatomize.dossier-exchange"] = (
    "anatomize.dossier-exchange"
)
DOSSIER_EXCHANGE_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
ARTIFACT_CHECK_TYPE: Literal["anatomize.artifact-check"] = "anatomize.artifact-check"
ARTIFACT_CHECK_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class ReviewApplicationError(AnatomizeError):
    """Stable, actionable failure across every public application interface."""

class DossierExchange(EvidenceModel):
    """One exact request and its answer, retained together for review and expansion."""

    artifact_type: Literal["anatomize.dossier-exchange"] = DOSSIER_EXCHANGE_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = DOSSIER_EXCHANGE_SCHEMA_VERSION
    exchange_id: str = Field(min_length=1)
    request: DossierRequest
    dossier: Dossier

    @model_validator(mode="after")
    def validate_exchange(self) -> DossierExchange:
        if self.dossier.request_id != self.request.request_id:
            raise ValueError("dossier exchange request and response identities differ")
        if (
            self.dossier.session_id,
            self.dossier.session_manifest_digest,
        ) != (
            self.request.session_id,
            self.request.session_manifest_digest,
        ):
            raise ValueError("dossier exchange request and response sessions differ")
        expected = content_id(
            "dossier-exchange",
            {
                "request_id": self.request.request_id,
                "dossier_id": self.dossier.dossier_id,
            },
        )
        if self.exchange_id != expected:
            raise ValueError("dossier exchange identifier does not match its content")
        return self


class ArtifactCheck(EvidenceModel):
    """Machine-readable successful artifact validation result."""

    artifact_type: Literal["anatomize.artifact-check"] = ARTIFACT_CHECK_TYPE
    schema_version: Literal["1.0.0"] = ARTIFACT_CHECK_SCHEMA_VERSION
    checked_artifact_type: str
    checked_schema_version: str
    identity: str
    valid: Literal[True] = True
    message: str = "Artifact is valid and internally consistent."


def build_dossier_exchange(request: DossierRequest, dossier: Dossier) -> DossierExchange:
    """Bind one exact request and response without inventing a second result schema."""
    return DossierExchange(
        exchange_id=content_id(
            "dossier-exchange",
            {"request_id": request.request_id, "dossier_id": dossier.dossier_id},
        ),
        request=request,
        dossier=dossier,
    )
