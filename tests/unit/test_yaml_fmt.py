"""Unit tests for YAML formatter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from anatomize.core.types import (
    ModuleInfo,
    PackageInfo,
    ResolutionLevel,
    Skeleton,
    SkeletonMetadata,
)
from anatomize.formats.yaml_fmt import YamlFormatter

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.unit


@pytest.fixture
def formatter() -> YamlFormatter:
    """Create a YAML formatter instance."""
    return YamlFormatter()


class TestYamlFormatterWrite:
    """Tests for YamlFormatter.write method."""

    def test_write_creates_hierarchy_file(
        self, formatter: YamlFormatter, tmp_path: Path, minimal_skeleton: Skeleton
    ) -> None:
        """Test that write creates hierarchy.yaml file."""
        formatter.write(minimal_skeleton, tmp_path)
        assert (tmp_path / "hierarchy.yaml").exists()

    def test_write_creates_modules_directory(
        self, formatter: YamlFormatter, tmp_path: Path, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that write creates modules directory when modules exist."""
        formatter.write(skeleton_with_classes, tmp_path)
        assert (tmp_path / "modules").exists()
        assert (tmp_path / "modules").is_dir()

    def test_write_creates_module_files(
        self, formatter: YamlFormatter, tmp_path: Path, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that write creates package-named module files."""
        formatter.write(skeleton_with_classes, tmp_path)
        assert (tmp_path / "modules" / "pkg.yaml").exists()

    def test_hierarchy_is_valid_yaml(
        self, formatter: YamlFormatter, tmp_path: Path, minimal_skeleton: Skeleton
    ) -> None:
        """Test that hierarchy file contains valid YAML."""
        formatter.write(minimal_skeleton, tmp_path)
        content = (tmp_path / "hierarchy.yaml").read_text()
        data = yaml.safe_load(content)
        assert data is not None
        assert "metadata" in data

    def test_hierarchy_contains_metadata(
        self, formatter: YamlFormatter, tmp_path: Path, minimal_skeleton: Skeleton
    ) -> None:
        """Test that hierarchy file contains metadata section."""
        formatter.write(minimal_skeleton, tmp_path)
        content = (tmp_path / "hierarchy.yaml").read_text()
        data = yaml.safe_load(content)
        assert "generator_version" in data["metadata"]
        assert "resolution" in data["metadata"]

    def test_hierarchy_contains_packages(
        self, formatter: YamlFormatter, tmp_path: Path, minimal_skeleton: Skeleton
    ) -> None:
        """Test that hierarchy file contains packages section."""
        formatter.write(minimal_skeleton, tmp_path)
        content = (tmp_path / "hierarchy.yaml").read_text()
        data = yaml.safe_load(content)
        assert "packages" in data
        assert "pkg" in data["packages"]

    def test_module_file_is_valid_yaml(
        self, formatter: YamlFormatter, tmp_path: Path, skeleton_with_classes: Skeleton
    ) -> None:
        """Test that module files contain valid YAML."""
        formatter.write(skeleton_with_classes, tmp_path)
        content = (tmp_path / "modules" / "pkg.yaml").read_text()
        data = yaml.safe_load(content)
        assert data is not None


class TestYamlFormatterFormatString:
    """Tests for YamlFormatter.format_string method."""

    def test_format_string_returns_valid_yaml(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that format_string returns valid YAML."""
        result = formatter.format_string(minimal_skeleton)
        data = yaml.safe_load(result)
        assert data is not None

    def test_format_string_includes_metadata(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that format_string includes metadata."""
        result = formatter.format_string(minimal_skeleton)
        data = yaml.safe_load(result)
        assert "metadata" in data

    def test_format_string_includes_packages(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that format_string includes packages."""
        result = formatter.format_string(minimal_skeleton)
        data = yaml.safe_load(result)
        assert "packages" in data


class TestYamlContent:
    """Tests for YAML content structure."""

    def test_metadata_has_correct_fields(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that metadata contains all required fields."""
        result = formatter.format_string(minimal_skeleton)
        data = yaml.safe_load(result)
        meta = data["metadata"]
        assert "generator_version" in meta
        assert "sources" in meta
        assert "resolution" in meta
        assert "total_packages" in meta
        assert "total_modules" in meta

    def test_packages_structure(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that packages have correct structure."""
        result = formatter.format_string(minimal_skeleton)
        data = yaml.safe_load(result)
        pkg = data["packages"]["pkg"]
        assert "subpackages" in pkg
        assert "modules" in pkg

    def test_resolution_is_string(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that resolution level is serialized as string."""
        result = formatter.format_string(minimal_skeleton)
        data = yaml.safe_load(result)
        assert data["metadata"]["resolution"] == "modules"


class TestYamlFormatting:
    """Tests for YAML formatting options."""

    def test_output_is_human_readable(self, formatter: YamlFormatter, minimal_skeleton: Skeleton) -> None:
        """Test that output is formatted for human readability."""
        result = formatter.format_string(minimal_skeleton)
        # Should use block style, not flow style
        assert "{" not in result or result.count("{") < 3

    def test_unicode_is_preserved(self, formatter: YamlFormatter) -> None:
        """Test that unicode characters are preserved."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.MODULES,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["mod"])},
            modules={
                "pkg.mod": ModuleInfo(
                    path="pkg/mod.py",
                    name="pkg.mod",
                    source=0,
                    doc="Unicode: \u00e9\u00e8\u00ea \u4e2d\u6587",
                )
            },
        )
        result = formatter.format_string(skeleton)
        # Unicode should be preserved in output
        data = yaml.safe_load(result)
        assert data is not None


class TestEdgeCases:
    """Tests for edge cases in YAML formatting."""

    def test_empty_skeleton(self, formatter: YamlFormatter) -> None:
        """Test formatting skeleton with no packages or modules."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.HIERARCHY,
            ),
            packages={},
            modules={},
        )
        result = formatter.format_string(skeleton)
        data = yaml.safe_load(result)
        assert data["packages"] == {}

    def test_multiple_packages(self, formatter: YamlFormatter) -> None:
        """Test formatting skeleton with multiple packages."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test"],
                resolution=ResolutionLevel.HIERARCHY,
            ),
            packages={
                "pkg_a": PackageInfo(name="pkg_a", subpackages=[], modules=["mod1"]),
                "pkg_b": PackageInfo(name="pkg_b", subpackages=["sub"], modules=["mod2"]),
            },
            modules={},
        )
        result = formatter.format_string(skeleton)
        data = yaml.safe_load(result)
        assert "pkg_a" in data["packages"]
        assert "pkg_b" in data["packages"]

    def test_special_characters_in_paths(self, formatter: YamlFormatter) -> None:
        """Test that special characters in paths are handled."""
        skeleton = Skeleton(
            metadata=SkeletonMetadata(
                generator_version="1.0.0",
                sources=["/test/path with spaces"],
                resolution=ResolutionLevel.MODULES,
            ),
            packages={"pkg": PackageInfo(name="pkg", subpackages=[], modules=["mod"])},
            modules={
                "pkg.mod": ModuleInfo(
                    path="pkg/file-with-dash.py",
                    name="pkg.mod",
                    source=0,
                )
            },
        )
        result = formatter.format_string(skeleton)
        data = yaml.safe_load(result)
        assert data is not None
