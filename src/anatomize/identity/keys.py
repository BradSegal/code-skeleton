"""Typed, portable, source-bound identity keys."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, TypeAdapter, field_validator, model_validator

from anatomize.evidence import (
    ContentClass,
    EvidenceModel,
    EvidenceStrength,
    SourceRange,
    validate_repository_path,
)

RepositoryPath = Annotated[str, AfterValidator(validate_repository_path)]


class PathKind(str, Enum):
    """Filesystem identity behavior, kept distinct from content classification."""

    REGULAR = "regular"
    SYMLINK = "symlink"
    GENERATED = "generated"
    VENDORED = "vendored"


class SymbolRole(str, Enum):
    """Declaration role needed to avoid false symbol collapse."""

    DEFINITION = "definition"
    DECLARATION = "declaration"
    REEXPORT = "reexport"
    ALIAS = "alias"
    OVERLOAD = "overload"
    METHOD = "method"


class RepositoryIdentityKey(EvidenceModel):
    identity_type: Literal["repository"] = "repository"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)


class FileIdentityKey(EvidenceModel):
    identity_type: Literal["file"] = "file"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    path_kind: PathKind = PathKind.REGULAR
    content_class: ContentClass = ContentClass.ORDINARY
    symlink_target: str | None = None

    @field_validator("symlink_target")
    @classmethod
    def validate_symlink_target(cls, value: str | None) -> str | None:
        return validate_repository_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_symlink(self) -> FileIdentityKey:
        if (
            self.path_kind is PathKind.SYMLINK
            and self.symlink_target is None
            and self.content_class not in {ContentClass.EXTERNAL, ContentClass.OPAQUE, ContentClass.SENSITIVE}
        ):
            raise ValueError("contained symlink identities require a portable target")
        if self.path_kind is not PathKind.SYMLINK and self.symlink_target is not None:
            raise ValueError("only symlink identities can declare a target")
        return self


class SymbolIdentityKey(EvidenceModel):
    identity_type: Literal["symbol"] = "symbol"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    language: str = Field(min_length=1)
    symbol_kind: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    role: SymbolRole
    signature: str | None = None
    declaration_ordinal: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_overload_signature(self) -> SymbolIdentityKey:
        if self.role is SymbolRole.OVERLOAD and self.signature is None:
            raise ValueError("overload identities require a signature")
        return self


class AnonymousScopeIdentityKey(EvidenceModel):
    identity_type: Literal["anonymous_scope"] = "anonymous_scope"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    language: str = Field(min_length=1)
    scope_kind: str = Field(min_length=1)
    parent_identity_id: str = Field(min_length=1)
    source_range: SourceRange
    ordinal: int = Field(ge=0)


class NotebookCellIdentityKey(EvidenceModel):
    identity_type: Literal["notebook_cell"] = "notebook_cell"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    cell_id: str = Field(min_length=1)
    cell_index: int = Field(ge=0)
    source_digest: str = Field(min_length=1)
    language: str = Field(min_length=1)


class EmbeddedRegionIdentityKey(EvidenceModel):
    identity_type: Literal["embedded_region"] = "embedded_region"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    region_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    host_range: SourceRange


class GeneratedIdentityKey(EvidenceModel):
    identity_type: Literal["generated"] = "generated"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    generator_identity_id: str = Field(min_length=1)
    source_identity_ids: list[str] = Field(min_length=1)
    mapping_strength: EvidenceStrength

    @field_validator("source_identity_ids")
    @classmethod
    def reject_duplicate_sources(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("generated identity sources must be unique")
        return sorted(values)


class WorkflowIdentityKey(EvidenceModel):
    identity_type: Literal["workflow"] = "workflow"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    path: RepositoryPath
    workflow_kind: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)


class ExternalPackageIdentityKey(EvidenceModel):
    identity_type: Literal["external_package"] = "external_package"
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    ecosystem: str = Field(min_length=1)
    package: str = Field(min_length=1)
    version: str | None = None
    symbol: str | None = None


IdentityKey = Annotated[
    RepositoryIdentityKey
    | FileIdentityKey
    | SymbolIdentityKey
    | AnonymousScopeIdentityKey
    | NotebookCellIdentityKey
    | EmbeddedRegionIdentityKey
    | GeneratedIdentityKey
    | WorkflowIdentityKey
    | ExternalPackageIdentityKey,
    Field(discriminator="identity_type"),
]

_IDENTITY_ADAPTER: TypeAdapter[IdentityKey] = TypeAdapter(IdentityKey)


def parse_identity_key(value: dict[str, Any]) -> IdentityKey:
    """Validate one discriminated public identity key."""
    return _IDENTITY_ADAPTER.validate_python(value)


def canonical_identity_id(key: IdentityKey) -> str:
    """Return a deterministic opaque ID from the complete typed key."""
    payload = key.model_dump(mode="json")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return f"identity:{key.identity_type}:sha256:{hashlib.sha256(raw).hexdigest()}"
