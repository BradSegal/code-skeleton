"""Exact, data-only contract for captured Language Server Protocol semantics."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from anatomize._artifacts import canonical_json_bytes, require_unique
from anatomize.evidence import (
    EvidenceModel,
    ProjectionCompleteness,
    ProviderRunStatus,
    SourceStateRecord,
    validate_portable_identity,
    validate_repository_path,
)
from anatomize.identity import ProviderRange, SymbolRole
from anatomize.providers.models import ProviderToolIdentity

LSP_SEMANTIC_ARTIFACT_TYPE: Literal["anatomize.lsp-semantic"] = "anatomize.lsp-semantic"
LSP_SEMANTIC_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
SUPPORTED_LSP_VERSIONS = frozenset({"3.17", "3.18"})
DEFAULT_MAX_LSP_SEMANTIC_BYTES = 64 * 1024 * 1024


class LspPositionEncoding(str, Enum):
    """Negotiated LSP position encoding, never inferred from a server name."""

    UTF8 = "utf-8"
    UTF16 = "utf-16"
    UTF32 = "utf-32"


class SemanticCapability(str, Enum):
    """Evidence families captured from language-server responses."""

    DEFINITIONS = "definitions"
    DECLARATIONS = "declarations"
    REFERENCES = "references"
    CALLS = "calls"
    IMPLEMENTATIONS = "implementations"
    OVERRIDES = "overrides"


class SemanticRelationship(str, Enum):
    """Resolved relationship represented by one source occurrence."""

    REFERENCE = "reference"
    CALL = "call"
    IMPLEMENTATION = "implementation"
    OVERRIDE = "override"


class SemanticIssueCode(str, Enum):
    """Material degradation states retained by the captured artifact."""

    COMPILER_ERROR = "compiler_error"
    INCOMPLETE_PROJECT = "incomplete_project"
    GENERATED_PATH = "generated_path"
    STALE_INDEX = "stale_index"
    VERSION_SKEW = "version_skew"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class SemanticDocument(EvidenceModel):
    """One digest-bound repository document covered by the capture."""

    path: str
    language: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generated: bool = False
    generator_identity_id: str | None = None
    source_location_ids: list[str] = Field(default_factory=list)
    projection: ProjectionCompleteness = ProjectionCompleteness.UNAVAILABLE

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_repository_path(value)

    @model_validator(mode="after")
    def validate_generated_shape(self) -> SemanticDocument:
        if self.generated and self.generator_identity_id is None:
            raise ValueError("generated semantic documents require a generator identity")
        if not self.generated and (
            self.generator_identity_id is not None
            or self.source_location_ids
            or self.projection is not ProjectionCompleteness.UNAVAILABLE
        ):
            raise ValueError("ordinary semantic documents cannot claim generated-source projection")
        if len(self.source_location_ids) != len(set(self.source_location_ids)):
            raise ValueError("generated source locations must be unique")
        if self.projection in {ProjectionCompleteness.EXACT, ProjectionCompleteness.PARTIAL}:
            if not self.source_location_ids:
                raise ValueError("exact or partial generated projection requires source locations")
        return self


class SemanticSymbol(EvidenceModel):
    """One provider-native declaration with enough identity to reconcile it."""

    symbol_id: str = Field(min_length=1)
    document_path: str
    source_range: ProviderRange
    selection_range: ProviderRange | None = None
    language: str = Field(min_length=1)
    symbol_kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    role: SymbolRole
    signature: str | None = None
    public: bool | None = None

    @field_validator("symbol_id")
    @classmethod
    def validate_symbol_id(cls, value: str) -> str:
        return validate_portable_identity(value, label="semantic symbol identity")

    @field_validator("document_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_repository_path(value)

    @model_validator(mode="after")
    def validate_role(self) -> SemanticSymbol:
        if self.role is SymbolRole.OVERLOAD and self.signature is None:
            raise ValueError("semantic overloads require a signature")
        return self


class SemanticExternalTarget(EvidenceModel):
    """Portable external semantic target without a checkout-specific URI."""

    identity_scheme: str = Field(min_length=1)
    external_identity: str = Field(min_length=1)
    entity_kind: str = Field(min_length=1)
    display_name: str = Field(min_length=1)

    @field_validator("external_identity")
    @classmethod
    def validate_external_identity(cls, value: str) -> str:
        return validate_portable_identity(value, label="external semantic identity")


class SemanticOccurrence(EvidenceModel):
    """One occurrence and its internal, external, or unresolved target claim."""

    occurrence_id: str = Field(min_length=1)
    document_path: str
    source_range: ProviderRange
    relationship: SemanticRelationship
    source_symbol_id: str | None = None
    target_symbol_id: str | None = None
    external_target: SemanticExternalTarget | None = None

    @field_validator("occurrence_id", "source_symbol_id", "target_symbol_id")
    @classmethod
    def validate_native_identity(cls, value: str | None) -> str | None:
        return validate_portable_identity(value, label="semantic occurrence identity") if value else value

    @field_validator("document_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_repository_path(value)

    @model_validator(mode="after")
    def validate_target(self) -> SemanticOccurrence:
        if self.target_symbol_id is not None and self.external_target is not None:
            raise ValueError("semantic occurrences cannot have both internal and external targets")
        return self


class SemanticIssue(EvidenceModel):
    """Explicit producer-reported reason why semantic coverage is degraded."""

    issue_id: str = Field(min_length=1)
    code: SemanticIssueCode
    message: str = Field(min_length=1)
    path: str | None = None
    recoverable: bool
    remediation: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return validate_repository_path(value) if value is not None else None


class LspSemanticArtifact(EvidenceModel):
    """A replayable capture of selected LSP 3.17/3.18 semantic responses."""

    artifact_type: Literal["anatomize.lsp-semantic"] = LSP_SEMANTIC_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = LSP_SEMANTIC_SCHEMA_VERSION
    protocol: Literal["lsp"] = "lsp"
    protocol_version: str = Field(min_length=1)
    position_encoding: LspPositionEncoding
    tool: ProviderToolIdentity
    repository_id: str = Field(min_length=1)
    source_state: SourceStateRecord
    indexed_content_digest: str = Field(min_length=1)
    configuration_digest: str = Field(min_length=1)
    capabilities: list[SemanticCapability] = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    status: ProviderRunStatus
    documents: list[SemanticDocument] = Field(default_factory=list)
    symbols: list[SemanticSymbol] = Field(default_factory=list)
    occurrences: list[SemanticOccurrence] = Field(default_factory=list)
    issues: list[SemanticIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_artifact_graph(self) -> LspSemanticArtifact:
        if self.source_state.repository_id != self.repository_id:
            raise ValueError("semantic source state belongs to another repository")
        require_unique([item.path for item in self.documents], "semantic document paths")
        require_unique([item.symbol_id for item in self.symbols], "semantic symbol identities")
        require_unique([item.occurrence_id for item in self.occurrences], "semantic occurrence identities")
        require_unique([item.issue_id for item in self.issues], "semantic issue identities")
        document_paths = {item.path for item in self.documents}
        referenced_paths = {item.document_path for item in self.symbols}
        referenced_paths.update(item.document_path for item in self.occurrences)
        unknown_paths = sorted(referenced_paths.difference(document_paths))
        if unknown_paths:
            raise ValueError(f"semantic records reference undeclared documents: {unknown_paths}")
        document_languages = {item.path: item.language for item in self.documents}
        undeclared_languages = sorted(
            {item.language for item in self.documents}.union(item.language for item in self.symbols).difference(
                self.languages
            )
        )
        if undeclared_languages:
            raise ValueError(f"semantic records use undeclared languages: {undeclared_languages}")
        mismatched_symbol_languages = sorted(
            item.symbol_id
            for item in self.symbols
            if document_languages[item.document_path] != item.language
        )
        if mismatched_symbol_languages:
            raise ValueError(
                f"semantic symbols disagree with their document language: {mismatched_symbol_languages}"
            )
        capabilities = set(self.capabilities)
        required_capabilities: set[SemanticCapability] = set()
        if any(item.role in {SymbolRole.DEFINITION, SymbolRole.METHOD} for item in self.symbols):
            required_capabilities.add(SemanticCapability.DEFINITIONS)
        if any(item.role not in {SymbolRole.DEFINITION, SymbolRole.METHOD} for item in self.symbols):
            required_capabilities.add(SemanticCapability.DECLARATIONS)
        required_capabilities.update(
            {
                {
                    SemanticRelationship.REFERENCE: SemanticCapability.REFERENCES,
                    SemanticRelationship.CALL: SemanticCapability.CALLS,
                    SemanticRelationship.IMPLEMENTATION: SemanticCapability.IMPLEMENTATIONS,
                    SemanticRelationship.OVERRIDE: SemanticCapability.OVERRIDES,
                }[item.relationship]
                for item in self.occurrences
            }
        )
        missing_capabilities = sorted(
            item.value for item in required_capabilities.difference(capabilities)
        )
        if missing_capabilities:
            raise ValueError(f"semantic records lack declared capabilities: {missing_capabilities}")
        if self.status in {
            ProviderRunStatus.UNAVAILABLE,
            ProviderRunStatus.FAILED,
            ProviderRunStatus.CANCELLED,
        } and (self.symbols or self.occurrences):
            raise ValueError("unavailable, failed, or cancelled semantic artifacts cannot claim evidence")
        if self.status is ProviderRunStatus.COMPLETE and any(
            item.code
            in {
                SemanticIssueCode.COMPILER_ERROR,
                SemanticIssueCode.INCOMPLETE_PROJECT,
                SemanticIssueCode.STALE_INDEX,
                SemanticIssueCode.VERSION_SKEW,
                SemanticIssueCode.PROVIDER_UNAVAILABLE,
            }
            for item in self.issues
        ):
            raise ValueError("complete semantic artifacts cannot carry material degradation issues")
        return self


def canonical_lsp_semantic_bytes(artifact: LspSemanticArtifact) -> bytes:
    """Serialize one semantic artifact deterministically."""
    return canonical_json_bytes(artifact.model_dump(mode="json"))


def lsp_semantic_json_schema() -> dict[str, Any]:
    """Return the exact public JSON Schema for captured LSP semantics."""
    return LspSemanticArtifact.model_json_schema(mode="serialization")
