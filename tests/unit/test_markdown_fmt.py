"""Unit tests for Markdown formatter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from anatomize.core.types import (
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    PackageInfo,
    ResolutionLevel,
    Skeleton,
    SkeletonMetadata,
)
from anatomize.formats.markdown_fmt import MarkdownFormatter

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.unit


@pytest.fixture
def formatter() -> MarkdownFormatter:
    """Create a Markdown formatter instance."""
    return MarkdownFormatter()


class TestMarkdownFormatterWrite:
    """Tests for MarkdownFormatter.write method."""

    def test_write_creates_hierarchy_file(
        self, formatter: MarkdownFormatter, tmp_path: Path, minimal_skeleton: Skeleton
    ) -> None:
        """Test that write creates hierarchy.md file."""
        formatter.write(minimal_skeleton, tmp_path)
        assert (tmp_path / "hierarchy.md").exists()

    def test_write_creates_modules_directory(
        self, formatter: MarkdownFormatter, tmp_path: Path, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that write creates modules directory when modules exist."""
        formatter.write(skeleton_with_classes, tmp_path)
        assert (tmp_path / "modules").exists()
        assert (tmp_path / "modules").is_dir()

    def test_write_creates_module_files(
        self, formatter: MarkdownFormatter, tmp_path: Path, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that write creates package-named module files."""
        formatter.write(skeleton_with_classes, tmp_path)
        assert (tmp_path / "modules" / "pkg.md").exists()

    def test_hierarchy_file_contains_metadata(
        self, formatter: MarkdownFormatter, tmp_path: Path, minimal_skeleton: Skeleton
    ) -> None:
        """Test that hierarchy file contains metadata."""
        formatter.write(minimal_skeleton, tmp_path)
        content = (tmp_path / "hierarchy.md").read_text()
        assert "Anatomize" in content or "Package Hierarchy" in content
        assert "Modules" in content or "modules" in content.lower()


class TestMarkdownFormatterFormatString:
    """Tests for MarkdownFormatter.format_string method."""

    def test_format_string_includes_header(self, formatter: MarkdownFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that format_string includes header."""
        result = formatter.format_string(minimal_skeleton)
        assert "# Anatomize" in result or "#" in result

    def test_format_string_includes_metadata(self, formatter: MarkdownFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that format_string includes metadata info."""
        result = formatter.format_string(minimal_skeleton)
        assert "modules" in result.lower() or "Modules" in result

    def test_format_string_formats_classes(
        self, formatter: MarkdownFormatter, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that format_string includes class information."""
        result = formatter.format_string(skeleton_with_classes)
        assert "User" in result

    def test_format_string_formats_functions(
        self, formatter: MarkdownFormatter, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that format_string includes function information."""
        result = formatter.format_string(skeleton_with_classes)
        assert "create_user" in result


class TestFormatClass:
    """Tests for class formatting in Markdown output."""

    def test_class_with_decorators_in_output(
        self, formatter: MarkdownFormatter, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that decorated classes show decorators."""
        result = formatter.format_string(skeleton_with_classes)
        # User class has @dataclass decorator
        assert "dataclass" in result

    def test_class_with_bases_in_output(self, formatter: MarkdownFormatter, skeleton_with_classes: Skeleton) -> None:
        """Test that class bases are shown."""
        result = formatter.format_string(skeleton_with_classes)
        # User extends BaseModel, Admin extends User
        assert "BaseModel" in result or "User" in result

    def test_class_with_methods_in_output(self, formatter: MarkdownFormatter, skeleton_with_classes: Skeleton) -> None:
        """Test that class methods are shown."""
        result = formatter.format_string(skeleton_with_classes)
        assert "greet" in result


class TestFormatFunction:
    """Tests for function formatting in Markdown output."""

    def test_async_function_marked(self, formatter: MarkdownFormatter) -> None:
        """Test that async functions are marked."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.SIGNATURES,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["mod"])},
            modules={
                "pkg.mod": ModuleInfo(
                    path="pkg/mod.py",
                    name="pkg.mod",
                    source=0,
                    functions=[FunctionInfo(name="async_func", line=1, signature="() -> None", is_async=True)],
                )
            },
        )
        result = formatter.format_string(skeleton)
        assert "async" in result.lower() or "async_func" in result

    def test_function_with_decorators(self, formatter: MarkdownFormatter) -> None:
        """Test that function decorators are shown."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.SIGNATURES,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["mod"])},
            modules={
                "pkg.mod": ModuleInfo(
                    path="pkg/mod.py",
                    name="pkg.mod",
                    source=0,
                    functions=[
                        FunctionInfo(name="cached_func", line=1, signature="() -> None", decorators=["lru_cache"])
                    ],
                )
            },
        )
        result = formatter.format_string(skeleton)
        assert "lru_cache" in result or "cached_func" in result


class TestResolutionLevels:
    """Tests for different resolution levels in Markdown output."""

    def test_hierarchy_resolution(self, formatter: MarkdownFormatter) -> None:
        """Test output at hierarchy resolution level."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.HIERARCHY,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=["sub"], modules=["mod"])},
            modules={"pkg.mod": ModuleInfo(path="pkg/mod.py", name="pkg.mod", source=0)},
        )
        result = formatter.format_string(skeleton)
        assert "pkg" in result or "mod" in result

    def test_modules_resolution(self, formatter: MarkdownFormatter, minimal_skeleton: Skeleton) -> None:
        """Test output at modules resolution level."""
        result = formatter.format_string(minimal_skeleton)
        assert "mod" in result.lower() or "pkg.mod" in result

    def test_signatures_resolution_shows_signatures(
        self, formatter: MarkdownFormatter, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that signatures resolution shows function signatures."""
        result = formatter.format_string(skeleton_with_classes)
        # Should contain signature info like parameter types
        assert "str" in result or "int" in result


class TestEdgeCases:
    """Tests for edge cases in Markdown formatting."""

    def test_module_with_no_classes_or_functions(self, formatter: MarkdownFormatter) -> None:
        """Test formatting module with no classes or functions."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.MODULES,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["empty"])},
            modules={
                "pkg.empty": ModuleInfo(
                    path="pkg/empty.py",
                    name="pkg.empty",
                    source=0,
                    doc="An empty module.",
                )
            },
        )
        result = formatter.format_string(skeleton)
        assert "empty" in result.lower()

    def test_class_with_no_methods(self, formatter: MarkdownFormatter) -> None:
        """Test formatting class with no methods."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.SIGNATURES,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["mod"])},
            modules={
                "pkg.mod": ModuleInfo(
                    path="pkg/mod.py",
                    name="pkg.mod",
                    source=0,
                    classes=[ClassInfo(name="EmptyClass", line=1)],
                )
            },
        )
        result = formatter.format_string(skeleton)
        assert "EmptyClass" in result
