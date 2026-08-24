"""Filesystem containment at repository-controlled configuration boundaries."""

from __future__ import annotations

from pathlib import Path


def resolve_inside(root: Path, value: str | Path, *, purpose: str) -> Path:
    """Resolve a repository-controlled path and reject boundary escape."""
    resolved_root = root.resolve()
    path = Path(value)
    candidate = path.resolve() if path.is_absolute() else (resolved_root / path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{purpose} must resolve inside repository root: {value}") from error
    return candidate
