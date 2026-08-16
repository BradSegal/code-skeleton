from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_index_find_impact_and_changed_commands(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    target = tmp_path / "src" / "pkg" / "core.py"
    target.write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "fixture")

    runner = CliRunner()
    output = tmp_path.parent / "repository-index.json"
    result = runner.invoke(
        app,
        ["index", str(tmp_path), "--output", str(output)],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "find",
            "answer",
            "--root",
            str(tmp_path),
            "--index",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["qualified_name"] == "pkg.core.answer"

    result = runner.invoke(
        app,
        [
            "impact",
            "answer",
            "--root",
            str(tmp_path),
            "--index",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["focus"] == ["src/pkg/core.py"]

    target.write_text("def answer() -> int:\n    return 43\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["changed", "--base", "HEAD", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changed_files"] == ["src/pkg/core.py"]


def test_repository_artifact_paths_are_root_relative_and_errors_are_concise(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "pkg" / "core.py").write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")

    runner = CliRunner()
    result = runner.invoke(app, ["index", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / ".anatomy" / "index.json").is_file()

    result = runner.invoke(
        app,
        ["find", "value", "--root", str(root), "--index", ".anatomy/index.json", "--json"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        ["find", "value", "--root", str(root), "--index", "missing.json"],
    )
    assert result.exit_code == 1
    assert "Failed to read repository index" in result.output
    assert "Traceback" not in result.output


def test_capabilities_and_impact_review_pack_are_machine_readable(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["capabilities"])
    assert result.exit_code == 0, result.output
    capabilities = json.loads(result.output)
    assert capabilities["repository_intelligence"]["baseline_consumers"] is True
    assert capabilities["repository_intelligence"]["semantic_references"]["failure_mode"] == "explicit"

    pack = tmp_path.parent / "review-pack.json"
    result = runner.invoke(
        app,
        ["impact", "answer", "--root", str(tmp_path), "--pack-output", str(pack)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(pack.read_text(encoding="utf-8"))
    assert payload["files"][0]["path"] == "pkg/core.py"
    assert payload["files"][0]["relationships"][0]["source"] == "target"
