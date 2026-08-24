"""Exact lexical relationships and conservative duplicate candidates."""

from __future__ import annotations

import ast
import copy
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from anatomize._python_imports import imported_module
from anatomize.index.models import (
    DOCUMENTATION_TEXT_PROVIDER,
    PYTHON_AST_PROVIDER,
    DocumentationSection,
    DuplicateGroup,
    DuplicateKind,
    DuplicateMember,
    DuplicateNormalization,
    EvidenceConfidence,
    FileRole,
    OccurrenceKind,
    OccurrenceRecord,
    ReferenceCandidate,
    RelationshipKind,
    RelationshipRecord,
    SymbolRecord,
)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MIN_CLONE_NODES = 12
_MIN_CLONE_LINES = 3
_MIN_DOCUMENT_WORDS = 30


@dataclass(frozen=True)
class DefinitionFingerprints:
    exact_digest: str
    structural_digest: str
    node_count: int
    body_line_count: int


@dataclass(frozen=True)
class _ScopeInfo:
    kind: str
    aliases: dict[str, str]
    modules: dict[str, str]
    shadowed: set[str]


class _BindingCollector(ast.NodeVisitor):
    def __init__(self, *, module: str, is_package: bool) -> None:
        self.module = module
        self.is_package = is_package
        self.imported_names: dict[str, str] = {}
        self.imported_modules: dict[str, str] = {}
        self.other_bound: set[str] = set()
        self.definition_bound: set[str] = set()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        resolved = imported_module(
            self.module,
            is_package=self.is_package,
            imported=node.module,
            level=node.level,
        )
        if not resolved:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            self.imported_names[alias.asname or alias.name] = f"{resolved}.{alias.name}"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            imported = alias.name if alias.asname else alias.name.split(".", 1)[0]
            self.imported_modules[local] = imported

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definition_bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.definition_bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definition_bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.other_bound.add(node.id)


