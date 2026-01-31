"""Pydantic models for extracted code structure.

This module defines the core data models used throughout anatomize
package for representing extracted code structure at various resolutions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResolutionLevel(str, Enum):
    """Resolution level for skeleton generation.

    Attributes
    ----------
    HIERARCHY
        L1: Package tree with module list only (~500-800 tokens).
    MODULES
        L2: Module summaries with class/function names (~200-400 tokens/module).
    SIGNATURES
        L3: Full signatures with parameters and types (~800-1500 tokens/module).
    """

    HIERARCHY = "hierarchy"
    MODULES = "modules"
    SIGNATURES = "signatures"


class SymbolInfo(BaseModel):
    """Base model for extracted symbols.

    Attributes
    ----------
    name
        Symbol name (function, class, or variable name).
    line
        Line number where the symbol is defined.
    doc
        First line of docstring if present.
    """

    name: str
    line: int
    doc: str | None = None

    model_config = {"frozen": True}


class ParameterInfo(BaseModel):
    """Function parameter information.

    Attributes
    ----------
    name
        Parameter name.
    annotation
        Type annotation as string, if present.
    default
        Default value as string representation, if present.
    kind
        Parameter kind (positional, keyword, *args, **kwargs).
    """

    name: str
    annotation: str | None = None
    default: str | None = None
    kind: str = "POSITIONAL_OR_KEYWORD"

    model_config = {"frozen": True}


class FunctionInfo(SymbolInfo):
    """Function or method signature information.

    Attributes
    ----------
    signature
        Full signature string (parameters with types).
    returns
        Return type annotation as string.
    decorators
        List of decorator names.
    is_async
        Whether the function is async.
    is_method
        Whether this is a method (vs standalone function).
    parameters
        List of parameter details (L3 only).
    """

    signature: str = ""
    returns: str | None = None
    decorators: list[str] = Field(default_factory=list)
    is_async: bool = False
    is_method: bool = False
    parameters: list[ParameterInfo] = Field(default_factory=list)


class AttributeInfo(SymbolInfo):
    """Class attribute information.

    Attributes
    ----------
    annotation
        Type annotation as string, if present.
    default
        Default value as string representation, if present.
    """

    annotation: str | None = None
    default: str | None = None


class ClassInfo(SymbolInfo):
    """Class definition information.

    Attributes
    ----------
    bases
        List of base class names.
    decorators
        List of decorator names.
    methods
        List of method signatures.
    attributes
        List of class attributes (L3 only).
    is_dataclass
        Whether the class is a dataclass.
    is_pydantic
        Whether the class is a Pydantic model.
    """

    bases: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    methods: list[FunctionInfo] = Field(default_factory=list)
    attributes: list[AttributeInfo] = Field(default_factory=list)
    is_dataclass: bool = False
    is_pydantic: bool = False


class ModuleInfo(BaseModel):
    """Module-level structure information.

    Attributes
    ----------
    path
        Relative path to the module file.
    name
        Module name (e.g., 'core.types').
    doc
        First line of module docstring.
    classes
        List of class definitions.
    functions
        List of top-level function definitions.
    imports
        List of import statements (for dependency tracking).
    constants
        List of module-level constants (L3 only).
    """

    path: str
    name: str
    doc: str | None = None
    classes: list[ClassInfo] = Field(default_factory=list)
    functions: list[FunctionInfo] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    constants: list[AttributeInfo] = Field(default_factory=list)
    source: int = 0

    model_config = {"frozen": True}


class PackageInfo(BaseModel):
    """Package hierarchy information.

    Attributes
    ----------
    name
        Package name (e.g., 'core' or 'core.orchestration').
    subpackages
        List of direct subpackage names.
    modules
        List of module names (without .py extension).
    """

    name: str
    subpackages: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class SkeletonMetadata(BaseModel):
    """Generation metadata for the skeleton.

    Attributes
    ----------
    generator_version
        Version of anatomize that generated this.
    sources
        Source roots (container directories) that were analyzed.
    resolution
        Resolution level of the skeleton.
    total_packages
        Total number of packages found.
    total_modules
        Total number of modules found.
    total_classes
        Total number of classes found.
    total_functions
        Total number of functions found.
    token_estimate
        Estimated token count for the full skeleton.
    """

    generator_version: str
    sources: list[str]
    resolution: ResolutionLevel
    total_packages: int = 0
    total_modules: int = 0
    total_classes: int = 0
    total_functions: int = 0
    token_estimate: int = 0


class Skeleton(BaseModel):
    """Complete skeleton representation.

    This is the top-level container for all extracted structure data.
    It can be serialized to YAML, JSON, or Markdown at any resolution level.

    Attributes
    ----------
    metadata
        Generation metadata.
    packages
        Package hierarchy (L1+).
    modules
        Module details keyed by module name (L2+).
    """

    metadata: SkeletonMetadata
    packages: dict[str, PackageInfo] = Field(default_factory=dict)
    modules: dict[str, ModuleInfo] = Field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        """Return the estimated token count."""
        return self.metadata.token_estimate

    def get_module(self, name: str) -> ModuleInfo | None:
        """Get module info by name.

        Parameters
        ----------
        name
            Module name (e.g., 'core.types').

        Returns
        -------
        ModuleInfo or None
            Module information if found.
        """
        return self.modules.get(name)

    def get_package(self, name: str) -> PackageInfo | None:
        """Get package info by name.

        Parameters
        ----------
        name
            Package name (e.g., 'core.orchestration').

        Returns
        -------
        PackageInfo or None
            Package information if found.
        """
        return self.packages.get(name)

    def find_class(self, class_name: str) -> list[tuple[str, ClassInfo]]:
        """Find all classes with the given name.

        Parameters
        ----------
        class_name
            Class name to search for.

        Returns
        -------
        list[tuple[str, ClassInfo]]
            List of (module_name, class_info) tuples.
        """
        results: list[tuple[str, ClassInfo]] = []
        for module_name, module in self.modules.items():
            for cls in module.classes:
                if cls.name == class_name:
                    results.append((module_name, cls))
        return results

    def find_function(self, func_name: str) -> list[tuple[str, FunctionInfo]]:
        """Find all functions with the given name.

        Parameters
        ----------
        func_name
            Function name to search for.

        Returns
        -------
        list[tuple[str, FunctionInfo]]
            List of (module_name, function_info) tuples.
        """
        results: list[tuple[str, FunctionInfo]] = []
        for module_name, module in self.modules.items():
            for func in module.functions:
                if func.name == func_name:
                    results.append((module_name, func))
        return results

    def to_dict(self, exclude_none: bool = True) -> dict[str, Any]:
        """Convert skeleton to dictionary.

        Parameters
        ----------
        exclude_none
            Whether to exclude None values from output.

        Returns
        -------
        dict[str, Any]
            Dictionary representation of the skeleton.
        """
        return self.model_dump(mode="json", exclude_none=exclude_none)
