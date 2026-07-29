"""Deterministic repository discovery for `anatomize pack`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anatomize.core.exclude import Excluder
from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.match import GlobMatcher


@dataclass(frozen=True)
class DiscoveredPath:
    """A discovered file or directory in the repository.

    Attributes
    ----------
    absolute_path
        Resolved absolute path to the entry.
    relative_posix
        Path relative to the root in POSIX format.
    is_dir
        True if this entry is a directory.
    is_symlink
        True if this entry is a symbolic link.
    size_bytes
        File size in bytes (0 for directories).
    is_binary
        True if the file appears to be binary.
    """

    absolute_path: Path
    relative_posix: str
    is_dir: bool
    is_symlink: bool
    size_bytes: int
    is_binary: bool


@dataclass(frozen=True)
class DiscoveryTraceItem:
    """Trace record explaining a discovery decision.

    Attributes
    ----------
    path
        Relative path in POSIX format.
    is_dir
        True if the entry is a directory.
    decision
        Either 'included' or 'excluded'.
    reason
        Why excluded: 'ignore' (matched ignore pattern), 'include'
        (not in include list), 'symlink', 'broken_symlink', or None for
        included files.
    matched_pattern
        The pattern that matched (for ignore exclusions).
    matched_source
        Source file of the matched pattern (e.g., '.gitignore').
    """

    path: str
    is_dir: bool
    decision: str  # "included" or "excluded"
    reason: str | None
    matched_pattern: str | None
    matched_source: str | None


def discover_paths(
    root: Path,
    *,
    excluder: Excluder,
    include_patterns: list[str] | None,
    symlinks: SymlinkPolicy,
    trace: list[DiscoveryTraceItem] | None = None,
) -> list[DiscoveredPath]:
    """Discover all files and directories in a repository.

    Performs deterministic, depth-first traversal with filtering based
    on ignore patterns, include patterns, and symlink policy. File sizes are
    recorded here and enforced by the caller after task selection so an
    unrelated large file cannot block a focused slice.

    Parameters
    ----------
    root
        Root directory to discover.
    excluder
        Excluder instance for ignore pattern matching.
    include_patterns
        Optional allowlist of glob patterns (files not matching are excluded).
    symlinks
        Policy for following symbolic links.
    trace
        Optional list to receive discovery decision traces.

    Returns
    -------
    list[DiscoveredPath]
        Sorted list of discovered paths (directories first, then files).

    Raises
    ------
    ValueError
        If root doesn't exist or isn't a directory.
    """
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
                if not entry.exists():
                    if trace is not None:
                        trace.append(
                            DiscoveryTraceItem(
                                path=(f"{rel_dir_posix}/{entry.name}" if rel_dir_posix else entry.name),
                                is_dir=False,
                                decision="excluded",
                                reason="broken_symlink",
                                matched_pattern=None,
                                matched_source=None,
                            )
                        )
                    continue
                is_link_dir = entry.is_dir()
                is_link_file = entry.is_file()
                permitted = (is_link_dir and symlinks in (SymlinkPolicy.DIRS, SymlinkPolicy.ALL)) or (
                    is_link_file and symlinks in (SymlinkPolicy.FILES, SymlinkPolicy.ALL)
                )
                if not permitted:
                    if trace is not None:
                        trace.append(
                            DiscoveryTraceItem(
                                path=(f"{rel_dir_posix}/{entry.name}" if rel_dir_posix else entry.name),
                                is_dir=is_link_dir,
                                decision="excluded",
                                reason="symlink",
                                matched_pattern=None,
                                matched_source=None,
                            )
                        )
                    continue

            rel = f"{rel_dir_posix}/{entry.name}" if rel_dir_posix else entry.name
            rel_posix = rel.replace("\\", "/")

            try:
                is_dir = entry.is_dir()
            except OSError as e:
                raise ValueError(f"Failed to stat path: {entry}") from e

            excluded, matched = excluder.explain(rel_posix, is_dir=is_dir)
            if excluded:
                if trace is not None:
                    matched_pattern = None
                    if matched is not None:
                        matched_pattern = matched.pattern + ("/" if matched.directory_only else "")
                    trace.append(
                        DiscoveryTraceItem(
                            path=rel_posix,
                            is_dir=is_dir,
                            decision="excluded",
                            reason="ignore",
                            matched_pattern=matched_pattern,
                            matched_source=matched.source if matched is not None else None,
                        )
                    )
                continue

            if include_patterns and not include_matcher.matches_any(rel_posix, is_dir=is_dir):
                # If the user provided an allowlist, exclude anything that doesn't match.
                # Note: directories can still be traversed if they match via a descendant;
                # our simple include matcher cannot prove that cheaply, so we traverse all
                # non-excluded directories and filter files at the leaf.
                if not is_dir:
                    if trace is not None:
                        trace.append(
                            DiscoveryTraceItem(
                                path=rel_posix,
                                is_dir=False,
                                decision="excluded",
                                reason="include",
                                matched_pattern=None,
                                matched_source=None,
                            )
                        )
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

            try:
                size = entry.stat().st_size
            except OSError as e:
                raise ValueError(f"Failed to stat file: {rel_posix}") from e

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
            if trace is not None:
                trace.append(
                    DiscoveryTraceItem(
                        path=rel_posix,
                        is_dir=False,
                        decision="included",
                        reason=None,
                        matched_pattern=None,
                        matched_source=None,
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
        with path.open("rb") as f:
            data = f.read(sniff_bytes)
    except OSError:
        return True
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
