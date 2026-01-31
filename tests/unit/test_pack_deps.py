from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.formats import PackFormat
from anatomize.pack.runner import pack

pytestmark = pytest.mark.unit


def test_pack_dependency_closure(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .a import run\n", encoding="utf-8")
    (pkg / "a.py").write_text("from . import b\n\ndef run() -> None:\n    b.f()\n", encoding="utf-8")
    (pkg / "b.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")
    (src / "unrelated.py").write_text("x = 1\n", encoding="utf-8")

    out = tmp_path / "out.json"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSON,
        include=["src/**"],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=False,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=1_000_000,
        token_encoding="cl100k_base",
        compress=False,
        entries=[Path("src/pkg/a.py")],
        deps=True,
        python_roots=[],
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = {f["path"] for f in data["files"]}
    assert "src/pkg/a.py" in paths
    assert "src/pkg/b.py" in paths
    assert "src/pkg/__init__.py" in paths
    assert "src/unrelated.py" not in paths


def test_pack_dependency_closure_unresolved_local_import_fails(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from .missing import x\n", encoding="utf-8")

    out = tmp_path / "out.json"
    with pytest.raises(ValueError, match="Unresolved local import"):
        pack(
            root=tmp_path,
            output=out,
            fmt=PackFormat.JSON,
            include=["src/**"],
            ignore=[],
            ignore_files=[],
            respect_standard_ignores=False,
            symlinks=SymlinkPolicy.FORBID,
            max_file_bytes=1_000_000,
            token_encoding="cl100k_base",
            compress=False,
            entries=[Path("src/pkg/a.py")],
            deps=True,
            python_roots=[],
        )


def test_pack_deps_fails_if_required_file_excluded_by_ignore(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from . import b\n", encoding="utf-8")
    (pkg / "b.py").write_text("x = 1\n", encoding="utf-8")

    out = tmp_path / "out.json"
    with pytest.raises(ValueError, match="not included by filtering"):
        pack(
            root=tmp_path,
            output=out,
            fmt=PackFormat.JSON,
            include=["src/**"],
            ignore=["src/pkg/b.py"],
            ignore_files=[],
            respect_standard_ignores=False,
            symlinks=SymlinkPolicy.FORBID,
            max_file_bytes=1_000_000,
            token_encoding="cl100k_base",
            compress=False,
            entries=[Path("src/pkg/a.py")],
            deps=True,
            python_roots=[],
        )


def test_pack_reverse_deps_includes_importers_transitively(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "b.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "a.py").write_text("from . import b\n\ny = b.x\n", encoding="utf-8")
    (src / "app.py").write_text("from pkg import a\nprint(a.y)\n", encoding="utf-8")

    out = tmp_path / "out.json"
    pack(
        root=tmp_path,
        output=out,
        fmt=PackFormat.JSON,
        include=["src/**"],
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
        target=Path("src/pkg/b.py"),
        target_module=None,
        reverse_deps=True,
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = {f["path"] for f in data["files"]}
    assert "src/pkg/b.py" in paths
    assert "src/pkg/a.py" in paths
    assert "src/app.py" in paths