def definition_fingerprints(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> DefinitionFingerprints:
    """Return exact and definition-name-normalized AST identities."""
    exact = hashlib.sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()
    normalized = copy.deepcopy(node)
    normalized.name = "_"
    structural = hashlib.sha256(ast.dump(normalized, include_attributes=False).encode("utf-8")).hexdigest()
    return DefinitionFingerprints(
        exact_digest=exact,
        structural_digest=structural,
        node_count=sum(1 for _ in ast.walk(node)),
        body_line_count=max(1, (node.end_lineno or node.lineno) - node.lineno + 1),
    )


def python_reference_candidates(
    tree: ast.Module,
    *,
    module: str,
    path: str,
    symbols: list[SymbolRecord],
    is_package: bool,
    source_lines: list[str],
) -> list[ReferenceCandidate]:
    """Collect references whose target identity is established lexically."""
    visitor = _ReferenceVisitor(
        module=module,
        path=path,
        symbols=symbols,
        is_package=is_package,
        source_lines=source_lines,
    )
    visitor.visit(tree)
    unique = {item.candidate_id: item for item in visitor.candidates}
    return sorted(
        unique.values(),
        key=lambda item: (item.path, item.line, item.column, item.kind.value, item.target_qualified_name),
    )


class _ReferenceVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        module: str,
        path: str,
        symbols: list[SymbolRecord],
        is_package: bool,
        source_lines: list[str],
    ) -> None:
        self.module = module
        self.path = path
        self.is_package = is_package
        self.source_lines = source_lines
        self.module_symbols = {
            symbol.name: symbol.qualified_name
            for symbol in symbols
            if symbol.qualified_name.count(".") == module.count(".") + 1
        }
        self.symbols_by_location = {(symbol.line, symbol.column): symbol.symbol_id for symbol in symbols}
        self.scopes: list[_ScopeInfo] = []
        self.owners: list[str | None] = []
        self.classes: list[str | None] = []
        self.candidates: list[ReferenceCandidate] = []

    def visit_Module(self, node: ast.Module) -> None:
        self.scopes.append(self._scope("module", node.body, extra_aliases=self.module_symbols))
        self.owners.append(None)
        self.classes.append(None)
        for statement in node.body:
            self.visit(statement)
        self.owners.pop()
        self.classes.pop()
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = {
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        }
        if node.args.vararg is not None:
            arguments.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            arguments.add(node.args.kwarg.arg)
        self.scopes.append(self._scope("function", node.body, extra_shadowed=arguments))
        self.owners.append(self.symbols_by_location.get((node.lineno, node.col_offset)))
        for statement in node.body:
            self.visit(statement)
        self.owners.pop()
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes.append(self._scope("class", node.body))
        owner = self.symbols_by_location.get((node.lineno, node.col_offset))
        self.owners.append(owner)
        symbol = next((item for item in self.module_symbols.values() if item.endswith(f".{node.name}")), None)
        self.classes.append(symbol)
        for statement in node.body:
            self.visit(statement)
        self.classes.pop()
        self.owners.pop()
        self.scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        arguments = {argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        self.scopes.append(_ScopeInfo(kind="function", aliases={}, modules={}, shadowed=arguments))
        self.owners.append(self.owners[-1] if self.owners else None)
        self.visit(node.body)
        self.owners.pop()
        self.scopes.pop()

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        target = self._resolve_name(node.id)
        if target is not None:
            self._append_candidate(node, target, OccurrenceKind.REFERENCE)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name):
            if node.value.id in {"self", "cls"} and self.classes and self.classes[-1] is not None:
                self._append_candidate(node, f"{self.classes[-1]}.{node.attr}", OccurrenceKind.REFERENCE)
                return
            module = self._resolve_module(node.value.id)
            if module is not None:
                self._append_candidate(node, f"{module}.{node.attr}", OccurrenceKind.REFERENCE)
                return
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and (owner := self._resolve_name(node.value.func.id)) is not None
        ):
            self._append_candidate(node, f"{owner}.{node.attr}", OccurrenceKind.REFERENCE)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        resolved = imported_module(
            self.module,
            is_package=self.is_package,
            imported=node.module,
            level=node.level,
        )
        if not resolved:
            return
        for alias in node.names:
            if alias.name != "*":
                self._append_candidate(
                    alias,
                    f"{resolved}.{alias.name}",
                    OccurrenceKind.IMPORT,
                    alias_qualified_name=(
                        f"{self.module}.{alias.asname or alias.name}" if self.is_package else None
                    ),
                )

    def _scope(
        self,
        kind: str,
        body: list[ast.stmt],
        *,
        extra_aliases: dict[str, str] | None = None,
        extra_shadowed: set[str] | None = None,
    ) -> _ScopeInfo:
        collector = _BindingCollector(module=self.module, is_package=self.is_package)
        for statement in body:
            collector.visit(statement)
        aliases = dict(extra_aliases or {})
        modules: dict[str, str] = {}
        for name in tuple(aliases):
            if name in collector.other_bound or name in collector.imported_names or name in collector.imported_modules:
                aliases.pop(name)
        for name, target in collector.imported_names.items():
            if name not in collector.other_bound and name not in collector.definition_bound:
                aliases[name] = target
        for name, target in collector.imported_modules.items():
            if name not in collector.other_bound and name not in collector.definition_bound:
                modules[name] = target
        shadowed = collector.other_bound | collector.definition_bound | set(extra_shadowed or set())
        return _ScopeInfo(kind=kind, aliases=aliases, modules=modules, shadowed=shadowed)

    def _resolve_name(self, name: str) -> str | None:
        for scope in reversed(self.scopes):
            if scope.kind == "class" and self.scopes[-1].kind == "function":
                continue
            if name in scope.aliases:
                return scope.aliases[name]
            if name in scope.shadowed:
                return None
        return None

    def _resolve_module(self, name: str) -> str | None:
        for scope in reversed(self.scopes):
            if scope.kind == "class" and self.scopes[-1].kind == "function":
                continue
            if name in scope.modules:
                return scope.modules[name]
            if name in scope.shadowed:
                return None
        return None

    def _append_candidate(
        self,
        node: ast.AST,
        target: str,
        kind: OccurrenceKind,
        *,
        alias_qualified_name: str | None = None,
    ) -> None:
        line = getattr(node, "lineno", 1)
        column = _codepoint_column(self.source_lines, line, getattr(node, "col_offset", 0))
        end_line = getattr(node, "end_lineno", line) or line
        raw_end_column = getattr(node, "end_col_offset", 0) or 0
        end_column = _codepoint_column(self.source_lines, end_line, raw_end_column)
        identity = f"{self.path}:{line}:{column}:{kind.value}:{target}"
        self.candidates.append(
            ReferenceCandidate(
                candidate_id=f"candidate:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}",
                target_qualified_name=target,
                path=self.path,
                kind=kind,
                line=line,
                end_line=end_line,
                column=column,
                end_column=end_column,
                enclosing_symbol_id=self.owners[-1] if self.owners else None,
                alias_qualified_name=alias_qualified_name,
            )
        )


