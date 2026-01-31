from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def _require_pyright() -> None:
    if os.environ.get("ANATOMIZE_RUN_PYRIGHT_E2E") != "1":
        pytest.skip("pyright e2e not enabled")
    if shutil.which("pyright-langserver") is None:
        pytest.skip("pyright-langserver not installed")


def _run_pack_uses(*, root: Path, target: str, uses_include_private: bool = False) -> set[str]:
    out = root / "out.json"
    args = [
        "pack",
        ".",
        "--format",
        "json",
        "--output",
        str(out),
        "--include",
        "src/**",
        "--target",
        target,
        "--uses",
        "--slice-backend",
        "pyright",
    ]
    if uses_include_private:
        args.append("--uses-include-private")

    res = CliRunner().invoke(app, args)
    assert res.exit_code == 0, res.output
    data = json.loads(out.read_text(encoding="utf-8"))
    return {f["path"] for f in data["files"]}


def test_cli_pack_uses_pyright_selects_references_src_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _require_pyright()

    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def f(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("from pkg.a import f\n\nprint(f(1))\n", encoding="utf-8")
    (pkg / "c.py").write_text("print('unused')\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    paths = _run_pack_uses(root=tmp_path, target="src/pkg/a.py")
    assert "src/pkg/a.py" in paths
    assert "src/pkg/b.py" in paths
    assert "src/pkg/c.py" not in paths


def test_cli_pack_uses_pyright_include_private_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _require_pyright()

    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        (
            "def _private(x: int) -> int:\n"
            "    return x + 1\n\n"
            "def public(x: int) -> int:\n"
            "    return x + 2\n"
        ),
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        (
            "from pkg.a import _private, public\n\n"
            "print(_private(1))\n"
            "print(public(1))\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    without_private = _run_pack_uses(root=tmp_path, target="src/pkg/a.py", uses_include_private=False)
    assert "src/pkg/a.py" in without_private
    assert "src/pkg/b.py" in without_private

    with_private = _run_pack_uses(root=tmp_path, target="src/pkg/a.py", uses_include_private=True)
    assert "src/pkg/a.py" in with_private
    assert "src/pkg/b.py" in with_private


def test_cli_pack_uses_pyright_rejects_bad_langserver_cmd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _require_pyright()

    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("from pkg.a import f\n\nprint(f())\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out.json"
    res = CliRunner().invoke(
        app,
        [
            "pack",
            ".",
            "--format",
            "json",
            "--output",
            str(out),
            "--include",
            "src/**",
            "--target",
            "src/pkg/a.py",
            "--uses",
            "--slice-backend",
            "pyright",
            "--pyright-langserver-cmd",
            "pyright-langserver",
        ],
    )
    assert res.exit_code != 0
    assert "must include" in res.output
    assert "--stdio" in res.output
