"""Unit tests for the symbol extractor."""

from pathlib import Path

import pytest

from anatomize.core.extractor import SymbolExtractor
from anatomize.core.types import ResolutionLevel

pytestmark = pytest.mark.unit


@pytest.fixture
def fixtures_path() -> Path:
    """Return path to test fixtures."""
    return Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def sample_package_path(fixtures_path: Path) -> Path:
    """Return path to sample package fixture."""
    return fixtures_path / "sample_package"


class TestSymbolExtractor:
    """Tests for SymbolExtractor."""

    def test_extract_module_l2(self, sample_package_path: Path) -> None:
        """Test extracting module at L2 (modules) resolution."""
        extractor = SymbolExtractor(resolution=ResolutionLevel.MODULES)
        module_path = sample_package_path / "models.py"

        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )

        # Check basic info
        assert module_info.name == "sample_package.models"
        assert module_info.doc is not None
        assert "Sample models module" in module_info.doc

        # Check classes
        class_names = [c.name for c in module_info.classes]
        assert "SimpleDataclass" in class_names
        assert "BaseModel" in class_names
        assert "DerivedModel" in class_names

        # Check functions
        func_names = [f.name for f in module_info.functions]
        assert "process_data" in func_names
        assert "fetch_data" in func_names

    def test_extract_module_l3(self, sample_package_path: Path) -> None:
        """Test extracting module at L3 (signatures) resolution."""
        extractor = SymbolExtractor(resolution=ResolutionLevel.SIGNATURES)
        module_path = sample_package_path / "models.py"

        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )

        # Check that signatures are included
        process_func = next(f for f in module_info.functions if f.name == "process_data")
        assert process_func.signature != ""
        assert "items" in process_func.signature
        assert "max_items" in process_func.signature

        # Check async function
        fetch_func = next(f for f in module_info.functions if f.name == "fetch_data")
        assert fetch_func.is_async is True
        assert fetch_func.returns == "dict[str, Any]"

    def test_extract_class_info(self, sample_package_path: Path) -> None:
        """Test extracting class information."""
        extractor = SymbolExtractor(resolution=ResolutionLevel.MODULES)
        module_path = sample_package_path / "models.py"

        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )

        # Check BaseModel class
        base_model = next(c for c in module_info.classes if c.name == "BaseModel")
        assert base_model.doc is not None
        assert "base model" in base_model.doc.lower()

        # Check methods
        method_names = [m.name for m in base_model.methods]
        assert "__init__" in method_names
        assert "get_id" in method_names

        # Check DerivedModel inheritance
        derived_model = next(c for c in module_info.classes if c.name == "DerivedModel")
        assert "BaseModel" in derived_model.bases

    def test_extract_dataclass(self, sample_package_path: Path) -> None:
        """Test extracting dataclass information."""
        extractor = SymbolExtractor(resolution=ResolutionLevel.MODULES)
        module_path = sample_package_path / "models.py"

        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )

        # Check SimpleDataclass
        dataclass = next(c for c in module_info.classes if c.name == "SimpleDataclass")
        assert dataclass.is_dataclass is True
        assert "dataclass" in dataclass.decorators

    def test_extract_line_numbers(self, sample_package_path: Path) -> None:
        """Test that line numbers are extracted correctly."""
        extractor = SymbolExtractor(resolution=ResolutionLevel.MODULES)
        module_path = sample_package_path / "models.py"

        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )

        # All classes and functions should have positive line numbers
        for cls in module_info.classes:
            assert cls.line > 0
            for method in cls.methods:
                assert method.line > 0

        for func in module_info.functions:
            assert func.line > 0


class TestResolutionLevels:
    """Tests for different resolution levels."""

    def test_hierarchy_resolution(self, sample_package_path: Path) -> None:
        """Test that hierarchy resolution extracts minimal info."""
        # Hierarchy resolution primarily affects which details are extracted.
        # This test verifies the extractor doesn't break at any level.
        extractor = SymbolExtractor(resolution=ResolutionLevel.HIERARCHY)
        module_path = sample_package_path / "models.py"

        # Should still work but with minimal data
        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )
        assert module_info.name == "sample_package.models"

    def test_signatures_includes_parameters(self, sample_package_path: Path) -> None:
        """Test that signatures resolution includes parameter details."""
        extractor = SymbolExtractor(resolution=ResolutionLevel.SIGNATURES)
        module_path = sample_package_path / "models.py"

        module_info = extractor.extract_module(
            module_path,
            "sample_package.models",
            relative_path="sample_package/models.py",
            source=0,
        )

        # Find process_data function
        process_func = next(f for f in module_info.functions if f.name == "process_data")

        # Should have parameter details at L3
        assert len(process_func.parameters) > 0

        # Find the 'items' parameter
        items_param = next(p for p in process_func.parameters if p.name == "items")
        assert items_param.annotation is not None
