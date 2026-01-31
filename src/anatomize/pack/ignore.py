"""Ignore pattern loading for `pack`.

This intentionally uses the same gitignore-like matcher as the generator
(`anatomize.core.exclude.Excluder`) to keep behavior consistent and
predictable.
"""

from __future__ import annotations

from pathlib import Path

from anatomize.core.exclude import Excluder

DEFAULT_IGNORE_PATTERNS: list[str] = [
    "__pycache__/",
    "*.pyc",
    ".git/",
    ".venv/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".skeleton/",
    ".tox/",
    "dist/",
    "build/",
    "*.egg-info/",
]


STANDARD_IGNORE_FILES: list[str] = [
    ".repomixignore",
    ".ignore",
    ".gitignore",
    ".git/info/exclude",
]

def build_excluder(
    root: Path, *, ignore: list[str], ignore_files: list[Path], respect_standard_ignores: bool
) -> Excluder:
    patterns: list[str] = []
    patterns.extend(DEFAULT_IGNORE_PATTERNS)

    if respect_standard_ignores:
        for rel in STANDARD_IGNORE_FILES:
            p = root / rel
            if p.exists() and p.is_file():
                patterns.extend(_read_patterns_file(p))

    for p in ignore_files:
        patterns.extend(_read_patterns_file(p))

    patterns.extend(ignore)
    return Excluder(patterns)


def _read_patterns_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read ignore file: {path}") from e
    return [line.rstrip("\n") for line in text.splitlines()]
