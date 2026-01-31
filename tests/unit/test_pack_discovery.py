from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.formats import PackFormat
from anatomize.pack.runner import pack

pytestmark = pytest.mark.unit


def test_pack_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("ok\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("nope\n", encoding="utf-8")

    out = tmp_path / "out.json"
    res = pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSON,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=True,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        entries=[],
        deps=False,
        python_roots=[],
    )
    assert [a.path for a in res.artifacts] == [out.resolve()]

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = [f["path"] for f in data["files"]]
    assert "kept.txt" in paths
    assert "ignored.txt" not in paths


def test_pack_omits_binary_contents(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")

    out = tmp_path / "out.json"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSON,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        entries=[],
        deps=False,
        python_roots=[],
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    by_path = {f["path"]: f for f in data["files"]}
    assert by_path["bin.dat"]["is_binary"] is True
    assert by_path["bin.dat"]["content"] is None
