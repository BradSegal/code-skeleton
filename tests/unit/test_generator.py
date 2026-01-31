"""Unit tests for the skeleton generator."""

from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import ResolutionLevel
from anatomize.generators.main import SkeletonGenerator

pytestmark = pytest.mark.unit


@pytest.fixture
def fixtures_path() -> Path:
    """Return path to test fixtures."""
    return Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def sample_package_path(fixtures_path: Path) -> Path:
    """Return path to sample package fixture."""
    return fixtures_path / "sample_package"


class TestSkeletonGenerator:
    """Tests for SkeletonGenerator."""

    def test_generate_hierarchy(self, sample_package_path: Path) -> None:
        """Test generating hierarchy-level skeleton."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.HIERARCHY)

        assert skeleton.metadata.resolution == ResolutionLevel.HIERARCHY
        assert skeleton.metadata.total_packages >= 1
        assert "sample_package" in skeleton.packages

    def test_generate_modules(self, sample_package_path: Path) -> None:
        """Test generating module-level skeleton."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.MODULES)

        assert skeleton.metadata.resolution == ResolutionLevel.MODULES
        assert skeleton.metadata.total_modules >= 1

        # Check that modules were extracted
        assert len(skeleton.modules) > 0

    def test_generate_signatures(self, sample_package_path: Path) -> None:
        """Test generating signature-level skeleton."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.SIGNATURES)

        assert skeleton.metadata.resolution == ResolutionLevel.SIGNATURES
        assert skeleton.metadata.total_functions > 0

    def test_token_estimate(self, sample_package_path: Path) -> None:
        """Test that token estimation works."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.MODULES)

        assert skeleton.metadata.token_estimate > 0
        assert skeleton.token_estimate == skeleton.metadata.token_estimate

    def test_find_class(self, sample_package_path: Path) -> None:
        """Test finding classes by name."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.MODULES)

        results = skeleton.find_class("BaseModel")
        assert len(results) > 0

        module_name, class_info = results[0]
        assert class_info.name == "BaseModel"

    def test_find_function(self, sample_package_path: Path) -> None:
        """Test finding functions by name."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.MODULES)

        results = skeleton.find_function("process_data")
        assert len(results) > 0

        module_name, func_info = results[0]
        assert func_info.name == "process_data"

    def test_to_dict(self, sample_package_path: Path) -> None:
        """Test converting skeleton to dictionary."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        skeleton = gen.generate(level=ResolutionLevel.MODULES)

        data = skeleton.to_dict()
        assert "metadata" in data
        assert "packages" in data
        assert "modules" in data


class TestGeneratorErrors:
    """Tests for error handling."""

    def test_nonexistent_directory(self) -> None:
        """Test that nonexistent directory raises error."""
        with pytest.raises(ValueError, match="does not exist"):
            SkeletonGenerator(sources=["/nonexistent/path"], symlinks=SymlinkPolicy.FORBID, workers=0)

    def test_file_instead_of_directory(self, sample_package_path: Path) -> None:
        """Test that file path raises error."""
        file_path = sample_package_path / "models.py"
        with pytest.raises(ValueError, match="not a directory"):
            SkeletonGenerator(sources=[file_path], symlinks=SymlinkPolicy.FORBID, workers=0)

    def test_empty_sources_rejected(self) -> None:
        with pytest.raises(ValueError, match="At least one source directory"):
            SkeletonGenerator(sources=[], symlinks=SymlinkPolicy.FORBID, workers=0)

    def test_syntax_error_source_fails_hard(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "bad.py").write_text("def f( -> int:\n    return 1\n", encoding="utf-8")

        gen = SkeletonGenerator(sources=[src], exclude=[], symlinks=SymlinkPolicy.FORBID, workers=2)
        with pytest.raises(ValueError, match="bad.py"):
            gen.generate(level=ResolutionLevel.MODULES)

    def test_parallel_workers_produce_same_skeleton(self, sample_package_path: Path) -> None:
        src = sample_package_path.parent
        gen1 = SkeletonGenerator(sources=[src], symlinks=SymlinkPolicy.FORBID, workers=1)
        gen4 = SkeletonGenerator(sources=[src], symlinks=SymlinkPolicy.FORBID, workers=4)
        s1 = gen1.generate(level=ResolutionLevel.SIGNATURES).to_dict()
        s2 = gen4.generate(level=ResolutionLevel.SIGNATURES).to_dict()
        assert s1 == s2


class TestEstimate:
    """Tests for token estimation."""

    def test_estimate_method(self, sample_package_path: Path) -> None:
        """Test the estimate method."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)
        tokens = gen.estimate(level=ResolutionLevel.MODULES)

        assert tokens > 0

    def test_estimate_increases_with_resolution(self, sample_package_path: Path) -> None:
        """Test that higher resolution gives higher token count."""
        gen = SkeletonGenerator(sources=[sample_package_path.parent], symlinks=SymlinkPolicy.FORBID, workers=0)

        hierarchy_tokens = gen.estimate(level=ResolutionLevel.HIERARCHY)
        modules_tokens = gen.estimate(level=ResolutionLevel.MODULES)
        signatures_tokens = gen.estimate(level=ResolutionLevel.SIGNATURES)

        # Higher resolution should generally have more tokens
        # (This may not always hold for very small packages)
        assert modules_tokens >= hierarchy_tokens
        assert signatures_tokens >= modules_tokens
