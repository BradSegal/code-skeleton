"""Small fail-loud Git boundary for repository intelligence."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_text(root: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Git command failed: {' '.join(arguments)}: {message}")
    return completed.stdout


def git_bytes(root: Path, arguments: list[str]) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Git command failed: {' '.join(arguments)}: {message}")
    return completed.stdout


def nul_paths(value: str) -> list[str]:
    return [item for item in value.split("\0") if item]