def _codepoint_column(lines: list[str], line: int, byte_column: int) -> int:
    """Convert CPython's UTF-8 byte offset to a Unicode codepoint offset."""
    if line < 1 or line > len(lines):
        return byte_column
    raw = lines[line - 1].encode("utf-8")[:byte_column]
    return len(raw.decode("utf-8", errors="ignore"))


def resolve_symbol_facts(
    symbols: list[SymbolRecord],
    candidates: list[ReferenceCandidate],
) -> tuple[list[OccurrenceRecord], int]:
    """Resolve lexical candidates against exact current symbol identities."""
    by_qualified: dict[str, list[SymbolRecord]] = {}
    for symbol in symbols:
        by_qualified.setdefault(symbol.qualified_name, []).append(symbol)
    occurrences = [
        OccurrenceRecord(
            occurrence_id=f"occurrence:{symbol.symbol_id}:definition",
            symbol_id=symbol.symbol_id,
            path=symbol.path,
            kind=OccurrenceKind.DEFINITION,
            line=symbol.line,
            end_line=symbol.end_line,
            column=symbol.column,
            end_column=symbol.end_column,
            provider_id=symbol.provider_id,
            confidence=EvidenceConfidence.EXACT,
        )
        for symbol in symbols
    ]
    aliases: dict[str, str] = {}
    for candidate in candidates:
        if candidate.alias_qualified_name is None:
            continue
        targets = by_qualified.get(candidate.target_qualified_name, [])
        if targets:
            aliases[candidate.alias_qualified_name] = max(
                targets,
                key=lambda item: (item.line, item.symbol_id),
            ).qualified_name
    unresolved = 0
    for candidate in candidates:
        qualified = aliases.get(candidate.target_qualified_name, candidate.target_qualified_name)
        targets = by_qualified.get(qualified, [])
        if not targets:
            unresolved += 1
            continue
        # Python binds repeated top-level definitions to the final declaration.
        # Preserve every declaration, but point users at the effective binding.
        target = max(targets, key=lambda item: (item.line, item.symbol_id))
        occurrences.append(
            OccurrenceRecord(
                occurrence_id=f"occurrence:{candidate.candidate_id}",
                symbol_id=target.symbol_id,
                path=candidate.path,
                kind=candidate.kind,
                line=candidate.line,
                end_line=candidate.end_line,
                column=candidate.column,
                end_column=candidate.end_column,
                enclosing_symbol_id=candidate.enclosing_symbol_id,
                provider_id=candidate.provider_id,
                confidence=EvidenceConfidence.EXACT,
            )
        )
    occurrences = sorted(
        occurrences,
        key=lambda item: (item.path, item.line, item.column, item.kind.value, item.symbol_id),
    )
    return occurrences, unresolved


def relationship_from_occurrence(occurrence: OccurrenceRecord) -> RelationshipRecord:
    """Project the edge encoded by an occurrence without storing it twice."""
    return RelationshipRecord(
        relationship_id=f"relationship:{occurrence.occurrence_id}",
        source_id=occurrence.enclosing_symbol_id or f"file:{occurrence.path}",
        target_id=occurrence.symbol_id,
        kind=(
            RelationshipKind.DEFINES
            if occurrence.kind is OccurrenceKind.DEFINITION
            else RelationshipKind.IMPORTS
            if occurrence.kind is OccurrenceKind.IMPORT
            else RelationshipKind.REFERENCES
        ),
        occurrence_id=occurrence.occurrence_id,
        provider_id=occurrence.provider_id,
        confidence=occurrence.confidence,
        reason=f"{occurrence.kind.value} established by {occurrence.provider_id}",
    )


