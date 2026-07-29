from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def test_check_validates_configured_artifacts_and_detects_drift(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    source = tmp_path / "src" / "pkg" / "core.py"
    source.write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / ".anatomize.yaml").write_text(
        "output: .anatomy\n"
        "sources:\n"
        "  - path: src\n"
        "    output: src\n"
        "    level: modules\n"
        "formats: [json]\n"
        "pack:\n"
        "  format: markdown\n"
        "  output: .anatomy/repository.md\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    for arguments in (
        ["generate", "--config", str(tmp_path / ".anatomize.yaml")],
        ["pack", str(tmp_path), "--config", str(tmp_path / ".anatomize.yaml")],
        ["index", str(tmp_path)],
    ):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["check", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    checks = {item["check_id"]: item for item in json.loads(result.output)["checks"]}
    assert checks["skeleton:src"]["status"] == "verified"
    assert checks["pack"]["status"] == "verified"
    assert checks["index"]["status"] == "verified"

    source.write_text("def changed_value() -> int:\n    return 2\n", encoding="utf-8")
    result = runner.invoke(app, ["check", str(tmp_path), "--json"])
    assert result.exit_code == 1
    checks = {item["check_id"]: item for item in json.loads(result.output)["checks"]}
    assert checks["skeleton:src"]["status"] == "invalid"
    assert checks["pack"]["status"] == "invalid"
    assert checks["index"]["status"] == "invalid"
