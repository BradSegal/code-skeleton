from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def test_cli_init_writes_config_and_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    res = runner.invoke(app, ["init", "--preset", "standard"])
    assert res.exit_code == 0, res.output
    cfg = tmp_path / ".anatomize.yaml"
    assert cfg.exists()
    text = cfg.read_text(encoding="utf-8")
    assert "sources:" in text
    assert "output:" in text

    again = runner.invoke(app, ["init", "--preset", "standard"])
    assert again.exit_code != 0

