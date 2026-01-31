from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def _tokens_from_cli(output: str) -> int:
    m = re.search(r"Artifact tokens:\s*([0-9,]+)", output)
    assert m, output
    return int(m.group(1).replace(",", ""))


def test_cli_pack_and_compress_reduces_tokens(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        "import typing\n\n"
        "def f(x: int, y: int) -> int:\n"
        "    \"\"\"Add two numbers.\"\"\"\n"
        "    return x + y\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    full = runner.invoke(app, ["pack", ".", "--format", "markdown", "--output", "full.md", "--include", "src/**"])
    assert full.exit_code == 0, full.output
    full_tokens = _tokens_from_cli(full.output)

    comp = runner.invoke(
        app, ["pack", ".", "--format", "markdown", "--output", "comp.md", "--include", "src/**", "--compress"]
    )
    assert comp.exit_code == 0, comp.output
    comp_tokens = _tokens_from_cli(comp.output)

    assert comp_tokens < full_tokens


def test_cli_pack_infers_format_from_output_extension(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    res = runner.invoke(app, ["pack", ".", "--output", "out.jsonl", "--include", "src/**"])
    assert res.exit_code == 0, res.output
    out = tmp_path / "out.jsonl"
    assert out.exists()
    first = out.read_text(encoding="utf-8").splitlines()[0]
    meta = json.loads(first)
    assert meta["type"] == "meta"


def test_cli_pack_rejects_conflicting_format_and_output_extension(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    res = runner.invoke(app, ["pack", ".", "--format", "markdown", "--output", "out.jsonl", "--include", "src/**"])
    assert res.exit_code != 0
    assert "extension implies format jsonl" in res.output
