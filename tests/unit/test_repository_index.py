from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from anatomize.index import (
    build_changed_report,
    build_impact_report,
    build_repository_index,
    find_symbols,
    write_json,
)
from anatomize.index.models import ImpactRole

pytestmark = pytest.mark.unit


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def indexed_repository(tmp_path: Path) -> Path:
    (tmp_path / "src" / "sample").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "sample" / "__init__.py").write_text(
        'from sample.core import calculate\n\n__all__ = ["calculate"]\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "sample" / "helpers.py").write_text(
        "def normalize(value: int) -> int:\n    return value\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "sample" / "core.py").write_text(
        "from sample.helpers import normalize\n\n"
        "def calculate(value: int) -> int:\n"
        "    return normalize(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_core.py").write_text(
        "from sample.core import calculate\n\n" "def test_calculate() -> None:\n" "    assert calculate(1) == 1\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "Use `calculate` through the public API.\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "sample-cli").write_text(
        "#!/usr/bin/env python3\nfrom sample.core import calculate\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "fixture")
    return tmp_path


def test_index_find_and_impact_are_portable(indexed_repository: Path) -> None:
    index = build_repository_index(indexed_repository)

    matches = find_symbols(index, "calculate")
    assert [item.qualified_name for item in matches] == ["sample.core.calculate"]
    assert any(module.path == "scripts/sample-cli" for module in index.modules)

    report = build_impact_report(indexed_repository, index, "calculate")
    by_path = {item.path: item for item in report.nodes}
    assert by_path["src/sample/core.py"].role is ImpactRole.FOCUS
    assert by_path["src/sample/helpers.py"].role is ImpactRole.DEPENDENCY
    assert by_path["tests/test_core.py"].role is ImpactRole.TEST
    assert by_path["docs/guide.md"].role is ImpactRole.DOCUMENTATION

    output = indexed_repository.parent / "index.json"
    write_json(index, output)
    serialized = output.read_text(encoding="utf-8")
    assert str(indexed_repository) not in serialized
    assert json.loads(serialized)["schema_version"] == "1.0.0"


def test_changed_report_uses_explicit_base(indexed_repository: Path) -> None:
    path = indexed_repository / "src" / "sample" / "helpers.py"
    path.write_text(
        "def normalize(value: int) -> int:\n    return abs(value)\n",
        encoding="utf-8",
    )
    index = build_repository_index(indexed_repository)

    report = build_changed_report(
        indexed_repository,
        index,
        base="HEAD",
    )

    assert report.changed_files == ["src/sample/helpers.py"]
    by_path = {item.path: item for item in report.nodes}
    assert by_path["src/sample/helpers.py"].role is ImpactRole.FOCUS
    assert by_path["src/sample/core.py"].role is ImpactRole.IMPORTER


def test_non_python_file_can_be_a_visible_focus(indexed_repository: Path) -> None:
    (indexed_repository / "R").mkdir()
    r_source = indexed_repository / "R" / "model.R"
    r_source.write_text("fit_model <- function(x) x\n", encoding="utf-8")
    index = build_repository_index(indexed_repository)

    report = build_impact_report(indexed_repository, index, "R/model.R")

    assert report.focus == ["R/model.R"]
    assert report.nodes[0].role is ImpactRole.FOCUS
    assert any("R and other languages" in item for item in report.limitations)
