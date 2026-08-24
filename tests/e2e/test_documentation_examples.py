from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app
from anatomize.review import review_capabilities

pytestmark = pytest.mark.e2e


def test_readme_review_cli_workflow(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    source = root / "src" / "pkg"
    source.mkdir(parents=True)
    (source / "core.py").write_text(
        "class RepositoryIndex:\n    pass\n",
        encoding="utf-8",
    )
    artifact = root / "session.json"
    dossier = root / "dossier.json"
    runner = CliRunner()

    started = runner.invoke(
        app,
        ["review", "start", str(root), "--format", "json", "--output", str(artifact)],
    )
    queried = runner.invoke(
        app,
        [
            "review", "dossier", str(artifact), "src/pkg/core.py", "--profile", "implementation",
            "--format", "json", "--output", str(dossier),
        ],
    )
    assert started.exit_code == 0, started.output
    assert queried.exit_code == 0, queried.output
    assert "src/pkg/core.py" in dossier.read_text(encoding="utf-8")


def test_public_documentation_matches_runtime_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    reference = (root / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
    concepts = (root / "docs" / "CONCEPTS.md").read_text(encoding="utf-8")
    quickstart = (root / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
    troubleshooting = (root / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    generated_capabilities = (root / "docs" / "generated" / "capabilities.md").read_text(encoding="utf-8")
    documentation = "\n".join((reference, concepts, quickstart, troubleshooting, generated_capabilities))
    capabilities = review_capabilities()

    for value in (
        *capabilities["operations"],
        *capabilities["profiles"],
        *capabilities["target_kinds"],
        *capabilities["schemas"],
        *capabilities["schemas"].values(),
    ):
        assert str(value) in documentation

    public_namespaces = (
        "anatomize.index",
        "anatomize.evidence",
        "anatomize.identity",
        "anatomize.providers",
        "anatomize.sessions",
        "anatomize.dossiers",
        "anatomize.temporal",
        "anatomize.lifecycle",
        "anatomize.semantic",
        "anatomize.diagnostics",
        "anatomize.research",
        "anatomize.review",
    )
    for namespace in public_namespaces:
        assert namespace in reference

    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    for command in ("review", "mcp"):
        assert command in help_result.output
        assert f"`{command}`" in reference

    review_help = runner.invoke(app, ["review", "--help"])
    assert review_help.exit_code == 0
    for command in (
        "capabilities",
        "start",
        "dossier",
        "expand",
        "similarity",
        "change",
        "consolidate",
        "overlay-create",
        "overlay-check",
        "intent",
        "verify",
        "export",
        "check",
        "recover",
    ):
        assert command in review_help.output


def test_reader_path_links_resolve_and_exclude_internal_narration() -> None:
    root = Path(__file__).resolve().parents[2]
    public_pages = [
        root / "README.md",
        root / "docs" / "index.md",
        root / "docs" / "QUICKSTART.md",
        root / "docs" / "WORKFLOWS.md",
        root / "docs" / "ARTIFACTS.md",
        root / "docs" / "CONCEPTS.md",
        root / "docs" / "PROVIDERS.md",
        root / "docs" / "AUTOMATION.md",
        root / "docs" / "REFERENCE.md",
        root / "docs" / "MAINTAINERS.md",
        root / "docs" / "TROUBLESHOOTING.md",
    ]
    link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")

    for page in public_pages:
        content = page.read_text(encoding="utf-8")
        assert "tickets/" not in content
        assert "T-035" not in content
        for target in link_pattern.findall(content):
            if "://" in target or target.startswith("#"):
                continue
            linked = (page.parent / target.split("#", 1)[0]).resolve()
            assert linked.exists(), f"{page.relative_to(root)} links to missing {target}"
