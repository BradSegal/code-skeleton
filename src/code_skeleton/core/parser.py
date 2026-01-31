"""Tree-sitter based parser for Python source code.

This module provides a wrapper around tree-sitter for parsing Python files
and extracting structural information.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

if TYPE_CHECKING:
    from tree_sitter import Tree

logger = logging.getLogger(__name__)


class PythonParser:
    """Tree-sitter parser for Python source code.

    This class wraps tree-sitter to provide a clean interface for parsing
    Python files and navigating the resulting syntax tree.

    Example
    -------
    >>> parser = PythonParser()
    >>> tree = parser.parse_file(Path("src/module.py"))
    >>> for node in parser.iter_children(tree.root_node, "class_definition"):
    ...     print(parser.get_node_text(node))
    """

    def __init__(self) -> None:
        """Initialize the Python parser."""
        # tree-sitter 0.21.x API
        self._language = Language(tspython.language(), "python")
        self._parser = Parser()
        self._parser.set_language(self._language)
        self._current_source: bytes | None = None

    def parse(self, source: str | bytes) -> Tree:
        """Parse Python source code.

        Parameters
        ----------
        source
            Python source code as string or bytes.

        Returns
        -------
        Tree
            Parsed syntax tree.
        """
        if isinstance(source, str):
            source = source.encode("utf-8")
        self._current_source = source
        return self._parser.parse(source)

    def parse_file(self, path: Path) -> Tree:
        """Parse a Python file.

        Parameters
        ----------
        path
            Path to the Python file.

        Returns
        -------
        Tree
            Parsed syntax tree.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        """
        content = path.read_bytes()
        return self.parse(content)

    def get_node_text(self, node: Node, source: bytes | None = None) -> str:
        """Get the text content of a node.

        Parameters
        ----------
        node
            Tree-sitter node.
        source
            Original source bytes (required if node doesn't have text).

        Returns
        -------
        str
            Text content of the node.
        """
        if node.text is not None:
            return node.text.decode("utf-8")

        if source is None:
            source = self._current_source
        if source is None:
            raise ValueError("Source bytes required to extract node text")

        return source[node.start_byte : node.end_byte].decode("utf-8")

    def iter_children(
        self, node: Node, *types: str, recursive: bool = False
    ) -> list[Node]:
        """Iterate over children of specific types.

        Parameters
        ----------
        node
            Parent node to search.
        *types
            Node type names to match.
        recursive
            If True, search recursively through all descendants.

        Returns
        -------
        list[Node]
            List of matching child nodes.
        """
        results: list[Node] = []
        self._collect_children(node, types, recursive, results)
        return results

    def _collect_children(
        self,
        node: Node,
        types: tuple[str, ...],
        recursive: bool,
        results: list[Node],
    ) -> None:
        """Recursively collect matching children."""
        for child in node.children:
            if child.type in types:
                results.append(child)
            if recursive:
                self._collect_children(child, types, recursive, results)

    def find_first(self, node: Node, *types: str) -> Node | None:
        """Find the first child of given types.

        Parameters
        ----------
        node
            Parent node to search.
        *types
            Node type names to match.

        Returns
        -------
        Node or None
            First matching child or None.
        """
        for child in node.children:
            if child.type in types:
                return child
        return None

    def get_docstring(self, node: Node) -> str | None:
        """Extract docstring from a definition node.

        Parameters
        ----------
        node
            Function or class definition node.

        Returns
        -------
        str or None
            First line of docstring if present.
        """
        # Look for block/body child
        body = self.find_first(node, "block")
        if body is None:
            return None

        # First child of block should be expression_statement with string
        for child in body.children:
            if child.type == "expression_statement":
                string_node = self.find_first(child, "string")
                if string_node is not None:
                    text = self.get_node_text(string_node)
                    return self._extract_docstring_first_line(text)
                break
            # Skip pass statements, comments, etc.
            if child.type not in ("comment", "pass_statement"):
                break
        return None

    def _extract_docstring_first_line(self, text: str) -> str:
        """Extract first line from a docstring.

        Parameters
        ----------
        text
            Full docstring text including quotes.

        Returns
        -------
        str
            First meaningful line of the docstring.
        """
        # Remove quotes
        if text.startswith('"""') or text.startswith("'''"):
            text = text[3:-3]
        elif text.startswith('"') or text.startswith("'"):
            text = text[1:-1]

        # Get first non-empty line
        lines = text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line:
                return line
        return ""

    def get_name(self, node: Node) -> str:
        """Get the name from a definition node.

        Parameters
        ----------
        node
            Definition node (function, class, etc.).

        Returns
        -------
        str
            Name of the defined entity.
        """
        name_node = self.find_first(node, "identifier")
        if name_node is not None:
            return self.get_node_text(name_node)
        return ""

    def get_decorators(self, node: Node) -> list[str]:
        """Get decorator names from a definition node.

        Parameters
        ----------
        node
            Function or class definition node.

        Returns
        -------
        list[str]
            List of decorator names.
        """
        decorators: list[str] = []

        # Check if parent is decorated_definition
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            for child in parent.children:
                if child.type == "decorator":
                    # Get the decorator expression
                    expr = self.find_first(child, "identifier", "attribute", "call")
                    if expr is not None:
                        if expr.type == "call":
                            func = self.find_first(expr, "identifier", "attribute")
                            if func is not None:
                                decorators.append(self._get_attribute_name(func))
                        else:
                            decorators.append(self._get_attribute_name(expr))

        return decorators

    def _get_attribute_name(self, node: Node) -> str:
        """Get full attribute name (e.g., 'pytest.mark.parametrize').

        Parameters
        ----------
        node
            Identifier or attribute node.

        Returns
        -------
        str
            Full dotted name.
        """
        if node.type == "identifier":
            return self.get_node_text(node)
        elif node.type == "attribute":
            obj = self.find_first(node, "identifier", "attribute")
            attr = node.child_by_field_name("attribute")
            if obj is not None and attr is not None:
                return f"{self._get_attribute_name(obj)}.{self.get_node_text(attr)}"
        return ""

    def get_bases(self, node: Node) -> list[str]:
        """Get base class names from a class definition.

        Parameters
        ----------
        node
            Class definition node.

        Returns
        -------
        list[str]
            List of base class names.
        """
        bases: list[str] = []
        arg_list = self.find_first(node, "argument_list")
        if arg_list is not None:
            for child in arg_list.children:
                if child.type in ("identifier", "attribute"):
                    bases.append(self._get_attribute_name(child))
        return bases

    def get_parameters(self, node: Node) -> list[tuple[str, str | None, str | None, str]]:
        """Get function parameters.

        Parameters
        ----------
        node
            Function definition node.

        Returns
        -------
        list[tuple[str, str | None, str | None, str]]
            List of (name, annotation, default, kind) tuples.
        """
        params: list[tuple[str, str | None, str | None, str]] = []
        param_node = self.find_first(node, "parameters")
        if param_node is None:
            return params

        for child in param_node.children:
            if child.type == "identifier":
                # Simple parameter
                name = self.get_node_text(child)
                params.append((name, None, None, "POSITIONAL_OR_KEYWORD"))

            elif child.type == "typed_parameter":
                annotation = None
                type_node = child.child_by_field_name("type")
                if type_node:
                    annotation = self.get_node_text(type_node)

                # typed_parameter may wrap splat patterns for *args/**kwargs
                list_splat = self.find_first(child, "list_splat_pattern")
                dict_splat = self.find_first(child, "dictionary_splat_pattern")
                if list_splat is not None:
                    name_node = self.find_first(list_splat, "identifier")
                    name = self.get_node_text(name_node) if name_node else ""
                    params.append((name, annotation, None, "VAR_POSITIONAL"))
                elif dict_splat is not None:
                    name_node = self.find_first(dict_splat, "identifier")
                    name = self.get_node_text(name_node) if name_node else ""
                    params.append((name, annotation, None, "VAR_KEYWORD"))
                else:
                    name_node = self.find_first(child, "identifier")
                    name = self.get_node_text(name_node) if name_node else ""
                    params.append((name, annotation, None, "POSITIONAL_OR_KEYWORD"))

            elif child.type == "default_parameter":
                name = ""
                default = None
                name_node = self.find_first(child, "identifier")
                if name_node:
                    name = self.get_node_text(name_node)
                value_node = child.child_by_field_name("value")
                if value_node:
                    default = self.get_node_text(value_node)
                params.append((name, None, default, "POSITIONAL_OR_KEYWORD"))

            elif child.type == "typed_default_parameter":
                annotation = None
                default = None

                type_node = child.child_by_field_name("type")
                if type_node:
                    annotation = self.get_node_text(type_node)
                value_node = child.child_by_field_name("value")
                if value_node:
                    default = self.get_node_text(value_node)

                list_splat = self.find_first(child, "list_splat_pattern")
                dict_splat = self.find_first(child, "dictionary_splat_pattern")
                if list_splat is not None:
                    name_node = self.find_first(list_splat, "identifier")
                    name = self.get_node_text(name_node) if name_node else ""
                    params.append((name, annotation, default, "VAR_POSITIONAL"))
                elif dict_splat is not None:
                    name_node = self.find_first(dict_splat, "identifier")
                    name = self.get_node_text(name_node) if name_node else ""
                    params.append((name, annotation, default, "VAR_KEYWORD"))
                else:
                    name_node = self.find_first(child, "identifier")
                    name = self.get_node_text(name_node) if name_node else ""
                    params.append((name, annotation, default, "POSITIONAL_OR_KEYWORD"))

            elif child.type == "list_splat_pattern":
                # *args
                name_node = self.find_first(child, "identifier")
                if name_node:
                    name = self.get_node_text(name_node)
                    params.append((name, None, None, "VAR_POSITIONAL"))

            elif child.type == "dictionary_splat_pattern":
                # **kwargs
                name_node = self.find_first(child, "identifier")
                if name_node:
                    name = self.get_node_text(name_node)
                    params.append((name, None, None, "VAR_KEYWORD"))
            elif child.type == "positional_separator":
                # `/` positional-only separator
                params.append(("/", None, None, "POSITIONAL_ONLY_SEPARATOR"))
            elif child.type == "keyword_separator":
                # `*` keyword-only separator
                params.append(("*", None, None, "KEYWORD_ONLY_SEPARATOR"))

        return params

    def get_return_type(self, node: Node) -> str | None:
        """Get return type annotation from a function definition.

        Parameters
        ----------
        node
            Function definition node.

        Returns
        -------
        str or None
            Return type annotation as string.
        """
        return_type = node.child_by_field_name("return_type")
        if return_type is not None:
            return self.get_node_text(return_type)
        return None

    def is_async(self, node: Node) -> bool:
        """Check if a function is async.

        Parameters
        ----------
        node
            Function definition node.

        Returns
        -------
        bool
            True if the function is async.
        """
        # Check if it's async_function_definition
        if node.type == "async_function_definition":
            return True

        # In tree-sitter 0.21.x, async functions are function_definition
        # with an 'async' child node
        if node.type == "function_definition":
            for child in node.children:
                if child.type == "async":
                    return True

        # Also check parent for decorated async functions
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            for child in parent.children:
                if child.type == "async_function_definition":
                    return True
                if child.type == "function_definition":
                    for c in child.children:
                        if c.type == "async":
                            return True
        return False
