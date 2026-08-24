"""Shared, policy-free Python import-name normalization."""

from __future__ import annotations


def join_module(base: str, suffix: str) -> str:
    """Join two possibly empty dotted-module components."""
    if not base:
        return suffix
    if not suffix:
        return base
    return f"{base}.{suffix}"


def relative_import_base(module: str, *, is_package: bool, level: int) -> str | None:
    """Resolve a relative-import base, returning None when it escapes the package."""
    if level <= 0:
        return ""
    package = module if is_package else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    up = level - 1
    if up > len(parts):
        return None
    return ".".join(parts[: len(parts) - up])


def imported_module(
    module: str,
    *,
    is_package: bool,
    imported: str | None,
    level: int,
) -> str | None:
    """Resolve the module portion of one ``from`` import without policy decisions."""
    base = relative_import_base(module, is_package=is_package, level=level)
    if base is None:
        return None
    return join_module(base, imported or "")
