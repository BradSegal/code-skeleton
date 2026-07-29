"""Glob-style matching shared by `pack` features."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from anatomize.core.exclude import parse_ignore_line, path_matches_rule


@dataclass(frozen=True)
class GlobRule:
    """Parsed glob pattern rule.

    Attributes
    ----------
    pattern
        The glob pattern without leading/trailing slashes.
    anchored
        True if pattern was prefixed with '/' (match from root).
    directory_only
        True if pattern was suffixed with '/' (match directories only).
    has_slash
        True if pattern contains a path separator.
    """

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
        """Initialize with a list of glob patterns.

        Parameters
        ----------
        patterns
            List of gitignore-style glob patterns.
        """
        self._rules = compile_glob_rules(patterns)

    @classmethod
    def from_rules(cls, rules: list[GlobRule]) -> GlobMatcher:
        """Construct a matcher from already parsed rules."""
        matcher = cls([])
        matcher._rules = list(rules)
        return matcher

    def matches_rule(self, rel_posix: str, rule: GlobRule, *, is_dir: bool) -> bool:
        """Check one parsed rule against a path."""
        rel_posix = rel_posix.strip("/")
        path = PurePosixPath(rel_posix) if rel_posix else PurePosixPath(".")
        return path_matches_rule(path, rule, is_dir=is_dir)

    def matches_any(self, rel_posix: str, *, is_dir: bool) -> bool:
        """Check if a path matches any pattern.

        Parameters
        ----------
        rel_posix
            Relative path in POSIX format.
        is_dir
            True if the path is a directory.

        Returns
        -------
        bool
            True if any pattern matches.
        """
        rel_posix = rel_posix.strip("/")
        path = PurePosixPath(rel_posix) if rel_posix else PurePosixPath(".")
        for rule in self._rules:
            if path_matches_rule(path, rule, is_dir=is_dir):
                return True
        return False


def compile_glob_rules(patterns: list[str]) -> list[GlobRule]:
    """Parse reusable gitignore-style glob rules."""
    rules: list[GlobRule] = []
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
        rules.append(
            GlobRule(
                pattern=raw,
                anchored=anchored,
                directory_only=directory_only,
                has_slash=has_slash,
            )
        )
    return rules
