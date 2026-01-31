"""Representation policy for hybrid pack mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatchcase
from functools import cache
from pathlib import PurePosixPath

from anatomize.core.exclude import parse_ignore_line


class FileRepresentation(str, Enum):
    META = "meta"
    SUMMARY = "summary"
    CONTENT = "content"


@dataclass(frozen=True)
class RepresentationRule:
    pattern: str
    representation: FileRepresentation
    anchored: bool
    directory_only: bool
    has_slash: bool


def compile_representation_rules(patterns: list[str], representation: FileRepresentation) -> list[RepresentationRule]:
    rules: list[RepresentationRule] = []
    for raw in patterns:
        parsed = parse_ignore_line(raw, allow_negation=False)
        if parsed is None:
            continue
        pat = parsed.pattern

        directory_only = pat.endswith("/")
        if directory_only:
            pat = pat.rstrip("/")
            if not pat:
                continue

        anchored = pat.startswith("/")
        if anchored:
            pat = pat.lstrip("/")
            if not pat:
                continue

        has_slash = "/" in pat
        rules.append(
            RepresentationRule(
                pattern=pat,
                representation=representation,
                anchored=anchored,
                directory_only=directory_only,
                has_slash=has_slash,
            )
        )
    return rules


@dataclass(frozen=True)
class RepresentationPolicy:
    rules: list[RepresentationRule]

    def resolve(self, rel_posix: str, *, is_dir: bool, default: FileRepresentation) -> FileRepresentation:
        rel_posix = rel_posix.strip("/")
        path = PurePosixPath(rel_posix) if rel_posix else PurePosixPath(".")
        rep = default
        for rule in self.rules:
            if _matches(path, rule, is_dir=is_dir):
                rep = rule.representation
        return rep


def _matches(path: PurePosixPath, rule: RepresentationRule, *, is_dir: bool) -> bool:
    if rule.directory_only:
        if is_dir and _match_single(path, rule):
            return True
        for parent in path.parents:
            if _match_single(parent, rule):
                return True
        return False
    return _match_single(path, rule)


def _match_single(path: PurePosixPath, rule: RepresentationRule) -> bool:
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
def _match_parts_at(
    pat_parts: tuple[str, ...], pi: int, path_parts: tuple[str, ...], si: int
) -> bool:
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