def markdown_sections(source: Path, *, path: str) -> list[DocumentationSection]:
    """Extract deterministic heading-bounded Markdown sections."""
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Failed to read Markdown source: {source}") from error
    headings = [(index, match.group(2).strip()) for index, line in enumerate(lines) if (match := _HEADING.match(line))]
    sections: list[DocumentationSection] = []
    for offset, (start, heading) in enumerate(headings):
        end = headings[offset + 1][0] if offset + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        normalized = " ".join(body.split())
        if not normalized:
            continue
        word_count = len(normalized.split())
        sections.append(
            DocumentationSection(
                section_id=f"section:{path}:{start + 1}",
                path=path,
                heading=heading,
                line=start + 1,
                end_line=max(start + 1, end),
                digest=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                normalized_digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                word_count=word_count,
                provider_id=DOCUMENTATION_TEXT_PROVIDER,
            )
        )
    return sections


def documentation_reference_facts(
    root: Path,
    sections: list[DocumentationSection],
    symbols: list[SymbolRecord],
) -> list[OccurrenceRecord]:
    """Link exact documentation mentions to unambiguous Python symbols.

    These are conservative lexical references. They localise review context;
    they do not claim that prose is a behavioural consumer.
    """
    symbols_by_name: dict[str, list[SymbolRecord]] = {}
    symbols_by_qualified_name: dict[str, list[SymbolRecord]] = {}
    for symbol in symbols:
        symbols_by_name.setdefault(symbol.name, []).append(symbol)
        symbols_by_qualified_name.setdefault(symbol.qualified_name, []).append(symbol)
    unique_names = {
        name: matches[0]
        for name, matches in symbols_by_name.items()
        if len(matches) == 1 and len(name) >= 8
    }
    occurrences: list[OccurrenceRecord] = []
    lines_by_path: dict[str, list[str]] = {}
    reference_pattern = re.compile(r"(?<!\w)(?:[^\W\d]\w*)(?:\.(?:[^\W\d]\w*))*", re.UNICODE)
    for section in sections:
        lines = lines_by_path.get(section.path)
        if lines is None:
            try:
                lines = (root / section.path).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError) as error:
                raise ValueError(f"Failed to read Markdown source: {root / section.path}") from error
            lines_by_path[section.path] = lines
        section_lines = lines[section.line - 1 : section.end_line]
        section_text = "\n".join(section_lines)
        matched: dict[str, tuple[int, str, SymbolRecord]] = {}
        for match in reference_pattern.finditer(section_text):
            term = match.group(0)
            short_name = term.rsplit(".", 1)[-1]
            referenced = symbols_by_qualified_name.get(term) or (
                [unique_names[short_name]] if short_name in unique_names else []
            )
            for symbol in referenced:
                matched.setdefault(symbol.symbol_id, (match.start(), term, symbol))
        for symbol_id, (offset, term, symbol) in sorted(
            matched.items(),
            key=lambda item: (item[1][0], item[0]),
        )[:16]:
            prefix = section_text[:offset]
            relative_line = prefix.count("\n")
            column = offset - (prefix.rfind("\n") + 1)
            line = section.line + relative_line
            key = hashlib.sha256(
                f"{section.section_id}\0{symbol_id}\0{line}\0{column}".encode()
            ).hexdigest()[:24]
            occurrence = OccurrenceRecord(
                occurrence_id=f"occurrence:documentation:{key}",
                symbol_id=symbol_id,
                path=section.path,
                kind=OccurrenceKind.REFERENCE,
                line=line,
                end_line=line,
                column=column,
                end_column=column + len(term),
                enclosing_symbol_id=section.section_id,
                provider_id=DOCUMENTATION_TEXT_PROVIDER,
                confidence=EvidenceConfidence.CONSERVATIVE,
            )
            occurrences.append(occurrence)
    return sorted(occurrences, key=lambda item: (item.path, item.line, item.column, item.symbol_id))


