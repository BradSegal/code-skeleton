"""Code Skeleton - Generate token-efficient codebase structure maps for AI agents.

This package provides tools to extract and serialize codebase structure
at multiple resolution levels, optimized for LLM context efficiency.

Example
-------
>>> from code_skeleton import OutputFormat, SkeletonGenerator
>>> from code_skeleton.formats import write_skeleton
>>> gen = SkeletonGenerator(sources=["./src"])
>>> skeleton = gen.generate(level="modules")
>>> write_skeleton(skeleton, ".skeleton", formats=[OutputFormat.YAML])
"""

from code_skeleton.core.types import (
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    PackageInfo,
    Skeleton,
    SkeletonMetadata,
    SymbolInfo,
)
from code_skeleton.formats import OutputFormat
from code_skeleton.generators.main import SkeletonGenerator
from code_skeleton.version import __version__

__all__ = [
    # Core types
    "SymbolInfo",
    "FunctionInfo",
    "ClassInfo",
    "ModuleInfo",
    "PackageInfo",
    "Skeleton",
    "SkeletonMetadata",
    # Generator
    "SkeletonGenerator",
    # Formats
    "OutputFormat",
    # Version
    "__version__",
]
