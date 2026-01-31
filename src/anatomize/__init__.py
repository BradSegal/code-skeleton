"""Anatomize - Generate token-efficient codebase packs and skeleton maps for AI review.

This package provides tools to extract and serialize codebase structure
at multiple resolution levels, optimized for LLM context efficiency.

Example
-------
>>> from anatomize import OutputFormat, SkeletonGenerator
>>> from anatomize.formats import write_skeleton
>>> gen = SkeletonGenerator(sources=["./src"])
>>> skeleton = gen.generate(level="modules")
>>> write_skeleton(skeleton, ".skeleton", formats=[OutputFormat.YAML])
"""

from anatomize.core.types import (
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    PackageInfo,
    Skeleton,
    SkeletonMetadata,
    SymbolInfo,
)
from anatomize.formats import OutputFormat
from anatomize.generators.main import SkeletonGenerator
from anatomize.version import __version__

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
