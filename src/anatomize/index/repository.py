"""Build canonical baseline evidence inputs from a repository."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tokenize
from pathlib import Path

from anatomize._python_imports import imported_module, join_module
from anatomize.index.git import git_text, nul_paths
from anatomize.index.intelligence import (
    _codepoint_column,
    build_duplicate_groups,
    definition_fingerprints,
    documentation_reference_facts,
    markdown_sections,
    python_reference_candidates,
    resolve_symbol_facts,
)
from anatomize.index.models import (
    DOCUMENTATION_TEXT_PROVIDER,
    PYTHON_AST_PROVIDER,
    REPOSITORY_INVENTORY_PROVIDER,
    ArtifactProducer,
    DocumentationSection,
    FactProvider,
    FileRecord,
    FileRole,
    ImportEdge,
    ModuleRecord,
    ProviderCompleteness,
    ReferenceCandidate,
    RepositoryIndex,
    SourceState,
    SymbolKind,
    SymbolRecord,
)
from anatomize.version import __version__

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
_INDEXED_DOCUMENT_SUFFIXES = {".md"}
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

    roots = _resolve_python_roots(root, python_roots)
    return _assemble_repository_index(root, roots=roots)


def capture_repository_source_state(root: Path) -> SourceState:
    """Fingerprint the exact review source without building semantic facts."""
    resolved = root.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Repository root must be an existing directory: {resolved}")
    files = _repository_files(resolved)
    python_files = [path for path in files if _is_python_source(path)]
    file_records = _fact_file_records(resolved, files)
    return _source_state(resolved, python_files, file_records, _fact_providers())


def _assemble_repository_index(
    root: Path,
    *,
    roots: list[Path],
) -> RepositoryIndex:
    files = _repository_files(root)
    python_files = [path for path in files if _is_python_source(path)]
    document_files = [path for path in files if path.suffix.lower() in _INDEXED_DOCUMENT_SUFFIXES]
    file_records = _fact_file_records(root, files)
    providers = _fact_providers()
    module_names = {path: _module_name(path, root=root, python_roots=roots) for path in python_files}
    modules: list[ModuleRecord] = []
    symbols: list[SymbolRecord] = []
    candidates: list[ReferenceCandidate] = []
    parse_failures: list[str] = []
    for path in python_files:
        rel = path.relative_to(root).as_posix()
        try:
            module, module_symbols, module_candidates = _python_module_facts(
                path,
                module=module_names[path],
                path=rel,
            )
        except ValueError as error:
            parse_failures.append(f"{rel}: {error}")
            modules.append(ModuleRecord(module=module_names[path], path=rel))
            continue
        modules.append(module)
        symbols.extend(module_symbols)
        candidates.extend(module_candidates)

    edges = _resolved_import_edges(modules)
    sections: list[DocumentationSection] = []
    for path in document_files:
        relative = path.relative_to(root).as_posix()
        sections.extend(markdown_sections(path, path=relative))
    occurrences, unresolved_candidates = resolve_symbol_facts(symbols, candidates)
    documentation_occurrences = documentation_reference_facts(
        root,
        sections,
        symbols,
    )
    occurrences.extend(documentation_occurrences)
    duplicate_groups = build_duplicate_groups(symbols, sections)

    return RepositoryIndex(
        producer=ArtifactProducer(version=__version__),
        root_name=root.name,
        source_state=_source_state(root, python_files, file_records, providers),
        providers=providers,
        files=file_records,
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
        occurrences=sorted(
            occurrences,
            key=lambda item: (item.path, item.line, item.column, item.kind.value, item.symbol_id),
        ),
        documentation_sections=sorted(sections, key=lambda item: (item.path, item.line, item.heading)),
        duplicate_groups=duplicate_groups,
        limitations=[
            "Import edges are static Python imports; dynamic imports and runtime call paths are not inferred.",
            "R and other languages remain visible as related files, but the built-in repository map "
            "does not infer their symbol relationships.",
            "Text references locate supporting context and do not prove behavioral dependence.",
            f"The built-in repository map omitted {unresolved_candidates} unresolved or ambiguous references.",
            *(
                [
                    f"The built-in Python reader could not parse {len(parse_failures)} file(s): "
                    + "; ".join(parse_failures[:8])
                ]
                if parse_failures
                else []
            ),
            "Attribute dispatch, local type inference, dynamic imports, and runtime call paths are omitted.",
        ],
    )


def _fact_providers() -> list[FactProvider]:
    return [
        FactProvider(
            provider_id=PYTHON_AST_PROVIDER,
            version=__version__,
            capabilities=[
                "python_symbols",
                "static_python_imports",
                "lexical_symbol_occurrences",
                "structural_duplicate_candidates",
            ],
            completeness=ProviderCompleteness.COMPLETE,
            limitations=[
                "Dynamic imports, runtime call paths, external-package definitions, and inferred "
                "attribute dispatch are not resolved."
            ],
        ),
        FactProvider(
            provider_id=DOCUMENTATION_TEXT_PROVIDER,
            version=__version__,
            capabilities=["markdown_sections", "documentation_duplicate_candidates"],
            completeness=ProviderCompleteness.COMPLETE,
            limitations=[
                "Only ATX-heading-bounded Markdown sections are indexed; semantic paraphrases are not compared."
            ],
        ),
        FactProvider(
            provider_id=REPOSITORY_INVENTORY_PROVIDER,
            version=__version__,
            capabilities=["repository_file_inventory", "configuration_files"],
            completeness=ProviderCompleteness.COMPLETE,
            limitations=[
                "The baseline inventory records metadata, not configuration semantics or execution behavior."
            ],
        ),
    ]


def _provider_digest(providers: list[FactProvider]) -> str:
    payload = {
        "python": list(sys.version_info[:2]),
        "providers": [item.model_dump(mode="json") for item in providers],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fact_file_records(root: Path, fact_files: list[Path]) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in fact_files:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        is_python = _is_python_source(path)
        suffix = path.suffix.casefold()
        is_r = suffix == ".r"
        is_documentation = suffix in _DOCUMENT_SUFFIXES or suffix == ".rmd"
        is_configuration = _is_indexed_configuration(path)
        parts = {part.casefold() for part in path.relative_to(root).parts[:-1]}
        roles: set[FileRole] = set()
        if is_python:
            roles.add(FileRole.TEST if _is_test_path(relative) else FileRole.SOURCE)
            language = "python"
            provider_id = PYTHON_AST_PROVIDER
        elif is_r:
            roles.add(FileRole.TEST if _is_test_path(relative) else FileRole.SOURCE)
            language = "r"
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        elif suffix == ".ipynb":
            roles.update({FileRole.SOURCE, FileRole.DOCUMENTATION})
            language = "jupyter"
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        elif is_documentation:
            roles.add(FileRole.DOCUMENTATION)
            if suffix in {".qmd", ".rmd"}:
                roles.add(FileRole.SOURCE)
            language = "markdown" if suffix in {".md", ".qmd"} else suffix.removeprefix(".")
            provider_id = DOCUMENTATION_TEXT_PROVIDER if suffix == ".md" else REPOSITORY_INVENTORY_PROVIDER
        elif parts.intersection({"data", "datasets"}):
            roles.add(FileRole.DATA)
            language = suffix.removeprefix(".") or path.name.casefold()
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        elif parts.intersection({"workflow", "workflows", "pipelines"}) or path.name == "Snakefile":
            roles.add(FileRole.WORKFLOW)
            language = suffix.removeprefix(".") or path.name.casefold()
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        elif parts.intersection({"artifacts", "derived", "outputs", "reports", "results"}):
            roles.add(FileRole.ARTIFACT)
            language = suffix.removeprefix(".") or path.name.casefold()
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        elif is_configuration:
            roles.add(FileRole.CONFIGURATION)
            language = suffix.removeprefix(".") or path.name.casefold()
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        else:
            roles.add(FileRole.OTHER)
            language = suffix.removeprefix(".") or path.name.casefold()
            provider_id = REPOSITORY_INVENTORY_PROVIDER
        records.append(
            FileRecord(
                file_id=_file_id(relative),
                path=relative,
                language=language,
                digest=hashlib.sha256(content).hexdigest(),
                size=len(content),
                roles=sorted(roles, key=lambda item: item.value),
                provider_ids=[provider_id],
            )
        )
    return sorted(records, key=lambda item: item.path)


def _file_id(path: str) -> str:
    return f"file:{path}"


def _symbol_id(qualified_name: str, path: str, *, duplicate_ordinal: int) -> str:
    suffix = "" if duplicate_ordinal == 1 else f"#{duplicate_ordinal}"
    return f"python:{qualified_name}@{path}{suffix}"


def _python_module_facts(
    source: Path,
    *,
    module: str,
    path: str,
) -> tuple[ModuleRecord, list[SymbolRecord], list[ReferenceCandidate]]:
    tree, source_text = _parse_python(source)
    source_lines = source_text.splitlines()
    exports = _module_exports(tree)
    imports = _module_imports(tree, module, is_package=source.name == "__init__.py")
    symbols = _symbols_for_module(
        tree,
        module=module,
        path=path,
        exports=exports,
        source_lines=source_lines,
    )
    return (
        ModuleRecord(module=module, path=path, imports=imports, exports=sorted(exports)),
        symbols,
        python_reference_candidates(
            tree,
            module=module,
            path=path,
            symbols=symbols,
            is_package=source.name == "__init__.py",
            source_lines=source_lines,
        ),
    )


def _resolved_import_edges(modules: list[ModuleRecord]) -> list[ImportEdge]:
    grouped: dict[str, list[str]] = {}
    for module in modules:
        grouped.setdefault(module.module, []).append(module.path)
    unique_modules = {module: paths[0] for module, paths in grouped.items() if len(paths) == 1}
    edges = [
        ImportEdge(
            importer=module.module,
            imported=imported,
            importer_path=module.path,
            imported_path=unique_modules[imported],
        )
        for module in modules
        for imported in module.imports
        if imported in unique_modules
    ]
    return sorted(edges, key=lambda item: (item.importer_path, item.imported_path, item.imported))


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


def _is_indexed_configuration(path: Path) -> bool:
    name = path.name.casefold()
    return (
        path.suffix.lower() in {".toml", ".yaml", ".yml"}
        or path.name in _CONFIG_NAMES
        or path.name.startswith(".")
        or name in {"package.json", "tsconfig.json", "composer.json"}
        or name.endswith("config.json")
    )


def _fact_provider_id(path: Path) -> str | None:
    if _is_python_source(path):
        return PYTHON_AST_PROVIDER
    if path.suffix.lower() in _INDEXED_DOCUMENT_SUFFIXES:
        return DOCUMENTATION_TEXT_PROVIDER
    if _is_indexed_configuration(path):
        return REPOSITORY_INVENTORY_PROVIDER
    return None


def _parse_python(path: Path) -> tuple[ast.Module, str]:
    try:
        with tokenize.open(path) as handle:
            source = handle.read()
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError(f"Failed to read Python source: {path}") from exc
    try:
        return ast.parse(source, filename=path.as_posix()), source
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
    source_lines: list[str],
) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    ordinals: dict[str, int] = {}

    def append_record(
        *,
        name: str,
        qualified: str,
        kind: SymbolKind,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        declared_exports: set[str],
    ) -> None:
        ordinal = ordinals.get(qualified, 0) + 1
        ordinals[qualified] = ordinal
        records.append(
            _symbol_record(
                name=name,
                qualified=qualified,
                kind=kind,
                path=path,
                node=node,
                exports=declared_exports,
                duplicate_ordinal=ordinal,
                source_lines=source_lines,
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            append_record(
                name=node.name,
                qualified=f"{module}.{node.name}",
                kind=SymbolKind.FUNCTION,
                node=node,
                declared_exports=exports,
            )
        elif isinstance(node, ast.ClassDef):
            append_record(
                name=node.name,
                qualified=f"{module}.{node.name}",
                kind=SymbolKind.CLASS,
                node=node,
                declared_exports=exports,
            )
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    append_record(
                        name=method.name,
                        qualified=f"{module}.{node.name}.{method.name}",
                        kind=SymbolKind.METHOD,
                        node=method,
                        declared_exports=set(),
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
    duplicate_ordinal: int,
    source_lines: list[str],
) -> SymbolRecord:
    fingerprints = definition_fingerprints(node)
    return SymbolRecord(
        symbol_id=_symbol_id(qualified, path, duplicate_ordinal=duplicate_ordinal),
        name=name,
        qualified_name=qualified,
        kind=kind,
        path=path,
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        column=_codepoint_column(source_lines, node.lineno, node.col_offset),
        end_column=_codepoint_column(
            source_lines,
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        ),
        digest=fingerprints.structural_digest,
        exact_digest=fingerprints.exact_digest,
        node_count=fingerprints.node_count,
        body_line_count=fingerprints.body_line_count,
        public=name in exports or not name.startswith("_"),
    )


def _module_imports(
    tree: ast.Module,
    module: str,
    *,
    is_package: bool,
) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = imported_module(
                module,
                is_package=is_package,
                imported=node.module,
                level=node.level,
            )
            if resolved:
                imports.append(resolved)
                imports.extend(join_module(resolved, alias.name) for alias in node.names if alias.name != "*")
            if node.module is None and resolved is not None:
                imports.extend(join_module(resolved, alias.name) for alias in node.names if alias.name != "*")
    return list(dict.fromkeys(imports))


def _source_state(
    root: Path,
    python_files: list[Path],
    file_records: list[FileRecord],
    providers: list[FactProvider],
) -> SourceState:
    python_digest = hashlib.sha256()
    fact_digest = hashlib.sha256()
    records_by_path = {item.path: item for item in file_records}
    for path in python_files:
        relative = path.relative_to(root).as_posix()
        record = records_by_path[relative]
        python_digest.update(relative.encode("utf-8"))
        python_digest.update(b"\0")
        python_digest.update(bytes.fromhex(record.digest))
    for record in file_records:
        fact_digest.update(record.path.encode("utf-8"))
        fact_digest.update(b"\0")
        fact_digest.update(bytes.fromhex(record.digest))
        fact_digest.update(b"\0")
        fact_digest.update("\0".join(record.provider_ids).encode("utf-8"))
    try:
        commit = git_text(root, ["rev-parse", "HEAD"]).strip() or None
        dirty = bool(git_text(root, ["status", "--porcelain=v1", "-z"]))
    except ValueError:
        commit = None
        dirty = False
    return SourceState(
        commit=commit,
        dirty=dirty,
        python_digest=python_digest.hexdigest(),
        python_file_count=len(python_files),
        fact_digest=fact_digest.hexdigest(),
        fact_file_count=len(file_records),
        provider_digest=_provider_digest(providers),
    )


def _is_test_path(relative: str) -> bool:
    parts = Path(relative).parts
    return "tests" in parts or Path(relative).name.startswith("test_")
