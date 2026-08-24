from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def test_mcp_cli_is_discoverable_and_refuses_unauthenticated_network_exposure(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    runner = CliRunner()

    help_result = runner.invoke(app, ["mcp", "--help"])
    assert help_result.exit_code == 0
    assert "optional read-only MCP server" in help_result.output
    assert "stdio or streamable-http" in help_result.output
    refused = runner.invoke(
        app,
        [
            "mcp",
            str(tmp_path),
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
        ],
    )
    assert refused.exit_code == 2
    assert "error[mcp_network_exposure_refused]" in refused.output
    assert "loopback" in refused.output
    assert "Traceback" not in refused.output
