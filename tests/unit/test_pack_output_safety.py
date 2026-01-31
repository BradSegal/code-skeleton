from __future__ import annotations

from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.formats import ContentEncoding, PackFormat
from anatomize.pack.limit import parse_output_limit
from anatomize.pack.runner import pack
from anatomize.pack.slicing import SliceBackend

pytestmark = pytest.mark.unit


def test_pack_markdown_parsable_uses_safe_fences(tmp_path: Path) -> None:
    # Content includes a triple-backtick fence, which would break naive markdown bundling.
    (tmp_path / "a.py").write_text("def f():\n    s = \"```\"\n    return s\n", encoding="utf-8")

    out = tmp_path / "out.md"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.MARKDOWN,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=True,
        include_files=True,
        max_output=None,
        split_output=None,
        entries=[],
        deps=False,
        python_roots=[],
    )

    text = out.read_text(encoding="utf-8")
    assert "````python" in text or "````" in text


def test_pack_markdown_rejects_verbatim_content_encoding(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    out = tmp_path / "out.md"

    with pytest.raises(ValueError, match="Markdown output requires"):
        pack(
            root=tmp_path,
            output=out,
            fmt=PackFormat.MARKDOWN,
            include=[],
            ignore=[],
            ignore_files=[],
            respect_standard_ignores=False,
            symlinks=SymlinkPolicy.FORBID,
            max_file_bytes=1_000_000,
            token_encoding="cl100k_base",
            compress=False,
            content_encoding=ContentEncoding.VERBATIM,
            line_numbers=False,
            include_structure=True,
            include_files=True,
            max_output=None,
            split_output=None,
            entries=[],
            deps=False,
            python_roots=[],
        )
    assert not out.exists()


def test_pack_split_output_bytes_creates_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a" * 200 + "\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b" * 200 + "\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("c" * 200 + "\n", encoding="utf-8")

    out = tmp_path / "bundle.txt"
    res = pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.PLAIN,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.VERBATIM,
        line_numbers=False,
        include_structure=True,
        include_files=True,
        max_output=None,
        split_output=parse_output_limit("800b"),
        entries=[],
        deps=False,
        python_roots=[],
    )

    assert len(res.artifacts) >= 2
    for a in res.artifacts:
        assert a.path.exists()
        assert a.bytes > 0


