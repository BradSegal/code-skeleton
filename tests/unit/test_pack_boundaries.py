from __future__ import annotations

from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.discovery import DiscoveryTraceItem, discover_paths
from anatomize.pack.formats import ContentEncoding, PackFormat
from anatomize.pack.ignore import build_excluder
from anatomize.pack.runner import pack

pytestmark = pytest.mark.unit


def _pack(
    root: Path,
    output: Path,
    *,
    entries: list[Path] | None = None,
    max_file_bytes: int = 1_000_000,
    target: Path | None = None,
    target_module: str | None = None,
    reverse_deps: bool = False,
) -> None:
    pack(
        root=root,
        output=output,
        fmt=PackFormat.MARKDOWN,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=max_file_bytes,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        entries=entries or [],
        deps=False,
        python_roots=[],
        target=target,
        target_module=target_module,
        reverse_deps=reverse_deps,
    )


def test_output_inside_root_is_not_ingested_on_repeat(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "codebase.md"

    _pack(tmp_path, output)
    first = output.read_bytes()
    _pack(tmp_path, output)

    assert output.read_bytes() == first
    assert b"## File: codebase.md" not in first


def test_unselected_large_file_does_not_block_focus(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "unrelated.bin").write_bytes(b"x" * 10_000)

    _pack(
        tmp_path,
        tmp_path / "focus.md",
        entries=[target],
        max_file_bytes=100,
    )

    with pytest.raises(ValueError, match="unrelated.bin"):
        _pack(tmp_path, tmp_path / "full.md", max_file_bytes=100)


def test_invalid_request_is_rejected_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> list[object]:
        raise AssertionError("discovery should not run")

    monkeypatch.setattr("anatomize.pack.runner.discover_paths", fail_if_called)

    with pytest.raises(ValueError, match="Specify at most one"):
        _pack(
            tmp_path,
            tmp_path / "invalid.md",
            target=Path("a.py"),
            target_module="pkg.a",
            reverse_deps=True,
        )


def test_broken_symlink_is_skipped_deterministically(tmp_path: Path) -> None:
    try:
        (tmp_path / "broken").symlink_to(tmp_path / "missing")
    except OSError:
        pytest.skip("symlinks are unavailable")
    (tmp_path / "kept.txt").write_text("ok\n", encoding="utf-8")
    trace: list[DiscoveryTraceItem] = []

    discovered = discover_paths(
        tmp_path,
        excluder=build_excluder(
            tmp_path,
            ignore=[],
            ignore_files=[],
            respect_standard_ignores=False,
        ),
        include_patterns=None,
        symlinks=SymlinkPolicy.FORBID,
        trace=trace,
    )

    assert [item.relative_posix for item in discovered if not item.is_dir] == ["kept.txt"]
    assert any(item.path == "broken" and item.reason == "broken_symlink" for item in trace)
