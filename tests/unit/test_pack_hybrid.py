from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.formats import ContentEncoding, PackFormat
from anatomize.pack.limit import parse_output_limit
from anatomize.pack.mode import PackMode
from anatomize.pack.runner import pack
from anatomize.pack.summaries import SummaryConfig

pytestmark = pytest.mark.unit


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        out.append(json.loads(line))
    return out


def test_pack_hybrid_defaults_to_python_summaries(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSONL,
        mode=PackMode.HYBRID,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        workers=0,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=None,
        representation_content=[],
        representation_summary=[],
        representation_meta=[],
        fit_to_max_output=False,
        summary_config=SummaryConfig(),
        entries=[],
        deps=False,
        python_roots=[],
    )

    rows = _read_jsonl(out)
    meta = rows[0]
    assert meta["type"] == "meta"
    assert meta["mode"] == "hybrid"

    files = [r for r in rows if r["type"] == "file"]
    a = next(r for r in files if r["path"] == "a.py")
    b = next(r for r in files if r["path"] == "b.txt")
    assert a["representation"] == "summary"
    assert a["summary"] is not None
    assert a["content"] is None
    assert b["representation"] == "meta"
    assert b["summary"] is None


def test_pack_hybrid_content_rule_includes_full_content(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")
    (tmp_path / "b.json").write_text("{\"a\": 1}\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSONL,
        mode=PackMode.HYBRID,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        workers=0,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=None,
        representation_content=["b.json"],
        representation_summary=[],
        representation_meta=[],
        fit_to_max_output=False,
        summary_config=SummaryConfig(),
        entries=[],
        deps=False,
        python_roots=[],
    )

    rows = _read_jsonl(out)
    files = [r for r in rows if r["type"] == "file"]
    b = next(r for r in files if r["path"] == "b.json")
    assert b["representation"] == "content"
    assert b["content"] is not None
    assert b["content_encoding"] == "fence-safe"


def test_pack_hybrid_summary_rule_requires_supported_type(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    with pytest.raises(ValueError, match="Unsupported summary type"):
        pack(
            root=tmp_path,
            output=out,
            fmt=PackFormat.JSONL,
            mode=PackMode.HYBRID,
            include=[],
            ignore=[],
            ignore_files=[],
            respect_standard_ignores=False,
            symlinks=SymlinkPolicy.FORBID,
            max_file_bytes=1_000_000,
            workers=0,
            token_encoding="cl100k_base",
            compress=False,
            content_encoding=ContentEncoding.FENCE_SAFE,
            line_numbers=False,
            include_structure=False,
            include_files=True,
            max_output=None,
            split_output=None,
            representation_content=[],
            representation_summary=["a.txt"],
            representation_meta=[],
            fit_to_max_output=False,
            summary_config=SummaryConfig(),
            entries=[],
            deps=False,
            python_roots=[],
        )
    assert not out.exists()


def test_pack_hybrid_fit_to_max_output_downgrades_summaries(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def f1() -> int:\n    return 1\n\ndef f2() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("def g() -> int:\n    return 3\n", encoding="utf-8")

    baseline = tmp_path / "baseline.jsonl"
    base = pack(
        root=tmp_path,
        output=baseline,
        fmt=PackFormat.JSONL,
        mode=PackMode.HYBRID,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        workers=0,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=None,
        representation_content=[],
        representation_summary=[],
        representation_meta=[],
        fit_to_max_output=False,
        summary_config=SummaryConfig(),
        entries=[],
        deps=False,
        python_roots=[],
    )
    base_tokens = sum(a.tokens for a in base.artifacts)

    out = tmp_path / "fit.jsonl"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSONL,
        mode=PackMode.HYBRID,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        workers=0,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=parse_output_limit(f"{base_tokens - 1}t"),
        split_output=None,
        representation_content=[],
        representation_summary=[],
        representation_meta=[],
        fit_to_max_output=True,
        summary_config=SummaryConfig(),
        entries=[],
        deps=False,
        python_roots=[],
    )

    rows = _read_jsonl(out)
    meta = rows[0]
    assert meta["fit_to_max_output"] is True
    assert meta["selection_trace"]

    files = [r for r in rows if r["type"] == "file"]
    downgraded = [r for r in files if r["representation"] == "meta"]
    assert downgraded
