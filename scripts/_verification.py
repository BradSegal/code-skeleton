"""Shared platform helpers for independently executable release verifiers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def venv_path(environment: Path, name: str) -> Path:
    """Return a named virtual-environment executable on POSIX or Windows."""
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return environment / directory / f"{name}{suffix}"


def create_venv(environment: Path, *, system_site_packages: bool = False) -> None:
    """Create a venv from the base interpreter, including inside a uv-managed venv."""
    if os.name == "nt":
        base_python = Path(sys.base_prefix) / "python.exe"
    else:
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        base_python = Path(sys.base_prefix) / "bin" / f"python{version}"
    if not base_python.is_file():
        base_python = Path(sys.executable)
    command = [str(base_python), "-m", "venv"]
    if system_site_packages:
        command.append("--system-site-packages")
    command.append(str(environment))
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Could not create isolated environment: {detail}")
