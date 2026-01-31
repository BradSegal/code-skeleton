"""Markdown output formatter.

This module provides Markdown formatting for skeleton data, optimized
for human readability and documentation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from anatomize.core.types import ClassInfo, FunctionInfo, ResolutionLevel, Skeleton

logger = logging.getLogger(__name__)


class MarkdownFormatter:
    """Format skeleton data as Markdown.

    Markdown is recommended for documentation and human review.
    It produces readable output suitable for README files or wikis.

    Example
    -------
    >>> formatter = MarkdownFormatter()
    >>> formatter.write(skeleton, Path(".skeleton"))
    """

    def write(self, skeleton: Skeleton, output_dir: Path) -> None:
        """Write skeleton to Markdown files.

        Parameters
        ----------
        skeleton
            Skeleton data to write.
        output_dir
            Output directory path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write hierarchy file
        hierarchy_path = output_dir / "hierarchy.md"
        self._write_hierarchy(skeleton, hierarchy_path)

        # Write module files if available
        if skeleton.modules:
            modules_dir = output_dir / "modules"
            modules_dir.mkdir(exist_ok=True)
            self._write_modules(skeleton, modules_dir)

        logger.info("Wrote skeleton to %s (Markdown)", output_dir)

    def _write_hierarchy(self, skeleton: Skeleton, path: Path) -> None:
        """Write hierarchy Markdown file.

        Parameters
        ----------
        skeleton
            Skeleton data.
        path
            Output file path.
        """
        lines: list[str] = []

        # Header
        sources = ", ".join(skeleton.metadata.sources)
        lines.append("# Anatomize")
        lines.append("")
        lines.append(
            f"Sources: {sources} | "
            f"Packages: {skeleton.metadata.total_packages} | "
            f"Modules: {skeleton.metadata.total_modules} | "
            f"Classes: {skeleton.metadata.total_classes} | "
            f"Functions: {skeleton.metadata.total_functions}"
        )
        lines.append("")
        lines.append(f"**Token Estimate:** ~{skeleton.metadata.token_estimate:,}")
        lines.append("")

        # Package hierarchy
        lines.append("## Package Hierarchy")
        lines.append("")

        for name in sorted(skeleton.packages.keys()):
            pkg = skeleton.packages[name]
            lines.append(f"### {name}")
            lines.append("")

            if pkg.subpackages:
                lines.append(f"**Subpackages:** {', '.join(sorted(pkg.subpackages))}")
                lines.append("")

            if pkg.modules:
                lines.append(f"**Modules:** {', '.join(sorted(pkg.modules))}")
                lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")

    def _write_modules(self, skeleton: Skeleton, output_dir: Path) -> None:
        """Write module Markdown files.

        Parameters
        ----------
        skeleton
            Skeleton data.
        output_dir
            Output directory for module files.
        """
        # Group modules by package
        packages_modules: dict[str, list[str]] = {}

        for module_name in skeleton.modules:
            if "." in module_name:
                package_name = module_name.rsplit(".", 1)[0]
            else:
                package_name = "__root__"

            if package_name not in packages_modules:
                packages_modules[package_name] = []
            packages_modules[package_name].append(module_name)

        # Write each package's modules to a separate file
        for package_name, module_names in packages_modules.items():
            filename = f"{package_name}.md"
            path = output_dir / filename

            lines: list[str] = []
            lines.append(f"# Package: {package_name}")
            lines.append("")

            for module_name in sorted(module_names):
                module_info = skeleton.modules[module_name]
                simple_name = module_name.rsplit(".", 1)[-1]

                lines.append(f"## {simple_name}")
                lines.append("")
                lines.append(f"**Path:** `{module_info.path}`")
                lines.append("")

                if module_info.doc:
                    lines.append(f"> {module_info.doc}")
                    lines.append("")

                # Classes
                if module_info.classes:
                    lines.append("### Classes")
                    lines.append("")

                    for cls in module_info.classes:
                        lines.extend(self._format_class(cls, skeleton.metadata.resolution))
                        lines.append("")

                # Functions
                if module_info.functions:
                    lines.append("### Functions")
                    lines.append("")

                    for func in module_info.functions:
                        lines.extend(self._format_function(func, skeleton.metadata.resolution))
                        lines.append("")

            path.write_text("\n".join(lines), encoding="utf-8")

    def _format_class(self, cls: ClassInfo, resolution: ResolutionLevel) -> list[str]:
        """Format a class as Markdown.

        Parameters
        ----------
        cls
            Class information.
        resolution
            Resolution level.

        Returns
        -------
        list[str]
            Markdown lines.
        """
        lines: list[str] = []

        # Class header with decorators
        if cls.decorators:
            lines.append(f"#### `@{', @'.join(cls.decorators)}`")

        # Class name with bases
        if cls.bases:
            lines.append(f"#### `class {cls.name}({', '.join(cls.bases)})` (line {cls.line})")
        else:
            lines.append(f"#### `class {cls.name}` (line {cls.line})")

        if cls.doc:
            lines.append(f"> {cls.doc}")

        # Methods
        if cls.methods:
            lines.append("")
            lines.append("**Methods:**")

            for method in cls.methods:
                if resolution == ResolutionLevel.SIGNATURES and method.signature:
                    sig = f"`{method.name}{method.signature}`"
                else:
                    sig = f"`{method.name}()`"

                prefix = ""
                if method.is_async:
                    prefix = "async "
                if method.decorators:
                    prefix += f"@{method.decorators[0]} "

                lines.append(f"- {prefix}{sig} (line {method.line})")

        return lines

    def _format_function(self, func: FunctionInfo, resolution: ResolutionLevel) -> list[str]:
        """Format a function as Markdown.

        Parameters
        ----------
        func
            Function information.
        resolution
            Resolution level.

        Returns
        -------
        list[str]
            Markdown lines.
        """
        lines: list[str] = []

        # Decorators
        if func.decorators:
            lines.append(f"#### `@{', @'.join(func.decorators)}`")

        # Function signature
        prefix = "async " if func.is_async else ""
        if resolution == ResolutionLevel.SIGNATURES and func.signature:
            lines.append(f"#### `{prefix}def {func.name}{func.signature}` (line {func.line})")
        else:
            lines.append(f"#### `{prefix}def {func.name}()` (line {func.line})")

        if func.doc:
            lines.append(f"> {func.doc}")

        return lines

    def format_string(self, skeleton: Skeleton) -> str:
        """Format skeleton as a Markdown string.

        Parameters
        ----------
        skeleton
            Skeleton data.

        Returns
        -------
        str
            Markdown formatted string.
        """
        lines: list[str] = []

        # Header
        sources = ", ".join(skeleton.metadata.sources)
        lines.append("# Anatomize")
        lines.append("")
        lines.append(
            f"Sources: {sources} | "
            f"Modules: {skeleton.metadata.total_modules} | "
            f"Classes: {skeleton.metadata.total_classes} | "
            f"Functions: {skeleton.metadata.total_functions}"
        )
        lines.append("")

        # Add full content
        for module_name in sorted(skeleton.modules.keys()):
            module = skeleton.modules[module_name]
            lines.append(f"## {module_name}")
            lines.append(f"Path: `{module.path}`")
            if module.doc:
                lines.append(f"> {module.doc}")
            lines.append("")

            for cls in module.classes:
                lines.extend(self._format_class(cls, skeleton.metadata.resolution))
                lines.append("")

            for func in module.functions:
                lines.extend(self._format_function(func, skeleton.metadata.resolution))
                lines.append("")

        return "\n".join(lines)
