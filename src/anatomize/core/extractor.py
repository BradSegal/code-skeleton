"""Symbol extraction from parsed Python source code.

This module provides the logic to extract structured information from
tree-sitter parsed Python code, converting AST nodes into typed data models.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from anatomize.core.parser import PythonParser
from anatomize.core.types import (
    AttributeInfo,
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    ParameterInfo,
    ResolutionLevel,
)

if TYPE_CHECKING:
    from tree_sitter import Node

logger = logging.getLogger(__name__)


class SymbolExtractor:
    """Extract symbols from Python source files.

    This class coordinates with PythonParser to extract classes, functions,
    and other symbols from Python source code at various resolution levels.

    Parameters
    ----------
    resolution
        Level of detail to extract.

    Example
    -------
    >>> extractor = SymbolExtractor(resolution=ResolutionLevel.MODULES)
    >>> module_info = extractor.extract_module(Path("src/module.py"), "mypackage.module")
    >>> print(module_info.classes)
    """

    def __init__(self, resolution: ResolutionLevel = ResolutionLevel.MODULES) -> None:
        """Initialize the symbol extractor.

        Parameters
        ----------
        resolution
            Level of detail to extract.
        """
        self._parser = PythonParser()
        self._resolution = resolution

    def extract_module(self, path: Path, module_name: str, *, relative_path: str, source: int) -> ModuleInfo:
        """Extract module information from a Python file.

        Parameters
        ----------
        path
            Path to the Python file.
        module_name
            Fully qualified module name.
        relative_path
            Module path relative to its source root (POSIX style).
        source
            Index of the source root this module belongs to.

        Returns
        -------
        ModuleInfo
            Extracted module information.
        """
        tree = self._parser.parse_file(path)
        root = tree.root_node
        if root.has_error:
            raise ValueError(f"Parse errors encountered in {path}")

        # Extract module docstring
        doc = self._get_module_docstring(root)

        # Extract classes
        classes: list[ClassInfo] = []
        for node in self._parser.iter_children(root, "class_definition"):
            class_info = self._extract_class(node)
            if class_info is not None:
                classes.append(class_info)

        # Also check for decorated classes
        for decorated in self._parser.iter_children(root, "decorated_definition"):
            for child in decorated.children:
                if child.type == "class_definition":
                    class_info = self._extract_class(child)
                    if class_info is not None:
                        classes.append(class_info)

        # Extract top-level functions (including async)
        functions: list[FunctionInfo] = []
        for node in self._parser.iter_children(root, "function_definition", "async_function_definition"):
            func_info = self._extract_function(node, is_method=False)
            if func_info is not None:
                functions.append(func_info)

        # Also check for decorated functions
        for decorated in self._parser.iter_children(root, "decorated_definition"):
            for child in decorated.children:
                if child.type in ("function_definition", "async_function_definition"):
                    func_info = self._extract_function(child, is_method=False)
                    if func_info is not None:
                        functions.append(func_info)

        # Extract imports (for L3)
        imports: list[str] = []
        if self._resolution == ResolutionLevel.SIGNATURES:
            imports = self._extract_imports(root)

        # Extract constants (for L3)
        constants: list[AttributeInfo] = []
        if self._resolution == ResolutionLevel.SIGNATURES:
            constants = self._extract_module_constants(root)

        return ModuleInfo(
            path=relative_path,
            name=module_name,
            doc=doc,
            classes=classes,
            functions=functions,
            imports=imports,
            constants=constants,
            source=source,
        )

    def _get_module_docstring(self, root: Node) -> str | None:
        """Extract module-level docstring.

        Parameters
        ----------
        root
            Root node of the module.

        Returns
        -------
        str or None
            First line of module docstring.
        """
        # Module docstring is the first expression_statement with a string
        for child in root.children:
            if child.type == "expression_statement":
                string_node = self._parser.find_first(child, "string")
                if string_node is not None:
                    text = self._parser.get_node_text(string_node)
                    return self._extract_first_line(text)
                break
            # Skip comments and other statements
            if child.type not in ("comment",):
                break
        return None

    def _extract_first_line(self, text: str) -> str:
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

    def _extract_class(self, node: Node) -> ClassInfo | None:
        """Extract class information.

        Parameters
        ----------
        node
            Class definition node.

        Returns
        -------
        ClassInfo or None
            Extracted class information.
        """
        name = self._parser.get_name(node)
        if not name:
            return None

        doc = self._parser.get_docstring(node)
        decorators = self._parser.get_decorators(node)
        bases = self._parser.get_bases(node)
        line = node.start_point[0] + 1  # 1-indexed

        # Check for special class types
        is_dataclass = "dataclass" in decorators or "dataclasses.dataclass" in decorators
        is_pydantic = any(
            base in ("BaseModel", "BaseSettings", "pydantic.BaseModel")
            for base in bases
        )

        # Extract methods
        methods: list[FunctionInfo] = []
        if self._resolution in (ResolutionLevel.MODULES, ResolutionLevel.SIGNATURES):
            methods = self._extract_class_methods(node)

        # Extract attributes (L3 only)
        attributes: list[AttributeInfo] = []
        if self._resolution == ResolutionLevel.SIGNATURES:
            attributes = self._extract_class_attributes(node)

        return ClassInfo(
            name=name,
            line=line,
            doc=doc,
            decorators=decorators,
            bases=bases,
            methods=methods,
            attributes=attributes,
            is_dataclass=is_dataclass,
            is_pydantic=is_pydantic,
        )

    def _extract_class_methods(self, class_node: Node) -> list[FunctionInfo]:
        """Extract methods from a class.

        Parameters
        ----------
        class_node
            Class definition node.

        Returns
        -------
        list[FunctionInfo]
            List of method information.
        """
        methods: list[FunctionInfo] = []

        # Find the class body
        body = self._parser.find_first(class_node, "block")
        if body is None:
            return methods

        # Extract regular methods
        for node in self._parser.iter_children(body, "function_definition"):
            func_info = self._extract_function(node, is_method=True)
            if func_info is not None:
                methods.append(func_info)

        # Extract decorated methods
        for decorated in self._parser.iter_children(body, "decorated_definition"):
            for child in decorated.children:
                if child.type in ("function_definition", "async_function_definition"):
                    func_info = self._extract_function(child, is_method=True)
                    if func_info is not None:
                        methods.append(func_info)

        # Extract async methods
        for node in self._parser.iter_children(body, "async_function_definition"):
            func_info = self._extract_function(node, is_method=True)
            if func_info is not None:
                methods.append(func_info)

        return methods

    def _extract_class_attributes(self, class_node: Node) -> list[AttributeInfo]:
        """Extract class attributes (for L3).

        Parameters
        ----------
        class_node
            Class definition node.

        Returns
        -------
        list[AttributeInfo]
            List of attribute information.
        """
        attributes: list[AttributeInfo] = []

        body = self._parser.find_first(class_node, "block")
        if body is None:
            return attributes

        for child in body.children:
            # Simple assignment: x = value
            if child.type == "expression_statement":
                assign = self._parser.find_first(child, "assignment")
                if assign is not None:
                    attr = self._extract_assignment_attribute(assign)
                    if attr is not None:
                        attributes.append(attr)

            # Annotated assignment: x: Type = value
            elif child.type == "annotated_assignment":
                attr = self._extract_annotated_attribute(child)
                if attr is not None:
                    attributes.append(attr)

        return attributes

    def _extract_assignment_attribute(self, node: Node) -> AttributeInfo | None:
        """Extract attribute from simple assignment.

        Parameters
        ----------
        node
            Assignment node.

        Returns
        -------
        AttributeInfo or None
            Extracted attribute information.
        """
        # Get the left side (identifier)
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return None

        name = self._parser.get_node_text(left)
        if not name or name.startswith("_"):
            return None

        # Get the right side (value)
        right = node.child_by_field_name("right")
        default = None
        if right is not None:
            default = self._parser.get_node_text(right)

        return AttributeInfo(
            name=name,
            line=node.start_point[0] + 1,
            annotation=None,
            default=default,
        )

    def _extract_annotated_attribute(self, node: Node) -> AttributeInfo | None:
        """Extract attribute from annotated assignment.

        Parameters
        ----------
        node
            Annotated assignment node.

        Returns
        -------
        AttributeInfo or None
            Extracted attribute information.
        """
        # Get the name
        name_node = self._parser.find_first(node, "identifier")
        if name_node is None:
            return None

        name = self._parser.get_node_text(name_node)
        if not name:
            return None

        # Get the type annotation
        type_node = node.child_by_field_name("type")
        annotation = None
        if type_node is not None:
            annotation = self._parser.get_node_text(type_node)

        # Get the default value
        value_node = node.child_by_field_name("value")
        default = None
        if value_node is not None:
            default = self._parser.get_node_text(value_node)

        return AttributeInfo(
            name=name,
            line=node.start_point[0] + 1,
            annotation=annotation,
            default=default,
        )

    def _extract_function(self, node: Node, *, is_method: bool) -> FunctionInfo | None:
        """Extract function information.

        Parameters
        ----------
        node
            Function definition node.
        is_method
            Whether this is a method.

        Returns
        -------
        FunctionInfo or None
            Extracted function information.
        """
        name = self._parser.get_name(node)
        if not name:
            return None

        doc = self._parser.get_docstring(node)
        decorators = self._parser.get_decorators(node)
        line = node.start_point[0] + 1  # 1-indexed
        is_async = self._parser.is_async(node) or node.type == "async_function_definition"

        # Build signature
        params = self._parser.get_parameters(node)
        returns = self._parser.get_return_type(node)

        # Build signature string
        signature = self._build_signature(params, returns)

        # Build parameter list (for L3)
        parameters: list[ParameterInfo] = []
        if self._resolution == ResolutionLevel.SIGNATURES:
            parameters = self._build_parameter_infos(params)

        return FunctionInfo(
            name=name,
            line=line,
            doc=doc,
            signature=signature,
            returns=returns,
            decorators=decorators,
            is_async=is_async,
            is_method=is_method,
            parameters=parameters,
        )

    def _build_parameter_infos(
        self, params: list[tuple[str, str | None, str | None, str]]
    ) -> list[ParameterInfo]:
        """Build ParameterInfo list from parsed parameter parts."""
        # Determine positional-only parameters (everything before '/')
        positional_separator_index = next(
            (i for i, (_, _, _, kind) in enumerate(params) if kind == "POSITIONAL_ONLY_SEPARATOR"),
            None,
        )
        positional_only_names: set[str] = set()
        if positional_separator_index is not None:
            for name, _, _, kind in params[:positional_separator_index]:
                if kind == "POSITIONAL_OR_KEYWORD" and name:
                    positional_only_names.add(name)

        keyword_only_mode = False
        infos: list[ParameterInfo] = []
        for name, annotation, default, kind in params:
            if kind in ("POSITIONAL_ONLY_SEPARATOR", "KEYWORD_ONLY_SEPARATOR"):
                if kind == "KEYWORD_ONLY_SEPARATOR":
                    keyword_only_mode = True
                continue

            if kind == "VAR_POSITIONAL":
                keyword_only_mode = True
                infos.append(
                    ParameterInfo(
                        name=name,
                        annotation=annotation,
                        default=default,
                        kind="VAR_POSITIONAL",
                    )
                )
                continue

            if kind == "VAR_KEYWORD":
                infos.append(
                    ParameterInfo(
                        name=name,
                        annotation=annotation,
                        default=default,
                        kind="VAR_KEYWORD",
                    )
                )
                continue

            if not name:
                # Tree-sitter returned an unexpected shape; fail hard.
                raise ValueError("Encountered unnamed parameter while extracting signature")

            if keyword_only_mode:
                resolved_kind = "KEYWORD_ONLY"
            elif name in positional_only_names:
                resolved_kind = "POSITIONAL_ONLY"
            else:
                resolved_kind = "POSITIONAL_OR_KEYWORD"

            infos.append(
                ParameterInfo(
                    name=name,
                    annotation=annotation,
                    default=default,
                    kind=resolved_kind,
                )
            )

        return infos

    def _build_signature(
        self,
        params: list[tuple[str, str | None, str | None, str]],
        returns: str | None,
    ) -> str:
        """Build function signature string.

        Parameters
        ----------
        params
            List of (name, annotation, default, kind) tuples.
        returns
            Return type annotation.

        Returns
        -------
        str
            Formatted signature string.
        """
        parts: list[str] = []

        for name, annotation, default, kind in params:
            if kind in ("POSITIONAL_ONLY_SEPARATOR", "KEYWORD_ONLY_SEPARATOR"):
                parts.append(name)
                continue

            part = name
            if annotation:
                part = f"{name}: {annotation}"
            if default:
                part = f"{part} = {default}"
            if kind == "VAR_POSITIONAL":
                part = f"*{part}"
            elif kind == "VAR_KEYWORD":
                part = f"**{part}"
            parts.append(part)

        sig = f"({', '.join(parts)})"
        if returns:
            sig = f"{sig} -> {returns}"

        return sig

    def _extract_imports(self, root: Node) -> list[str]:
        """Extract import statements.

        Parameters
        ----------
        root
            Root node of the module.

        Returns
        -------
        list[str]
            List of import statements.
        """
        imports: list[str] = []

        for node in self._parser.iter_children(root, "import_statement", "import_from_statement"):
            text = self._parser.get_node_text(node)
            imports.append(text)

        return imports

    def _extract_module_constants(self, root: Node) -> list[AttributeInfo]:
        """Extract module-level constants.

        Parameters
        ----------
        root
            Root node of the module.

        Returns
        -------
        list[AttributeInfo]
            List of constant definitions.
        """
        constants: list[AttributeInfo] = []

        for child in root.children:
            # Look for UPPER_CASE assignments
            if child.type == "expression_statement":
                assign = self._parser.find_first(child, "assignment")
                if assign is not None:
                    left = assign.child_by_field_name("left")
                    if left is not None and left.type == "identifier":
                        name = self._parser.get_node_text(left)
                        if name.isupper():
                            right = assign.child_by_field_name("right")
                            default = None
                            if right is not None:
                                default = self._parser.get_node_text(right)
                            constants.append(
                                AttributeInfo(
                                    name=name,
                                    line=child.start_point[0] + 1,
                                    default=default,
                                )
                            )

        return constants
