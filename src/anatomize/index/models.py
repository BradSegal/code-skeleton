"""Typed contracts for portable repository intelligence."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

REPOSITORY_INDEX_SCHEMA_VERSION: Literal["2.0.0"] = "2.0.0"
PYTHON_AST_PROVIDER = "python_ast"
DOCUMENTATION_TEXT_PROVIDER = "documentation_text"
REPOSITORY_INVENTORY_PROVIDER = "repository_inventory"


class SymbolKind(str, Enum):
    """Supported Python definition kinds."""

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class FileRole(str, Enum):
    """Observable repository roles assigned without a quality judgement."""

    SOURCE = "source"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    DATA = "data"
    WORKFLOW = "workflow"
    ARTIFACT = "artifact"
    OTHER = "other"


class ProviderCompleteness(str, Enum):
    """Whether a fact provider completed its declared scope."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class EvidenceConfidence(str, Enum):
    """Strength of one statically observed relationship."""

    EXACT = "exact"
    CONSERVATIVE = "conservative"


class OccurrenceKind(str, Enum):
    """How a symbol appears at one exact source range."""

    DEFINITION = "definition"
    IMPORT = "import"
    REFERENCE = "reference"


class RelationshipKind(str, Enum):
    """Typed relationships emitted by repository fact providers."""

    DEFINES = "defines"
    IMPORTS = "imports"
    REFERENCES = "references"


class DuplicateKind(str, Enum):
    """Review surface in which repeated structure was observed."""

    IMPLEMENTATION = "implementation"
    TEST = "test"
    DOCUMENTATION = "documentation"


class DuplicateNormalization(str, Enum):
    """Deterministic transformation used to compare candidate members."""

    EXACT_AST = "exact_ast"
    DEFINITION_NAME = "definition_name"
    MARKDOWN_BODY_WHITESPACE = "markdown_body_whitespace"


class ArtifactProducer(BaseModel):
    """Identity of the Anatomize release that wrote an artifact."""

    name: Literal["anatomize"] = "anatomize"
    version: str

    model_config = {"frozen": True}


class FactProvider(BaseModel):
    """One fact producer and the evidence families it supplied."""

    provider_id: str
    version: str
    capabilities: list[str]
    completeness: ProviderCompleteness
    limitations: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class FileRecord(BaseModel):
    """Content identity and provider coverage for one indexed file."""

    file_id: str
    path: str
    language: str
    digest: str
    size: int
    roles: list[FileRole]
    provider_ids: list[str]

    model_config = {"frozen": True}


class SourceState(BaseModel):
    """Portable identity of the indexed source state."""

    commit: str | None
    dirty: bool
    python_digest: str
    python_file_count: int
    fact_digest: str
    fact_file_count: int
    provider_digest: str

    model_config = {"frozen": True}


class SymbolRecord(BaseModel):
    """One Python definition."""

    symbol_id: str
    name: str
    qualified_name: str
    kind: SymbolKind
    path: str
    line: int
    end_line: int
    column: int
    end_column: int = 0
    digest: str
    exact_digest: str = ""
    node_count: int = 0
    body_line_count: int = 0
    public: bool
    provider_id: str = PYTHON_AST_PROVIDER

    model_config = {"frozen": True}


class ModuleRecord(BaseModel):
    """One indexed Python module."""

    module: str
    path: str
    imports: list[str] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class ImportEdge(BaseModel):
    """One resolved local import edge."""

    importer: str
    imported: str
    importer_path: str
    imported_path: str

    model_config = {"frozen": True}


class ReferenceCandidate(BaseModel):
    """Per-file lexical evidence awaiting global symbol resolution."""

    candidate_id: str
    target_qualified_name: str
    path: str
    kind: OccurrenceKind
    line: int
    end_line: int
    column: int
    end_column: int
    enclosing_symbol_id: str | None = None
    alias_qualified_name: str | None = None
    provider_id: str = PYTHON_AST_PROVIDER

    model_config = {"frozen": True}


class OccurrenceRecord(BaseModel):
    """One resolved symbol occurrence with an exact source range."""

    occurrence_id: str
    symbol_id: str
    path: str
    kind: OccurrenceKind
    line: int
    end_line: int
    column: int
    end_column: int
    enclosing_symbol_id: str | None = None
    provider_id: str
    confidence: EvidenceConfidence

    model_config = {"frozen": True}


class RelationshipRecord(BaseModel):
    """One typed edge grounded in an exact occurrence."""

    relationship_id: str
    source_id: str
    target_id: str
    kind: RelationshipKind
    occurrence_id: str
    provider_id: str
    confidence: EvidenceConfidence
    reason: str

    model_config = {"frozen": True}


class DocumentationSection(BaseModel):
    """One heading-bounded Markdown section used for review evidence."""

    section_id: str
    path: str
    heading: str
    line: int
    end_line: int
    digest: str
    normalized_digest: str
    word_count: int
    provider_id: str

    model_config = {"frozen": True}


class DuplicateMember(BaseModel):
    """One exact member of a deterministic duplicate candidate group."""

    member_id: str
    path: str
    line: int
    end_line: int
    role: FileRole
    size_lines: int
    size_units: int
    exact_digest: str
    normalized_digest: str
    symbol_id: str | None = None
    section_id: str | None = None

    model_config = {"frozen": True}


class DuplicateGroup(BaseModel):
    """A review candidate sharing one declared structural representation."""

    group_id: str
    kind: DuplicateKind
    normalization: DuplicateNormalization
    members: list[DuplicateMember]
    differences: list[str]
    provider_id: str
    confidence: EvidenceConfidence

    model_config = {"frozen": True}


class RepositoryIndex(BaseModel):
    """Canonical portable repository index."""

    schema_version: Literal["2.0.0"] = REPOSITORY_INDEX_SCHEMA_VERSION
    producer: ArtifactProducer
    root_name: str
    source_state: SourceState
    providers: list[FactProvider]
    files: list[FileRecord]
    python_roots: list[str]
    modules: list[ModuleRecord]
    symbols: list[SymbolRecord]
    import_edges: list[ImportEdge]
    occurrences: list[OccurrenceRecord] = Field(default_factory=list)
    documentation_sections: list[DocumentationSection] = Field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}
