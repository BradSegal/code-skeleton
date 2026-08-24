from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_clean_baseline_and_optional_mcp_installations_are_independent(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert built.returncode == 0, built.stderr
    wheels = list(wheelhouse.glob("anatomize-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    baseline = tmp_path / "baseline"
    _create_venv(baseline)
    baseline_python = _venv_python(baseline)
    installed = subprocess.run(
        [str(baseline_python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert installed.returncode == 0, installed.stderr
    baseline_probe = subprocess.run(
        [
            str(baseline_python),
            "-c",
            (
                "import importlib.util; from pathlib import Path; import anatomize; "
                "from anatomize.mcp_transport import MCPReviewError, MCPReviewService, create_mcp_server; "
                "assert importlib.util.find_spec('mcp') is None; "
                "service=MCPReviewService(Path('.')); "
                "\ntry: create_mcp_server(service)\n"
                "except MCPReviewError as error: assert error.code == 'mcp_dependency_missing'\n"
                "else: raise AssertionError('missing MCP extra was not reported')"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert baseline_probe.returncode == 0, baseline_probe.stderr

    optional = tmp_path / "optional"
    _create_venv(optional)
    optional_python = _venv_python(optional)
    optional_installed = subprocess.run(
        [
            str(optional_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            f"{wheel}[mcp]",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert optional_installed.returncode == 0, optional_installed.stderr
    optional_probe = subprocess.run(
        [
            str(optional_python),
            "-c",
            (
                "from pathlib import Path; from anatomize.mcp_transport import "
                "MCPReviewService, create_mcp_server; "
                "server=create_mcp_server(MCPReviewService(Path('.'))); assert server.name == 'anatomize'"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert optional_probe.returncode == 0, optional_probe.stderr


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _create_venv(environment: Path) -> None:
    if sys.platform == "win32":
        base_python = Path(sys.base_prefix) / "python.exe"
    else:
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        base_python = Path(sys.base_prefix) / "bin" / f"python{version}"
    if not base_python.is_file():
        base_python = Path(sys.executable)
    completed = subprocess.run(
        [str(base_python), "-m", "venv", str(environment)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
