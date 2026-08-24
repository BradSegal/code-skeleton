from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from anatomize.review import review_capabilities

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
PUBLIC_NAMESPACES = (
    "review",
    "index",
    "evidence",
    "identity",
    "providers",
    "sessions",
    "dossiers",
    "temporal",
    "lifecycle",
    "semantic",
    "diagnostics",
    "research",
)


def test_generated_documentation_is_current_and_complete() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/generate_documentation.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    generated = ROOT / "docs" / "generated"
    capabilities_page = (generated / "capabilities.md").read_text(encoding="utf-8")
    capabilities = review_capabilities()
    for value in (
        *capabilities["operations"],
        *capabilities["profiles"],
        *capabilities["target_kinds"],
        *capabilities["schemas"],
        *capabilities["schemas"].values(),
    ):
        assert f"`{value}`" in capabilities_page
    for schema_name in capabilities["schemas"]:
        schema_path = generated / "schemas" / f"{schema_name}.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False

    api_page = (generated / "python-api.md").read_text(encoding="utf-8")
    assert "&amp;#" not in api_page
    assert "str(object='')" not in api_page
    assert "Value: `" in api_page
    assert "`() ->" in api_page
    for namespace in PUBLIC_NAMESPACES:
        module = importlib.import_module(f"anatomize.{namespace}")
        assert f"`anatomize.{namespace}`" in api_page
        for export in module.__all__:
            assert f"`{export}`" in api_page


def test_site_navigation_and_pages_workflow_are_bounded() -> None:
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    nav_paths = set(re.findall(r":\s+([A-Za-z0-9_./-]+\.md)\s*$", config, flags=re.MULTILINE))
    markdown_paths = {
        path.relative_to(ROOT / "docs").as_posix()
        for path in (ROOT / "docs").rglob("*.md")
    }
    assert markdown_paths == nav_paths
    assert "strict: true" in config
    assert "font: false" in config
    assert "https://bradsegal.github.io/anatomize/" in config

    workflow = (ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "actions/upload-pages-artifact@v5" in workflow
    assert "actions/deploy-pages@v5" in workflow
    assert "enablement: false" in workflow
    assert "tickets/" not in workflow
    assert "secrets." not in workflow

    for public_source in (ROOT / "docs").rglob("*"):
        assert not public_source.is_symlink()
        assert "tickets" not in public_source.parts
