"""Deterministic repository discovery for `anatomize pack`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anatomize.core.exclude import Excluder
from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.match import GlobMatcher


@dataclass(frozen=True)
class DiscoveredPath:
    absolute_path: Path
    relative_posix: str
    is_dir: bool
    is_symlink: bool
    size_bytes: int
    is_binary: bool


def discover_paths(
    root: Path,
    *,
    excluder: Excluder,
    include_patterns: list[str] | None,
    symlinks: SymlinkPolicy,
    max_file_bytes: int,
) -> list[DiscoveredPath]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Root must be an existing directory: {root}")

    include_matcher = GlobMatcher(include_patterns or [])

    results: list[DiscoveredPath] = []

    # Manual recursion gives us deterministic traversal + easy symlink control.
    def walk_dir(abs_dir: Path, rel_dir_posix: str) -> None:
        entries = sorted(abs_dir.iterdir(), key=lambda p: p.name)
        for entry in entries:
            is_symlink = entry.is_symlink()
            if is_symlink:
                if entry.is_dir() and symlinks not in (SymlinkPolicy.DIRS, SymlinkPolicy.ALL):
                    continue
                if entry.is_file() and symlinks not in (SymlinkPolicy.FILES, SymlinkPolicy.ALL):
                    continue

            rel = f"{rel_dir_posix}/{entry.name}" if rel_dir_posix else entry.name
            rel_posix = rel.replace("\\", "/")

            try:
                is_dir = entry.is_dir()
            except OSError as e:
                raise ValueError(f"Failed to stat path: {entry}") from e

            if excluder.is_excluded(rel_posix, is_dir=is_dir):
                continue

            if include_patterns and not include_matcher.matches_any(rel_posix, is_dir=is_dir):
                # If the user provided an allowlist, exclude anything that doesn't match.
                # Note: directories can still be traversed if they match via a descendant;
                # our simple include matcher cannot prove that cheaply, so we traverse all
                # non-excluded directories and filter files at the leaf.
                if not is_dir:
                    continue

            if is_dir:
                results.append(
                    DiscoveredPath(
                        absolute_path=entry.resolve(),
                        relative_posix=rel_posix,
                        is_dir=True,
                        is_symlink=is_symlink,
                        size_bytes=0,
                        is_binary=False,
                    )
                )
                walk_dir(entry, rel_posix)
                continue

            size = entry.stat().st_size
            if max_file_bytes > 0 and size > max_file_bytes:
                raise ValueError(f"File exceeds max size ({max_file_bytes} bytes): {rel_posix} ({size} bytes)")

            is_binary = _is_binary_file(entry)
            results.append(
                DiscoveredPath(
                    absolute_path=entry.resolve(),
                    relative_posix=rel_posix,
                    is_dir=False,
                    is_symlink=is_symlink,
                    size_bytes=size,
                    is_binary=is_binary,
                )
            )

    results.append(
        DiscoveredPath(
            absolute_path=root,
            relative_posix=".",
            is_dir=True,
            is_symlink=root.is_symlink(),
            size_bytes=0,
            is_binary=False,
        )
    )
    walk_dir(root, "")

    # Deterministic ordering: directories first, then files, both lexicographic by rel path.
    results.sort(key=lambda d: (0 if d.is_dir else 1, d.relative_posix))
    return results


def _is_binary_file(path: Path, *, sniff_bytes: int = 8192) -> bool:
    try:
        data = path.read_bytes()[:sniff_bytes]
    except OSError:
        return True
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
