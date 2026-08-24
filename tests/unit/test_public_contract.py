from __future__ import annotations

import ast
from pathlib import Path

import tomli

from anatomize.review import ReviewApplication
from anatomize.version import __version__


def test_public_application_contract_is_importable() -> None:
    assert __version__
    assert ReviewApplication().capabilities()["application"] == "anatomize.review"


def test_citation_version_matches_package_version() -> None:
    root = Path(__file__).resolve().parents[2]
    citation = (root / "CITATION.cff").read_text(encoding="utf-8")
    assert f"version: {__version__}" in citation


def test_package_has_no_writing_tools_runtime_dependency() -> None:
    root = Path(__file__).parents[2]
    metadata = tomli.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = metadata["project"]["dependencies"]
    assert not any("writing-tools" in item.casefold() for item in dependencies)

    imported_roots: set[str] = set()
    for path in (root / "src" / "anatomize").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
    assert "writing_tools" not in imported_roots


def test_mcp_sdk_is_an_optional_transport_dependency() -> None:
    root = Path(__file__).parents[2]
    metadata = tomli.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert not any(item.casefold().startswith("mcp") for item in metadata["project"]["dependencies"])
    assert metadata["project"]["optional-dependencies"]["mcp"] == ["mcp>=2,<3"]
