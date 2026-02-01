"""Deterministic, lightweight overview emitted at the start of pack artifacts.

This is intentionally minimal and token-efficient:
- no per-file symbol dumps
- no token counts

The structure tree is the primary “shape” representation; the overview is a
compact summary that helps agents orient themselves quickly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_pack_overview(
    *,
    root: Path,
    selected_rel_paths: list[str],
    size_by_rel: dict[str, int],
    is_binary_by_rel: dict[str, bool],
) -> dict[str, Any]:
    files = sorted(set(selected_rel_paths))
    python_files = [p for p in files if p.endswith(".py")]

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
        },
    }
