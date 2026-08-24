"""Shared Git command helper for repository fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(root: Path, *arguments: str) -> None:
    """Run one fixture Git command and fail with captured diagnostics."""
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def git_output(root: Path, *arguments: str) -> str:
    """Run one fixture Git query and return its stripped standard output."""
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
