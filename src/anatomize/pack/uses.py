"""Reference-based usage slicing helpers."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from anatomize.pack.pyright_lsp import LspPosition


@dataclass(frozen=True)
class UsageSlice:
    target_file: Path
    symbol_positions: list[LspPosition]


def python_public_symbol_positions(path: Path, *, include_private: bool) -> list[LspPosition]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read Python file: {path}") from e
    try:
        tree = ast.parse(source, filename=path.as_posix())
    except SyntaxError as e:
        raise ValueError(f"Syntax error while parsing {path}: {e.msg}") from e

    lines = source.splitlines()
    positions: list[LspPosition] = []
    for node in tree.body:
        name, pos = _position_for_node(node, lines=lines)
        if pos is None:
            continue
        if name is None:
            positions.append(pos)
            continue
        if include_private or not name.startswith("_"):
            positions.append(pos)

    # Deterministic de-dup.
    seen: set[tuple[int, int]] = set()
    out: list[LspPosition] = []
    for p in positions:
        key = (p.line, p.character)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


_DEF_RE = re.compile(r"^(?:async\s+def|def)\s+(?P<name>[A-Za-z_][A-Za-z_0-9]*)\b")
_CLASS_RE = re.compile(r"^class\s+(?P<name>[A-Za-z_][A-Za-z_0-9]*)\b")


def _position_for_node(node: ast.stmt, *, lines: list[str]) -> tuple[str | None, LspPosition | None]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        line_idx = node.lineno - 1
        if 0 <= line_idx < len(lines):
            line = lines[line_idx]
            start = max(0, min(len(line), node.col_offset))
            segment = line[start:].lstrip()
            seg_off = start + (len(line[start:]) - len(line[start:].lstrip()))
            m = (_CLASS_RE if isinstance(node, ast.ClassDef) else _DEF_RE).match(segment)
            if m is not None:
                name_offset = m.start("name")
                return node.name, LspPosition(line=line_idx, character=seg_off + name_offset)
        # Fallback: statement start.
        return node.name, LspPosition(line=node.lineno - 1, character=node.col_offset)
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        # Best-effort: treat assignment as “symbol” at the statement location.
        # For `Assign`, attempt to get the first target name.
        name: str | None = None
        if isinstance(node, ast.Assign) and node.targets:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                name = t.id
                return name, LspPosition(line=t.lineno - 1, character=t.col_offset)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            return name, LspPosition(line=node.target.lineno - 1, character=node.target.col_offset)
        return name, LspPosition(line=node.lineno - 1, character=node.col_offset)
    return None, None
