"""Exclude matching with gitignore-like semantics.

Patterns are applied in order; the last matching rule wins.

Supported:
- `#` comments and blank lines
- `!` negation rules
- `\\#` and `\\!` as literal `#` / `!` at line start
- trailing spaces ignored unless escaped (gitignore-like)
- `/`-anchored rules
- rules with `/` match against paths
- rules without `/` match against basenames (gitignore-like)
- glob wildcards including `**`
- trailing `/` marks directory-only rule
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cache
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ParsedIgnoreLine:
    pattern: str
    negated: bool


@dataclass(frozen=True)
class ExcludeRule:
    pattern: str
    negated: bool
    anchored: bool
    directory_only: bool
    has_slash: bool


class Excluder:
    def __init__(self, patterns: list[str]) -> None:
        self._rules: list[ExcludeRule] = []
        for raw in patterns:
            parsed = parse_ignore_line(raw, allow_negation=True)
            if parsed is None:
                continue
            raw = parsed.pattern
            negated = parsed.negated

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
                ExcludeRule(
                    pattern=raw,
                    negated=negated,
                    anchored=anchored,
                    directory_only=directory_only,
                    has_slash=has_slash,
                )
            )

    def is_excluded(self, rel_posix: str, *, is_dir: bool) -> bool:
        """Return True if the given relative path should be excluded."""
        rel_posix = rel_posix.strip("/")
        path = PurePosixPath(rel_posix) if rel_posix else PurePosixPath(".")

        excluded = False
        for rule in self._rules:
            if not self._matches(path, rule, is_dir=is_dir):
                continue
            excluded = not rule.negated
        return excluded

    def filter_dirnames(self, parent_rel_posix: str, dirnames: list[str]) -> None:
        """Prune excluded directories in-place for os.walk dirnames."""
        parent_rel_posix = parent_rel_posix.strip("/")
        keep: list[str] = []
        for d in dirnames:
            rel = f"{parent_rel_posix}/{d}" if parent_rel_posix else d
            if self.is_excluded(rel, is_dir=True):
                continue
            keep.append(d)
        dirnames[:] = keep

    def _matches(self, path: PurePosixPath, rule: ExcludeRule, *, is_dir: bool) -> bool:
        # Directory-only rules match the directory and anything under it.
        if rule.directory_only:
            if is_dir and self._match_single(path, rule):
                return True
            for parent in path.parents:
                if self._match_single(parent, rule):
                    return True
            return False

        return self._match_single(path, rule)

    def _match_single(self, path: PurePosixPath, rule: ExcludeRule) -> bool:
        pattern = rule.pattern

        path_parts = tuple(p for p in path.parts if p != ".")
        pat_parts = tuple(part for part in pattern.split("/") if part)

        if not rule.has_slash and not rule.anchored:
            # Basename-style matching (gitignore-like): matches the last segment.
            if not path_parts:
                return False
            return fnmatchcase(path_parts[-1], pattern)

        if rule.anchored:
            return _match_parts(pat_parts, path_parts)

        # Pattern with slash and not anchored: match at any segment boundary.
        for start in range(len(path_parts) + 1):
            if _match_parts(pat_parts, path_parts[start:]):
                return True
        return False


@cache
def _match_parts(pat_parts: tuple[str, ...], path_parts: tuple[str, ...]) -> bool:
    return _match_parts_at(pat_parts, 0, path_parts, 0)


@cache
def _match_parts_at(
    pat_parts: tuple[str, ...], pi: int, path_parts: tuple[str, ...], si: int
) -> bool:
    if pi == len(pat_parts):
        return si == len(path_parts)

    pat = pat_parts[pi]
    if pat == "**":
        # Match zero or more segments.
        for k in range(si, len(path_parts) + 1):
            if _match_parts_at(pat_parts, pi + 1, path_parts, k):
                return True
        return False

    if si == len(path_parts):
        return False

    if not fnmatchcase(path_parts[si], pat):
        return False

    return _match_parts_at(pat_parts, pi + 1, path_parts, si + 1)


def parse_ignore_line(raw: str, *, allow_negation: bool) -> ParsedIgnoreLine | None:
    """Parse a gitignore-like line with strict, minimal escaping support.

    Supported:
    - Blank lines are ignored
    - `#` comments when `#` is the first character
    - `\\#` and `\\!` to treat leading `#` / `!` as literals
    - `!` negation when `allow_negation=True` and `!` is the first character
    - trailing spaces are ignored unless escaped with a backslash (`\\ `)

    Any other backslash usage is rejected to avoid silent misinterpretation.
    """
    line = raw.rstrip("\r\n")
    if not line:
        return None

    # Comments only when `#` is the very first character (leading spaces are significant).
    if line.startswith("#"):
        return None

    # Trim trailing spaces unless escaped, per gitignore behavior.
    line = _strip_trailing_spaces(line)
    if not line:
        return None

    escaped_leading_bang = False
    if line.startswith("\\#"):
        line = line[1:]
    elif line.startswith("\\!"):
        escaped_leading_bang = True
        line = line[1:]

    negated = False
    if allow_negation and not escaped_leading_bang and line.startswith("!"):
        negated = True
        line = line[1:]
        if not line:
            raise ValueError("Invalid ignore rule: '!'")

    # Any other backslash use is rejected (we do not implement full gitignore escaping).
    if "\\" in line:
        raise ValueError(
            "Unsupported backslash escape in ignore rule. "
            "Supported: \\# (literal #), \\! (literal !), and escaping trailing space (\\ )."
        )

    return ParsedIgnoreLine(pattern=line, negated=negated)


def _strip_trailing_spaces(line: str) -> str:
    # Gitignore ignores trailing spaces unless the last space is escaped.
    i = len(line)
    while i > 0 and line[i - 1] == " ":
        # Is this trailing space escaped by an unescaped backslash?
        if i >= 2 and line[i - 2] == "\\":
            # Count consecutive backslashes before the space.
            j = i - 2
            backslashes = 0
            while j >= 0 and line[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 1:
                # Remove the escaping backslash and keep one trailing space as part of the pattern.
                return line[: i - 2] + " "
        i -= 1
    return line[:i]