def test_pack_max_output_tokens_enforced(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    out = tmp_path / "out.md"

    with pytest.raises(ValueError, match="max-output"):
        pack(
            root=tmp_path,
            output=out,
            fmt=PackFormat.MARKDOWN,
            include=[],
            ignore=[],
            ignore_files=[],
            respect_standard_ignores=False,
            symlinks=SymlinkPolicy.FORBID,
            max_file_bytes=1_000_000,
            token_encoding="cl100k_base",
            compress=False,
            content_encoding=ContentEncoding.FENCE_SAFE,
            line_numbers=False,
            include_structure=True,
            include_files=True,
            max_output=parse_output_limit("1t"),
            split_output=None,
            entries=[],
            deps=False,
            python_roots=[],
        )
    assert not out.exists()


def test_pack_split_output_tokens_limits_parts(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("a " * 200 + "\n", encoding="utf-8")
    (root / "b.txt").write_text("b " * 200 + "\n", encoding="utf-8")
    (root / "c.txt").write_text("c " * 200 + "\n", encoding="utf-8")

    out1 = tmp_path / "full.txt"
    full = pack(
        root=root,
        output=out1,
        fmt=PackFormat.PLAIN,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.VERBATIM,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=None,
        entries=[],
        deps=False,
        python_roots=[],
    )
    total_tokens = sum(a.tokens for a in full.artifacts)
    limit_tokens = max(1, total_tokens // 2)

    out = tmp_path / "bundle.txt"
    res = pack(
        root=root,
        output=out,
        fmt=PackFormat.PLAIN,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.VERBATIM,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=parse_output_limit(f"{limit_tokens}t"),
        entries=[],
        deps=False,
        python_roots=[],
    )

    assert len(res.artifacts) >= 2
    for a in res.artifacts:
        assert a.tokens <= limit_tokens


def test_pack_jsonl_split_output_bytes_creates_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a" * 200 + "\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b" * 200 + "\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("c" * 200 + "\n", encoding="utf-8")

    out_full = tmp_path / "full.jsonl"
    pack(
        root=tmp_path,
        output=out_full,
        fmt=PackFormat.JSONL,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.VERBATIM,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=None,
        entries=[],
        deps=False,
        python_roots=[],
    )

    lines = out_full.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    minimal = (lines[0] + "\n" + lines[1] + "\n").encode("utf-8")
    # Splitting adds a small amount of metadata into the JSONL meta record (e.g. split limit string),
    # so give a small safety margin while still forcing multi-part output.
    limit_bytes = len(minimal) + 256

    out = tmp_path / "bundle.jsonl"
    res = pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSONL,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.VERBATIM,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=parse_output_limit(f"{limit_bytes}b"),
        entries=[],
        deps=False,
        python_roots=[],
    )

    assert len(res.artifacts) >= 2
    for a in res.artifacts:
        assert a.path.exists()


def test_pack_jsonl_output_is_written(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    res = pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSONL,
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.VERBATIM,
        line_numbers=False,
        include_structure=True,
        include_files=True,
        max_output=None,
        split_output=None,
        entries=[],
        deps=False,
        python_roots=[],
    )

    assert res.artifacts and res.artifacts[0].path == out
    text = out.read_text(encoding="utf-8").splitlines()
    assert text[0].startswith("{") and "\"type\":\"meta\"" in text[0]
    assert any("\"type\":\"file\"" in line for line in text)


def test_pack_base64_content_encoding_roundtrips_in_json(tmp_path: Path) -> None:
    import base64
    import json

    (tmp_path / "a.txt").write_text("hi\nthere\n", encoding="utf-8")
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
        content_encoding=ContentEncoding.BASE64,
        line_numbers=False,
        include_structure=False,
        include_files=True,
        max_output=None,
        split_output=None,
        entries=[],
        deps=False,
        python_roots=[],
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    file_entry = next(f for f in data["files"] if f["path"] == "a.txt")
    assert file_entry["content_encoding"] == "base64"
    decoded = base64.b64decode(file_entry["content"]).decode("utf-8")
    assert decoded == "hi\nthere\n"


def test_pack_split_not_supported_for_json(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="only supported"):
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
            content_encoding=ContentEncoding.VERBATIM,
            line_numbers=False,
            include_structure=True,
            include_files=True,
            max_output=None,
            split_output=parse_output_limit("10b"),
            entries=[],
            deps=False,
            python_roots=[],
        )


def test_pack_uses_requires_pyright_backend(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")
    out = tmp_path / "out.json"
    with pytest.raises(ValueError, match="--uses requires --slice-backend pyright"):
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
            content_encoding=ContentEncoding.VERBATIM,
            line_numbers=False,
            include_structure=True,
            include_files=True,
            max_output=None,
            split_output=None,
            target=Path("a.py"),
            target_module=None,
            reverse_deps=False,
            uses=True,
            uses_include_private=False,
            slice_backend=SliceBackend.IMPORTS,
            pyright_langserver_cmd=None,
            entries=[],
            deps=False,
            python_roots=[],
        )


def test_pack_uses_pyright_missing_is_actionable(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")
    out = tmp_path / "out.json"
    with pytest.raises(ValueError, match="Pyright language server not found"):
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
            content_encoding=ContentEncoding.VERBATIM,
            line_numbers=False,
            include_structure=True,
            include_files=True,
            max_output=None,
            split_output=None,
            target=Path("a.py"),
            target_module=None,
            reverse_deps=False,
            uses=True,
            uses_include_private=False,
            slice_backend=SliceBackend.PYRIGHT,
            pyright_langserver_cmd=["pyright-langserver-not-a-real-binary", "--stdio"],
            entries=[],
            deps=False,
            python_roots=[],
        )
