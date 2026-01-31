"""Deterministic, lightweight overview emitted at the start of pack artifacts.

The goal is to preserve the codebase "shape" even when consumers only skim the
first part of a packed artifact.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OverviewLimits:
    max_imports: int = 50
    max_symbols: int = 200


def build_pack_overview(
    *,
    root: Path,
    selected_rel_paths: list[str],
    size_by_rel: dict[str, int],
    is_binary_by_rel: dict[str, bool],
    content_total_tokens: int,
    limits: OverviewLimits | None = None,
) -> dict[str, Any]:
    lim = limits or OverviewLimits()

    files = sorted(set(selected_rel_paths))
    python_files = [p for p in files if p.endswith(".py")]

    python_index: list[dict[str, Any]] = []
    python_parse_errors = 0
    for rel in python_files:
        abs_path = (root / rel).resolve()
        entry = _python_top_level_index(abs_path, rel_posix=rel, limits=lim)
        if entry.get("error") is not None:
            python_parse_errors += 1
        python_index.append(entry)

    binary_files = 0
    total_bytes = 0
    for rel in files:
        total_bytes += int(size_by_rel.get(rel, 0))
        if is_binary_by_rel.get(rel, False):
            binary_files += 1

    return {
        "selected": {
            "files": len(files),
            "python_files": len(python_files),
            "binary_files": binary_files,
            "total_bytes": total_bytes,
            "content_total_tokens": content_total_tokens,
        },
        "python_top_level": {
            "parse_errors": python_parse_errors,
            "limits": {"max_imports": lim.max_imports, "max_symbols": lim.max_symbols},
            "modules": python_index,
        }
        if python_index
        else None,
    }


def _python_top_level_index(path: Path, *, rel_posix: str, limits: OverviewLimits) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"path": rel_posix, "error": {"type": "read_error", "message": str(e)}}

    try:
        tree = ast.parse(text, filename=rel_posix)
    except SyntaxError as e:
        return {
            "path": rel_posix,
            "error": {"type": "syntax_error", "message": e.msg, "line": e.lineno, "offset": e.offset},
        }

    imports: list[str] = []
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if len(imports) >= limits.max_imports:
                    break
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if len(imports) >= limits.max_imports:
                continue
            mod = node.module or ""
            if node.level and node.level > 0:
                mod = "." * node.level + mod
            imports.append(mod or ".")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if len(functions) < limits.max_symbols:
                functions.append(
                    {"name": node.name, "line": node.lineno, "async": isinstance(node, ast.AsyncFunctionDef)}
                )
        elif isinstance(node, ast.ClassDef):
            if len(classes) < limits.max_symbols:
                classes.append({"name": node.name, "line": node.lineno})
        elif isinstance(node, ast.Assign):
            if len(assignments) >= limits.max_symbols:
                continue
            if node.targets:
                t = node.targets[0]
                if isinstance(t, ast.Name):
                    assignments.append({"name": t.id, "line": node.lineno})
        elif isinstance(node, ast.AnnAssign):
            if len(assignments) >= limits.max_symbols:
                continue
            if isinstance(node.target, ast.Name):
                assignments.append({"name": node.target.id, "line": node.lineno})

    return {
        "path": rel_posix,
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "assignments": assignments,
        "truncated": {
            "imports": len(imports) >= limits.max_imports,
            "symbols": (len(functions) >= limits.max_symbols)
            or (len(classes) >= limits.max_symbols)
            or (len(assignments) >= limits.max_symbols),
        },
    }
