from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    "flag",
    [
        "--max-total-bytes",
        "--max-total-tokens",
        "--split-output-bytes",
        "--split-output-tokens",
        "--markdown-mode",
    ],
)
def test_pack_removed_flags_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    res = runner.invoke(app, ["pack", ".", flag, "1"])
    assert res.exit_code != 0
    assert "No such option" in res.output
