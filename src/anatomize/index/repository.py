"""Build and query portable repository indexes."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import tempfile
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from anatomize.index.git import git_text, nul_paths
from anatomize.index.models import (
    ImpactNode,
    ImpactRelationship,
    ImpactReport,
    ImpactRole,
    ImportEdge,
    ModuleRecord,
    RepositoryIndex,
    SourceState,
    SymbolKind,
    SymbolRecord,
)

_EXCLUDED_PARTS = {
    ".anatomy",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
_DOCUMENT_SUFFIXES = {".md", ".qmd", ".rst"}
_CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}
_CONFIG_NAMES = {
    "Makefile",
    "Dockerfile",
    "environment.yml",
    "renv.lock",
}


def repository_path_included(path: Path) -> bool:
    """Return whether a relative path belongs in repository review surfaces."""
    return (
        not any(part in _EXCLUDED_PARTS for part in path.parts)
        and not any(part.endswith(".egg-info") for part in path.parts)
        and path.suffix not in {".pyc", ".pyo"}
    )


def build_repository_index(
    root: Path,
    *,
    python_roots: list[Path] | None = None,
) -> RepositoryIndex:
    """Build a deterministic Python-aware repository index."""
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Repository root must be an existing directory: {root}")

    files = _repository_files(root)
    python_files = [path for path in files if _is_python_source(path)]
    roots = _resolve_python_roots(root, python_roots)
    module_names = {path: _module_name(path, root=root, python_roots=roots) for path in python_files}
    unique_modules = _unique_module_paths(module_names)

    modules: list[ModuleRecord] = []
    symbols: list[SymbolRecord] = []
    imports_by_path: dict[Path, list[str]] = {}

    for path in python_files:
        rel = path.relative_to(root).as_posix()
        tree = _parse_python(path)
        exports = _module_exports(tree)
        imports = _module_imports(
            tree,
            module_names[path],
            is_package=path.name == "__init__.py",
        )
        imports_by_path[path] = imports
        modules.append(
            ModuleRecord(
                module=module_names[path],
                path=rel,
                imports=imports,
                exports=sorted(exports),
            )
        )
        symbols.extend(
            _symbols_for_module(
                tree,
                module=module_names[path],
                path=rel,
                exports=exports,
            )
        )

    edges: list[ImportEdge] = []
    for importer_path, imports in imports_by_path.items():
        for imported in imports:
            target = unique_modules.get(imported)
            if target is None:
                continue
            edges.append(
                ImportEdge(
                    importer=module_names[importer_path],
                    imported=imported,
                    importer_path=importer_path.relative_to(root).as_posix(),
                    imported_path=target.relative_to(root).as_posix(),
                )
            )

    return RepositoryIndex(
        root_name=root.name,
        source_state=_source_state(root, python_files),
        python_roots=["." if item == root else item.relative_to(root).as_posix() for item in roots],
        modules=sorted(modules, key=lambda item: (item.module, item.path)),
        symbols=sorted(
            symbols,
            key=lambda item: (item.qualified_name, item.path, item.line),
        ),
        import_edges=sorted(
            edges,
            key=lambda item: (
                item.importer_path,
                item.imported_path,
                item.imported,
            ),
        ),
        limitations=[
            "Import edges are static Python imports; dynamic imports and runtime call paths are not inferred.",
            "R and other languages remain visible as related files but do not receive semantic symbol edges in v1.",
            "Text references locate supporting context and do not prove behavioral dependence.",
        ],
    )


def load_repository_index(
    root: Path,
    path: Path,
    *,
    require_current: bool = True,
) -> RepositoryIndex:
    """Load an index and optionally reject source drift."""
    root = root.resolve()
    try:
        index = RepositoryIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Failed to read repository index: {path}") from error
    if require_current:
        current = build_repository_index(
            root,
            python_roots=[root if item == "." else root / item for item in index.python_roots],
        )
        if (
            current.source_state.commit != index.source_state.commit
            or current.source_state.python_digest != index.source_state.python_digest
            or current.source_state.python_file_count != index.source_state.python_file_count
        ):
            raise ValueError("Repository index is stale for the current Python source state; regenerate it")
    return index


def find_symbols(index: RepositoryIndex, query: str) -> list[SymbolRecord]:
    """Find definitions with exact matches ranked before partial matches."""
    query = query.strip()
    if not query:
        raise ValueError("Query must be non-empty")
    exact = [symbol for symbol in index.symbols if query in {symbol.name, symbol.qualified_name, symbol.path}]
    if exact:
        return exact
    lowered = query.casefold()
    return [
        symbol
        for symbol in index.symbols
        if lowered in symbol.name.casefold()
        or lowered in symbol.qualified_name.casefold()
        or lowered in symbol.path.casefold()
    ]


def build_impact_report(
    root: Path,
    index: RepositoryIndex,
    query: str,
    *,
    max_depth: int = 1,
    include_related: bool = True,
    max_related_per_role: int = 20,
    semantic_references: bool = False,
    pyright_langserver_cmd: list[str] | None = None,
) -> ImpactReport:
    """Build a role-labelled dependency and context impact report."""
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    root = root.resolve()
    focus, terms = _resolve_focus(root, index, query)
    nodes = _graph_nodes(index, focus, max_depth=max_depth)
    if semantic_references:
        _add_semantic_references(
            root,
            index,
            focus,
            nodes,
            langserver_cmd=pyright_langserver_cmd,
        )
    unresolved: list[str] = []
    related_omissions: dict[str, int] = {}
    if include_related:
        nodes, related_omissions = _add_related_files(
            root,
            nodes,
            terms,
            max_per_role=max_related_per_role,
        )
    if not focus:
        unresolved.append(f"No indexed Python file or symbol matched: {query}")

    return ImpactReport(
        root_name=index.root_name,
        source_state=index.source_state,
        query=query,
        focus=sorted(focus),
        nodes=_sorted_nodes(nodes.values()),
        related_omissions=related_omissions,
        unresolved=unresolved,
        limitations=index.limitations,
    )


def write_json(value: BaseModel | dict[str, object] | list[object], path: Path) -> None:
    """Write a Pydantic model or JSON-compatible value atomically."""
    payload: object
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_review_pack(
    root: Path,
    nodes: Iterable[ImpactNode],
    *,
    max_total_bytes: int = 1_000_000,
    max_file_bytes: int = 200_000,
) -> dict[str, object]:
    """Build a bounded, deterministic JSON review bundle from an impact surface."""
    if max_total_bytes < 0 or max_file_bytes < 0:
        raise ValueError("Review-pack byte limits must be non-negative")
    root = root.resolve()
    files: list[dict[str, object]] = []
    omitted: list[dict[str, str]] = []
    consumed = 0
    for node in _sorted_nodes(nodes):
        path = root / node.path
        if not path.is_file() or path.is_symlink():
            omitted.append({"path": node.path, "reason": "not present in working tree"})
            continue
        size = path.stat().st_size
        if size > max_file_bytes:
            omitted.append({"path": node.path, "reason": "file byte limit"})
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            omitted.append({"path": node.path, "reason": "not UTF-8 text"})
            continue
        encoded_size = len(content.encode("utf-8"))
        if consumed + encoded_size > max_total_bytes:
            omitted.append({"path": node.path, "reason": "total byte limit"})
            continue
        files.append(
            {
                "path": node.path,
                "relationships": [item.model_dump(mode="json") for item in _relationships(node)],
                "content": content,
            }
        )
        consumed += encoded_size
    return {
        "schema_version": "1.0.0",
        "root_name": root.name,
        "consumed_bytes": consumed,
        "max_total_bytes": max_total_bytes,
        "max_file_bytes": max_file_bytes,
        "files": files,
        "omitted": omitted,
        "limitations": [
            "The bundle contains selected source text, not trusted instructions or a quality verdict.",
            "Absent baseline files remain in the impact report but cannot contribute working-tree content.",
        ],
    }


def _repository_files(root: Path) -> list[Path]:
    try:
        output = git_text(
            root,
            ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        )
        relative = nul_paths(output)
        files = [root / item for item in relative]
    except ValueError:
        files = list(root.rglob("*"))
    return sorted(
        path
        for path in files
        if path.is_file() and not path.is_symlink() and repository_path_included(path.relative_to(root))
    )


def _resolve_python_roots(root: Path, requested: list[Path] | None) -> list[Path]:
    if requested:
        roots = [(root / item).resolve() if not item.is_absolute() else item.resolve() for item in requested]
    else:
        roots = [root / "src"] if (root / "src").is_dir() else []
        roots.append(root)
    for item in roots:
        if not item.exists() or not item.is_dir():
            raise ValueError(f"Python root must be an existing directory: {item}")
        try:
            item.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Python root must be inside repository root: {item}") from exc
    return sorted(set(roots), key=lambda item: (-len(item.parts), item.as_posix()))


def _module_name(path: Path, *, root: Path, python_roots: list[Path]) -> str:
    for python_root in python_roots:
        try:
            relative = path.relative_to(python_root)
        except ValueError:
            continue
        relative_name = relative.as_posix()
        name = relative_name[:-3].replace("/", ".") if path.suffix == ".py" else relative_name.replace("/", ".")
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        return name or path.stem
    relative_name = path.relative_to(root).as_posix()
    return relative_name[:-3].replace("/", ".") if path.suffix == ".py" else relative_name.replace("/", ".")


def _is_python_source(path: Path) -> bool:
    if path.suffix == ".py":
        return True
    if path.suffix:
        return False
    try:
        with path.open(encoding="utf-8") as handle:
            first_line = handle.readline(256)
    except (OSError, UnicodeError):
        return False
    return first_line.startswith("#!") and "python" in first_line.casefold()


def _unique_module_paths(module_names: dict[Path, str]) -> dict[str, Path]:
    grouped: dict[str, list[Path]] = {}
    for path, module in module_names.items():
        grouped.setdefault(module, []).append(path)
    return {module: paths[0] for module, paths in grouped.items() if len(paths) == 1}


def _parse_python(path: Path) -> ast.Module:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Failed to read Python source: {path}") from exc
    try:
        return ast.parse(source, filename=path.as_posix())
    except SyntaxError as exc:
        raise ValueError(f"Syntax error while indexing {path}: {exc.msg} at line {exc.lineno}") from exc


def _module_exports(tree: ast.Module) -> set[str]:
    exports: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)):
            exports.update(
                item.value for item in value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return exports


def _symbols_for_module(
    tree: ast.Module,
    *,
    module: str,
    path: str,
    exports: set[str],
) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            records.append(
                _symbol_record(
                    name=node.name,
                    qualified=f"{module}.{node.name}",
                    kind=SymbolKind.FUNCTION,
                    path=path,
                    node=node,
                    exports=exports,
                )
            )
        elif isinstance(node, ast.ClassDef):
            records.append(
                _symbol_record(
                    name=node.name,
                    qualified=f"{module}.{node.name}",
                    kind=SymbolKind.CLASS,
                    path=path,
                    node=node,
                    exports=exports,
                )
            )
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    records.append(
                        SymbolRecord(
                            name=method.name,
                            qualified_name=f"{module}.{node.name}.{method.name}",
                            kind=SymbolKind.METHOD,
                            path=path,
                            line=method.lineno,
                            end_line=method.end_lineno or method.lineno,
                            column=method.col_offset,
                            digest=_definition_digest(method),
                            public=not method.name.startswith("_"),
                        )
                    )
    return records


def _symbol_record(
    *,
    name: str,
    qualified: str,
    kind: SymbolKind,
    path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    exports: set[str],
) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        qualified_name=qualified,
        kind=kind,
        path=path,
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        column=node.col_offset,
        digest=_definition_digest(node),
        public=name in exports or not name.startswith("_"),
    )


def _definition_digest(node: ast.AST) -> str:
    normalized = copy.deepcopy(node)
    if isinstance(normalized, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        normalized.name = "_"
    return hashlib.sha256(ast.dump(normalized, include_attributes=False).encode("utf-8")).hexdigest()


def _module_imports(
    tree: ast.Module,
    module: str,
    *,
    is_package: bool,
) -> list[str]:
    imports: list[str] = []
    current_package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _relative_base(current_package, node.level)
            imported = _join_module(base, node.module or "")
            if imported:
                imports.append(imported)
                imports.extend(_join_module(imported, alias.name) for alias in node.names if alias.name != "*")
            if node.module is None:
                imports.extend(_join_module(base, alias.name) for alias in node.names if alias.name != "*")
    return list(dict.fromkeys(imports))


def _relative_base(package: str, level: int) -> str:
    if level <= 0:
        return ""
    parts = package.split(".") if package else []
    up = level - 1
    if up > len(parts):
        return ""
    return ".".join(parts[: len(parts) - up])


def _join_module(base: str, suffix: str) -> str:
    if not base:
        return suffix
    if not suffix:
        return base
    return f"{base}.{suffix}"


def _source_state(root: Path, python_files: list[Path]) -> SourceState:
    digest = hashlib.sha256()
    for path in python_files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    try:
        commit = git_text(root, ["rev-parse", "HEAD"]).strip() or None
        dirty = bool(git_text(root, ["status", "--porcelain=v1", "-z"]))
    except ValueError:
        commit = None
        dirty = False
    return SourceState(
        commit=commit,
        dirty=dirty,
        python_digest=digest.hexdigest(),
        python_file_count=len(python_files),
    )


def _resolve_focus(
    root: Path,
    index: RepositoryIndex,
    query: str,
) -> tuple[set[str], set[str]]:
    normalized = query.replace("\\", "/").lstrip("./")
    module_by_path = {module.path: module for module in index.modules}
    focus: set[str] = set()
    terms: set[str] = {Path(normalized).stem}
    candidate_path = root / normalized
    if candidate_path.is_file() and not candidate_path.is_symlink():
        focus.add(normalized)
    if normalized in module_by_path:
        focus.add(normalized)
        terms.add(module_by_path[normalized].module)
    else:
        matches = find_symbols(index, query)
        exact = [item for item in matches if query in {item.name, item.qualified_name}]
        chosen = exact or matches
        focus.update(item.path for item in chosen)
        terms.update(item.name for item in chosen)
        terms.update(item.qualified_name for item in chosen)
    return focus, {term for term in terms if len(term) > 2}


def _graph_nodes(
    index: RepositoryIndex,
    focus: set[str],
    *,
    max_depth: int,
) -> dict[str, ImpactNode]:
    nodes = {
        path: ImpactNode(
            path=path,
            role=ImpactRole.FOCUS,
            distance=0,
            reason="matched target file or symbol",
            relationships=[
                ImpactRelationship(
                    role=ImpactRole.FOCUS,
                    distance=0,
                    reason="matched target file or symbol",
                    source="target",
                )
            ],
        )
        for path in focus
    }
    forward: dict[str, set[str]] = {}
    reverse: dict[str, set[str]] = {}
    for edge in index.import_edges:
        forward.setdefault(edge.importer_path, set()).add(edge.imported_path)
        reverse.setdefault(edge.imported_path, set()).add(edge.importer_path)
    _walk_graph(nodes, focus, forward, role=ImpactRole.DEPENDENCY, max_depth=max_depth)
    _walk_graph(nodes, focus, reverse, role=ImpactRole.IMPORTER, max_depth=max_depth)
    return nodes


def _walk_graph(
    nodes: dict[str, ImpactNode],
    starts: set[str],
    graph: dict[str, set[str]],
    *,
    role: ImpactRole,
    max_depth: int,
) -> None:
    queue = deque((path, 0) for path in sorted(starts))
    visited = set(starts)
    while queue:
        current, distance = queue.popleft()
        if distance >= max_depth:
            continue
        for adjacent in sorted(graph.get(current, set())):
            next_distance = distance + 1
            candidate_role = ImpactRole.TEST if _is_test_path(adjacent) and role is ImpactRole.IMPORTER else role
            _merge_node(
                nodes,
                ImpactNode(
                    path=adjacent,
                    role=candidate_role,
                    distance=next_distance,
                    reason=(f"static import graph {role.value} at distance {next_distance}"),
                    relationships=[
                        ImpactRelationship(
                            role=candidate_role,
                            distance=next_distance,
                            reason=f"static import graph {role.value} at distance {next_distance}",
                            source="static_import",
                        )
                    ],
                ),
            )
            if adjacent not in visited:
                visited.add(adjacent)
                queue.append((adjacent, next_distance))


def _add_related_files(
    root: Path,
    nodes: dict[str, ImpactNode],
    terms: set[str],
    *,
    max_per_role: int,
) -> tuple[dict[str, ImpactNode], dict[str, int]]:
    if max_per_role < 0:
        raise ValueError("max_per_role must be non-negative")
    if not terms:
        return nodes, {}
    included: dict[ImpactRole, int] = {}
    omitted: dict[str, int] = {}
    for path in _repository_files(root):
        relative = path.relative_to(root).as_posix()
        if path.stat().st_size > 1_000_000:
            continue
        role = _related_role(relative, path)
        if role is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        matched = next((term for term in sorted(terms) if term in text), None)
        if matched is None:
            continue
        if included.get(role, 0) >= max_per_role:
            omitted[role.value] = omitted.get(role.value, 0) + 1
            continue
        _merge_node(
            nodes,
            ImpactNode(
                path=relative,
                role=role,
                distance=1,
                reason=f"text reference to {matched}",
                relationships=[
                    ImpactRelationship(
                        role=role,
                        distance=1,
                        reason=f"text reference to {matched}",
                        source="text_reference",
                    )
                ],
            ),
        )
        included[role] = included.get(role, 0) + 1
    return nodes, dict(sorted(omitted.items()))


def _related_role(relative: str, path: Path) -> ImpactRole | None:
    if _is_test_path(relative):
        return ImpactRole.TEST
    if path.suffix.lower() in _DOCUMENT_SUFFIXES:
        return ImpactRole.DOCUMENTATION
    if path.suffix.lower() in _CONFIG_SUFFIXES or path.name in _CONFIG_NAMES or path.name.startswith("."):
        return ImpactRole.CONFIGURATION
    return None


def _is_test_path(relative: str) -> bool:
    parts = Path(relative).parts
    return "tests" in parts or Path(relative).name.startswith("test_")


def _merge_node(nodes: dict[str, ImpactNode], candidate: ImpactNode) -> None:
    existing = nodes.get(candidate.path)
    if existing is None:
        nodes[candidate.path] = candidate
        return
    priority = {
        ImpactRole.FOCUS: 0,
        ImpactRole.TEST: 1,
        ImpactRole.DEPENDENCY: 2,
        ImpactRole.IMPORTER: 3,
        ImpactRole.REFERENCE: 4,
        ImpactRole.DOCUMENTATION: 5,
        ImpactRole.CONFIGURATION: 6,
    }
    primary = (
        candidate
        if (candidate.distance, priority[candidate.role])
        < (
            existing.distance,
            priority[existing.role],
        )
        else existing
    )
    relationships = {
        (item.role, item.distance, item.reason, item.source): item
        for item in (*_relationships(existing), *_relationships(candidate))
    }
    nodes[candidate.path] = ImpactNode(
        path=primary.path,
        role=primary.role,
        distance=primary.distance,
        reason=primary.reason,
        relationships=sorted(
            relationships.values(),
            key=lambda item: (item.distance, priority[item.role], item.source, item.reason),
        ),
    )


def _relationships(node: ImpactNode) -> list[ImpactRelationship]:
    if node.relationships:
        return node.relationships
    return [
        ImpactRelationship(
            role=node.role,
            distance=node.distance,
            reason=node.reason,
            source="primary",
        )
    ]


def _add_semantic_references(
    root: Path,
    index: RepositoryIndex,
    focus: set[str],
    nodes: dict[str, ImpactNode],
    *,
    langserver_cmd: list[str] | None,
) -> None:
    from anatomize.pack.pyright_lsp import pyright_referenced_files
    from anatomize.pack.uses import python_public_symbol_positions

    python_roots = [root if item == "." else root / item for item in index.python_roots]
    workspace_files = [root / module.path for module in index.modules]
    for relative in sorted(focus):
        target = root / relative
        if target.suffix != ".py" or not target.is_file():
            continue
        positions = python_public_symbol_positions(target, include_private=True)
        if not positions:
            continue
        references = pyright_referenced_files(
            root=root,
            target_file=target,
            positions=positions,
            langserver_cmd=langserver_cmd or ["pyright-langserver", "--stdio"],
            python_roots=python_roots,
            workspace_files=workspace_files,
        )
        for reference in references:
            relative_reference = reference.relative_to(root).as_posix()
            _merge_node(
                nodes,
                ImpactNode(
                    path=relative_reference,
                    role=ImpactRole.REFERENCE,
                    distance=1,
                    reason=f"Pyright semantic reference to {relative}",
                    relationships=[
                        ImpactRelationship(
                            role=ImpactRole.REFERENCE,
                            distance=1,
                            reason=f"Pyright semantic reference to {relative}",
                            source="pyright",
                        )
                    ],
                ),
            )


def _sorted_nodes(nodes: Iterable[ImpactNode]) -> list[ImpactNode]:
    values = list(nodes)
    role_order = {role: index for index, role in enumerate(ImpactRole)}
    return sorted(
        values,
        key=lambda item: (item.distance, role_order[item.role], item.path),
    )