def build_duplicate_groups(
    symbols: list[SymbolRecord],
    sections: list[DocumentationSection],
) -> list[DuplicateGroup]:
    """Group conservative code and documentation duplicate candidates."""
    groups: list[DuplicateGroup] = []
    code_by_digest: dict[str, list[SymbolRecord]] = {}
    for symbol in symbols:
        if symbol.node_count >= _MIN_CLONE_NODES and symbol.body_line_count >= _MIN_CLONE_LINES:
            code_by_digest.setdefault(symbol.digest, []).append(symbol)
    for digest, code_members in sorted(code_by_digest.items()):
        if len(code_members) < 2:
            continue
        roles = [FileRole.TEST if _is_test_path(item.path) else FileRole.SOURCE for item in code_members]
        kind = DuplicateKind.TEST if all(role is FileRole.TEST for role in roles) else DuplicateKind.IMPLEMENTATION
        normalization = (
            DuplicateNormalization.EXACT_AST
            if len({item.exact_digest for item in code_members}) == 1
            else DuplicateNormalization.DEFINITION_NAME
        )
        names = sorted({item.name for item in code_members})
        groups.append(
            DuplicateGroup(
                group_id=f"duplicate:{kind.value}:{digest[:24]}",
                kind=kind,
                normalization=normalization,
                members=[
                    DuplicateMember(
                        member_id=item.symbol_id,
                        symbol_id=item.symbol_id,
                        path=item.path,
                        line=item.line,
                        end_line=item.end_line,
                        role=role,
                        size_lines=item.body_line_count,
                        size_units=item.node_count,
                        exact_digest=item.exact_digest,
                        normalized_digest=item.digest,
                    )
                    for item, role in sorted(
                        zip(code_members, roles), key=lambda pair: (pair[0].path, pair[0].line)
                    )
                ],
                differences=([f"Definition names differ: {', '.join(names)}"] if len(names) > 1 else []),
                provider_id=PYTHON_AST_PROVIDER,
                confidence=EvidenceConfidence.CONSERVATIVE,
            )
        )

    docs_by_digest: dict[str, list[DocumentationSection]] = {}
    for section in sections:
        if section.word_count >= _MIN_DOCUMENT_WORDS:
            docs_by_digest.setdefault(section.normalized_digest, []).append(section)
    for digest, document_members in sorted(docs_by_digest.items()):
        if len(document_members) < 2:
            continue
        headings = sorted({item.heading for item in document_members})
        groups.append(
            DuplicateGroup(
                group_id=f"duplicate:documentation:{digest[:24]}",
                kind=DuplicateKind.DOCUMENTATION,
                normalization=DuplicateNormalization.MARKDOWN_BODY_WHITESPACE,
                members=[
                    DuplicateMember(
                        member_id=item.section_id,
                        section_id=item.section_id,
                        path=item.path,
                        line=item.line,
                        end_line=item.end_line,
                        role=FileRole.DOCUMENTATION,
                        size_lines=item.end_line - item.line + 1,
                        size_units=item.word_count,
                        exact_digest=item.digest,
                        normalized_digest=item.normalized_digest,
                    )
                    for item in sorted(document_members, key=lambda section: (section.path, section.line))
                ],
                differences=[
                    *([f"Section headings differ: {', '.join(headings)}"] if len(headings) > 1 else []),
                    *(["Body whitespace differs"] if len({item.digest for item in document_members}) > 1 else []),
                ],
                provider_id=DOCUMENTATION_TEXT_PROVIDER,
                confidence=EvidenceConfidence.CONSERVATIVE,
            )
        )
    return sorted(groups, key=lambda item: (item.kind.value, item.group_id))


def _is_test_path(path: str) -> bool:
    candidate = Path(path)
    return "tests" in candidate.parts or candidate.name.startswith("test_")
