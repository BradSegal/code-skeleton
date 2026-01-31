"""Unit tests for the tree-sitter parser."""

from pathlib import Path

import pytest

from anatomize.core.parser import PythonParser

pytestmark = pytest.mark.unit


@pytest.fixture
def parser() -> PythonParser:
    """Create a parser instance."""
    return PythonParser()


@pytest.fixture
def sample_code() -> str:
    """Sample Python code for testing."""
    return '''
"""Module docstring."""

from typing import Any


class MyClass:
    """A sample class."""

    def __init__(self, name: str) -> None:
        """Initialize the class."""
        self.name = name

    def get_name(self) -> str:
        """Get the name."""
        return self.name

    async def async_method(self) -> None:
        """An async method."""
        pass


def standalone_function(x: int, y: int = 10) -> int:
    """Add two numbers."""
    return x + y


@property
def decorated_function() -> str:
    """A decorated function."""
    return "hello"
'''


class TestPythonParser:
    """Tests for PythonParser."""

    def test_parse_source(self, parser: PythonParser, sample_code: str) -> None:
        """Test parsing source code."""
        tree = parser.parse(sample_code)
        assert tree is not None
        assert tree.root_node is not None
        assert tree.root_node.type == "module"

    def test_get_node_text(self, parser: PythonParser, sample_code: str) -> None:
        """Test extracting node text."""
        tree = parser.parse(sample_code)
        # Find the class definition
        classes = parser.iter_children(tree.root_node, "class_definition")
        assert len(classes) == 1
        name = parser.get_name(classes[0])
        assert name == "MyClass"

    def test_iter_children(self, parser: PythonParser, sample_code: str) -> None:
        """Test iterating over specific child types."""
        tree = parser.parse(sample_code)
        functions = parser.iter_children(tree.root_node, "function_definition")
        # Should find standalone_function and decorated_function (not methods)
        assert len(functions) >= 1

    def test_get_docstring(self, parser: PythonParser, sample_code: str) -> None:
        """Test extracting docstrings."""
        tree = parser.parse(sample_code)
        classes = parser.iter_children(tree.root_node, "class_definition")
        doc = parser.get_docstring(classes[0])
        assert doc == "A sample class."

    def test_get_decorators(self, parser: PythonParser) -> None:
        """Test extracting decorators."""
        code = '''
@dataclass
@other_decorator
class MyClass:
    """A class."""
    pass
'''
        tree = parser.parse(code)
        decorated = parser.iter_children(tree.root_node, "decorated_definition")
        assert len(decorated) == 1

        # Find class inside decorated_definition
        for child in decorated[0].children:
            if child.type == "class_definition":
                decorators = parser.get_decorators(child)
                assert "dataclass" in decorators
                assert "other_decorator" in decorators

    def test_get_bases(self, parser: PythonParser) -> None:
        """Test extracting base classes."""
        code = '''
class MyClass(BaseClass, Mixin):
    """A class with bases."""
    pass
'''
        tree = parser.parse(code)
        classes = parser.iter_children(tree.root_node, "class_definition")
        bases = parser.get_bases(classes[0])
        assert "BaseClass" in bases
        assert "Mixin" in bases

    def test_get_parameters(self, parser: PythonParser) -> None:
        """Test extracting function parameters."""
        code = '''
def my_func(x: int, y: str = "default", *args, **kwargs) -> bool:
    """A function with various parameters."""
    pass
'''
        tree = parser.parse(code)
        funcs = parser.iter_children(tree.root_node, "function_definition")
        params = parser.get_parameters(funcs[0])

        param_names = [p[0] for p in params]
        assert "x" in param_names
        assert "y" in param_names
        assert "args" in param_names
        assert "kwargs" in param_names

    def test_get_parameters_positional_only_and_keyword_only(self, parser: PythonParser) -> None:
        code = '''
def f(a, b, /, c, *, d, e=1, *args: int, **kwargs: str):
    pass
'''
        tree = parser.parse(code)
        funcs = parser.iter_children(tree.root_node, "function_definition")
        params = parser.get_parameters(funcs[0])
        kinds = [p[3] for p in params]
        assert "POSITIONAL_ONLY_SEPARATOR" in kinds
        assert "KEYWORD_ONLY_SEPARATOR" in kinds

        # typed *args/**kwargs should be recognized as VAR_POSITIONAL / VAR_KEYWORD
        by_name = {p[0]: p for p in params if p[0] not in ("/", "*")}
        assert by_name["args"][3] == "VAR_POSITIONAL"
        assert by_name["kwargs"][3] == "VAR_KEYWORD"

    def test_get_return_type(self, parser: PythonParser) -> None:
        """Test extracting return type annotations."""
        code = '''
def my_func() -> int:
    return 42
'''
        tree = parser.parse(code)
        funcs = parser.iter_children(tree.root_node, "function_definition")
        return_type = parser.get_return_type(funcs[0])
        assert return_type == "int"


class TestParseFile:
    """Tests for parsing files."""

    def test_parse_fixture_file(self, parser: PythonParser) -> None:
        """Test parsing a real fixture file."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "sample_package" / "models.py"
        if fixture_path.exists():
            tree = parser.parse_file(fixture_path)
            assert tree is not None

            # Check we can find classes
            classes = parser.iter_children(tree.root_node, "class_definition")
            class_names = [parser.get_name(c) for c in classes]
            assert "BaseModel" in class_names
            assert "DerivedModel" in class_names

    def test_parse_nonexistent_file(self, parser: PythonParser) -> None:
        """Test parsing a nonexistent file raises error."""
        with pytest.raises(FileNotFoundError):
            parser.parse_file(Path("/nonexistent/file.py"))
