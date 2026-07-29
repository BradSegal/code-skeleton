"""Representation policy for hybrid pack mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anatomize.pack.match import GlobMatcher, GlobRule, compile_glob_rules


class FileRepresentation(str, Enum):
    """How a file is represented in hybrid pack output.

    Attributes
    ----------
    META
        Only metadata (path, size, language).
    SUMMARY
        Structural summary (e.g., JSON paths, headings).
    CONTENT
        Full file content.
    """

    META = "meta"
    SUMMARY = "summary"
    CONTENT = "content"


@dataclass(frozen=True)
class RepresentationRule:
    """Compiled pattern rule for representation matching.

    Attributes
    ----------
    glob
        Parsed reusable glob rule.
    representation
        Representation to apply when matched.
    """

    glob: GlobRule
    representation: FileRepresentation


def compile_representation_rules(patterns: list[str], representation: FileRepresentation) -> list[RepresentationRule]:
    """Compile glob patterns into representation rules.

    Parameters
    ----------
    patterns
        List of gitignore-style glob patterns.
    representation
        Representation to assign to matching files.

    Returns
    -------
    list[RepresentationRule]
        Compiled rules for pattern matching.
    """
    return [RepresentationRule(glob=glob, representation=representation) for glob in compile_glob_rules(patterns)]


@dataclass(frozen=True)
class RepresentationPolicy:
    """Policy for resolving file representations from rules.

    Attributes
    ----------
    rules
        List of representation rules (applied in order).
    """

    rules: list[RepresentationRule]

    def resolve(self, rel_posix: str, *, is_dir: bool, default: FileRepresentation) -> FileRepresentation:
        """Resolve the representation for a path.

        Parameters
        ----------
        rel_posix
            Relative path in POSIX format.
        is_dir
            True if the path is a directory.
        default
            Default representation if no rule matches.

        Returns
        -------
        FileRepresentation
            Resolved representation (last matching rule wins).
        """
        rep = default
        matcher = GlobMatcher([])
        for rule in self.rules:
            if matcher.matches_rule(rel_posix, rule.glob, is_dir=is_dir):
                rep = rule.representation
        return rep
