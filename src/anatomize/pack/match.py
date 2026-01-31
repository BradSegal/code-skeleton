"""Glob-style matching shared by `pack` features."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cache
from pathlib import PurePosixPath

from anatomize.core.exclude import parse_ignore_line


@dataclass(frozen=True)
class GlobRule:
    pattern: str
    anchored: bool
    directory_only: bool
    has_slash: bool


class GlobMatcher:
    """Match a path against a list of patterns.

    Semantics are intentionally aligned with `anatomize.core.exclude.Excluder`
    (gitignore-like, with `**` support), but this matcher returns True when any
    rule matches.
    """

    def __init__(self, patterns: list[str]) -> None:
        self._rules: list[GlobRule] = []
        for raw in patterns:
            parsed = parse_ignore_line(raw, allow_negation=False)
            if parsed is None:
                continue
            raw = parsed.pattern

            directory_only = raw.endswith("/")
            if directory_only:
                raw = raw.rstrip("/")
                if not raw:
                    continue

            anchored = raw.startswith("/")
            if anchored:
                raw = raw.lstrip("/")
                if not raw:
                    continue

            has_slash = "/" in raw
            self._rules.append(
                GlobRule(pattern=raw, anchored=anchored, directory_only=directory_only, has_slash=has_slash)
            )

    def matches_any(self, rel_posix: str, *, is_dir: bool) -> bool:
        rel_posix = rel_posix.strip("/")
        path = PurePosixPath(rel_posix) if rel_posix else PurePosixPath(".")
        for rule in self._rules:
            if self._matches(path, rule, is_dir=is_dir):
                return True
        return False

    def _matches(self, path: PurePosixPath, rule: GlobRule, *, is_dir: bool) -> bool:
        if rule.directory_only:
            if is_dir and self._match_single(path, rule):
                return True
            for parent in path.parents:
                if self._match_single(parent, rule):
                    return True
            return False
        return self._match_single(path, rule)

    def _match_single(self, path: PurePosixPath, rule: GlobRule) -> bool:
        pattern = rule.pattern

        path_parts = tuple(p for p in path.parts if p != ".")
        pat_parts = tuple(part for part in pattern.split("/") if part)

        if not rule.has_slash and not rule.anchored:
            if not path_parts:
                return False
            return fnmatchcase(path_parts[-1], pattern)

        if rule.anchored:
            return _match_parts(pat_parts, path_parts)

        for start in range(len(path_parts) + 1):
            if _match_parts(pat_parts, path_parts[start:]):
                return True
        return False


@cache
def _match_parts(pat_parts: tuple[str, ...], path_parts: tuple[str, ...]) -> bool:
    return _match_parts_at(pat_parts, 0, path_parts, 0)


@cache
def _match_parts_at(pat_parts: tuple[str, ...], pi: int, path_parts: tuple[str, ...], si: int) -> bool:
    if pi == len(pat_parts):
        return si == len(path_parts)

    pat = pat_parts[pi]
    if pat == "**":
        for k in range(si, len(path_parts) + 1):
            if _match_parts_at(pat_parts, pi + 1, path_parts, k):
                return True
        return False

    if si == len(path_parts):
        return False

    if not fnmatchcase(path_parts[si], pat):
        return False

    return _match_parts_at(pat_parts, pi + 1, path_parts, si + 1)
