"""Dependency closure for `pack` (Python-only, static imports).

This is intentionally strict:
- Syntax errors are hard failures.
- Relative imports that escape the package root are hard failures.
- Imports that *appear* local and cannot be resolved are hard failures.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from anatomize.core.policy import SymlinkPolicy


@dataclass(frozen=True)
class PythonModule:
    module: str
    path: Path
    is_package: bool


class PythonModuleIndex:
    def __init__(self, python_roots: list[Path], *, symlinks: SymlinkPolicy = SymlinkPolicy.FORBID) -> None:
        self._python_roots = [p.resolve() for p in python_roots]
        self._by_module: dict[str, PythonModule] = {}
        for root in self._python_roots:
            if not root.exists() or not root.is_dir():
                raise ValueError(f"Python root must be an existing directory: {root}")
            for p in sorted(root.rglob("*.py")):
                if p.is_symlink():
                    if p.is_file() and symlinks not in (SymlinkPolicy.FILES, SymlinkPolicy.ALL):
                        continue
                    if p.is_dir() and symlinks not in (SymlinkPolicy.DIRS, SymlinkPolicy.ALL):
                        continue
                rel = p.relative_to(root).as_posix()
                mod = rel[:-3].replace("/", ".")
                is_pkg = mod.endswith(".__init__")
                if is_pkg:
                    mod = mod[: -len(".__init__")]
                self._by_module[mod] = PythonModule(module=mod, path=p.resolve(), is_package=is_pkg)

        self._local_toplevels = {m.split(".", 1)[0] for m in self._by_module.keys() if m}

    def module_for_path(self, path: Path) -> PythonModule:
        abs_path = path.resolve()
        for root in self._python_roots:
            try:
                rel = abs_path.relative_to(root)
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            if not rel_posix.endswith(".py"):
                break
            mod = rel_posix[:-3].replace("/", ".")
            is_pkg = mod.endswith(".__init__")
            if is_pkg:
                mod = mod[: -len(".__init__")]
            existing = self._by_module.get(mod)
            if existing is None:
                # It exists on disk (we're mapping from the path), so it must be indexable.
                return PythonModule(module=mod, path=abs_path, is_package=is_pkg)
            return existing
        raise ValueError(f"Path is not under any configured python root: {path}")

    def resolve_module(self, module: str) -> PythonModule | None:
        return self._by_module.get(module)

    def modules(self) -> list[PythonModule]:
        return [self._by_module[k] for k in sorted(self._by_module.keys())]

    def is_local_toplevel(self, name: str) -> bool:
        return name in self._local_toplevels

    def package_inits_for(self, module: str) -> list[PythonModule]:
        parts = module.split(".")
        inits: list[PythonModule] = []
        for i in range(1, len(parts)):
            pkg = ".".join(parts[:i])
            found = self._by_module.get(pkg)
            if found is not None and found.is_package:
                inits.append(found)
        return inits


def dependency_closure(entry_files: list[Path], *, index: PythonModuleIndex) -> list[Path]:
    if not entry_files:
        raise ValueError("At least one --entry is required for dependency closure")

    seen: set[Path] = set()
    queue: list[PythonModule] = []
    for f in entry_files:
        mod = index.module_for_path(f)
        queue.append(mod)

    while queue:
        current = queue.pop()
        if current.path in seen:
            continue
        seen.add(current.path)

        for init in index.package_inits_for(current.module):
            if init.path not in seen:
                queue.append(init)

        for imported in _extract_imported_modules(current, index=index):
            target = index.resolve_module(imported)
            if target is None:
                top = imported.split(".", 1)[0]
                if index.is_local_toplevel(top):
                    raise ValueError(f"Unresolved local import '{imported}' from {current.path}")
                continue
            if target.path not in seen:
                queue.append(target)

    return sorted(seen)


def reverse_dependency_closure(target_module: str, *, index: PythonModuleIndex) -> list[Path]:
    """Return the reverse import closure for a target module/package.

    This selects the target itself plus all local modules that import it,
    transitively. If the target is a package, its submodules are included as
    targets as well.
    """
    if not target_module:
        raise ValueError("Target module is required")

    targets: set[str] = set()
    for m in (mod.module for mod in index.modules()):
        if m == target_module or m.startswith(target_module + "."):
            targets.add(m)
    if not targets:
        raise ValueError(f"Unknown target module: {target_module}")

    imported_by = _build_reverse_import_index(index)

    selected: set[str] = set(targets)
    queue: list[str] = sorted(targets)
    while queue:
        mod = queue.pop()
        for importer in sorted(imported_by.get(mod, set())):
            if importer in selected:
                continue
            selected.add(importer)
            queue.append(importer)

    paths: list[Path] = []
    for m in sorted(selected):
        found = index.resolve_module(m)
        if found is not None:
            paths.append(found.path)
    return sorted(set(paths))


def _build_reverse_import_index(index: PythonModuleIndex) -> dict[str, set[str]]:
    imported_by: dict[str, set[str]] = {}
    for mod in index.modules():
        imports = _extract_imported_modules(mod, index=index)
        for imp in imports:
            if index.resolve_module(imp) is None:
                continue
            imported_by.setdefault(imp, set()).add(mod.module)
    return imported_by


def _extract_imported_modules(current: PythonModule, *, index: PythonModuleIndex) -> list[str]:
    try:
        source = current.path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read source file: {current.path}") from e
    try:
        tree = ast.parse(source, filename=current.path.as_posix())
    except SyntaxError as e:
        raise ValueError(f"Syntax error while parsing imports in {current.path}: {e.msg}") from e

    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from_base(current.module, current.is_package, node.level)
            if node.module is None:
                # from . import foo
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imports.append(_join_module(base, alias.name))
            else:
                mod = _join_module(base, node.module)
                imports.append(mod)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    # Only include a submodule if it actually exists locally; otherwise it's
                    # most likely a re-exported symbol, not a module.
                    candidate = _join_module(mod, alias.name)
                    if index.resolve_module(candidate) is not None:
                        imports.append(candidate)

    # De-dup while preserving deterministic ordering.
    seen: set[str] = set()
    out: list[str] = []
    for m in imports:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _resolve_from_base(current_module: str, current_is_package: bool, level: int) -> str:
    if level <= 0:
        return ""
    current_pkg = current_module if current_is_package else current_module.rpartition(".")[0]
    if not current_pkg and level > 1:
        raise ValueError("Relative import goes beyond top-level package")
    parts = current_pkg.split(".") if current_pkg else []
    up = level - 1
    if up > len(parts):
        raise ValueError("Relative import goes beyond top-level package")
    return ".".join(parts[: len(parts) - up])


def _join_module(base: str, suffix: str) -> str:
    if not base:
        return suffix
    if not suffix:
        return base
    return f"{base}.{suffix}"
