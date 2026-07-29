"""Typed contracts for portable repository intelligence."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SymbolKind(str, Enum):
    """Supported Python definition kinds."""

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class ImpactRole(str, Enum):
    """Why a file belongs to an impact surface."""

    FOCUS = "focus"
    DEPENDENCY = "dependency"
    IMPORTER = "importer"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"


class SourceState(BaseModel):
    """Portable identity of the indexed source state."""

    commit: str | None
    dirty: bool
    python_digest: str
    python_file_count: int

    model_config = {"frozen": True}


class SymbolRecord(BaseModel):
    """One Python definition."""

    name: str
    qualified_name: str
    kind: SymbolKind
    path: str
    line: int
    public: bool

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


class RepositoryIndex(BaseModel):
    """Canonical portable repository index."""

    schema_version: str = "1.0.0"
    root_name: str
    source_state: SourceState
    python_roots: list[str]
    modules: list[ModuleRecord]
    symbols: list[SymbolRecord]
    import_edges: list[ImportEdge]
    limitations: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class ImpactNode(BaseModel):
    """One role-labelled file in an impact surface."""

    path: str
    role: ImpactRole
    distance: int
    reason: str

    model_config = {"frozen": True}


class ImpactReport(BaseModel):
    """Dependency and supporting-context impact for one target."""

    schema_version: str = "1.0.0"
    root_name: str
    source_state: SourceState
    query: str
    focus: list[str]
    nodes: list[ImpactNode]
    related_omissions: dict[str, int] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class ChangedReport(BaseModel):
    """Impact surface for files changed from an explicit Git base."""

    schema_version: str = "1.0.0"
    root_name: str
    source_state: SourceState
    base: str
    changed_files: list[str]
    nodes: list[ImpactNode]
    related_omissions: dict[str, int] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}
