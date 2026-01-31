from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.formats import ContentEncoding, PackFormat
from anatomize.pack.limit import parse_output_limit
from anatomize.pack.mode import PackMode
from anatomize.pack.runner import pack
from anatomize.pack.slicing import SliceBackend

pytestmark = pytest.mark.unit


def test_pack_json_includes_overview_and_selected_structure(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "a.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "other" / "x.py").write_text("def nope() -> None:\n    pass\n", encoding="utf-8")

    out = tmp_path / "out.json"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSON,
        mode=PackMode.BUNDLE,
        include=["src/**"],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=0,
        workers=0,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=True,
        include_files=False,
        max_output=None,
        split_output=None,
        representation_content=None,
        representation_summary=None,
        representation_meta=None,
        fit_to_max_output=False,
        summary_config=None,
        target=None,
        target_module=None,
        reverse_deps=False,
        uses=False,
        uses_include_private=False,
        slice_backend=SliceBackend.IMPORTS,
        pyright_langserver_cmd=None,
        entries=[],
        deps=False,
        python_roots=[],
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overview"]["selected"]["files"] == 2
    structure = "\n".join(data["structure_paths"])
    assert "src/" in structure
    assert "other/" not in structure


def test_pack_overview_records_python_parse_error_without_failing(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    # Intentionally invalid Python.
    (tmp_path / "src" / "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")

    out = tmp_path / "out.json"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSON,
        mode=PackMode.BUNDLE,
        include=["src/**"],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=0,
        workers=0,
        token_encoding="cl100k_base",
        compress=False,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=False,
        include_files=False,
        max_output=parse_output_limit("1mb"),
        split_output=None,
        representation_content=None,
        representation_summary=None,
        representation_meta=None,
        fit_to_max_output=False,
        summary_config=None,
        target=None,
        target_module=None,
        reverse_deps=False,
        uses=False,
        uses_include_private=False,
        slice_backend=SliceBackend.IMPORTS,
        pyright_langserver_cmd=None,
        entries=[],
        deps=False,
        python_roots=[],
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    py = data["overview"]["python_top_level"]
    assert py is not None
    assert py["parse_errors"] == 1
    assert py["modules"][0]["path"] == "src/bad.py"
    assert py["modules"][0]["error"]["type"] == "syntax_error"
