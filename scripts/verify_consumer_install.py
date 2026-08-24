#!/usr/bin/env python3
"""Build, inspect, install, and dogfood both Anatomize distribution formats."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from _verification import create_venv, venv_path


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Command failed ({' '.join(command)}): {detail}")
    return completed


def _members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, mode="r:gz") as archive:
        return archive.getnames()


def _member_bytes(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]
    values = []
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                values.append((member.name, extracted.read()))
    return values


def _metadata(path: Path) -> str:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            name = next(item for item in archive.namelist() if item.endswith(".dist-info/METADATA"))
            return archive.read(name).decode("utf-8")
    with tarfile.open(path, mode="r:gz") as archive:
        member = next(item for item in archive.getmembers() if item.name.endswith("/PKG-INFO"))
        extracted = archive.extractfile(member)
        if extracted is None:
            raise ValueError(f"Could not read metadata from {path}")
        return extracted.read().decode("utf-8")


def _wheel_from_sdist(source: Path, workspace: Path) -> Path:
    extracted = workspace / "source-from-sdist"
    extracted.mkdir()
    with tarfile.open(source, mode="r:gz") as archive:
        archive.extractall(extracted, filter="data")
    roots = [item for item in extracted.iterdir() if item.is_dir()]
    if len(roots) != 1:
        raise ValueError(f"Expected one source root in {source.name}")
    wheels = workspace / "wheel-from-sdist"
    wheels.mkdir()
    _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels), str(roots[0])], cwd=workspace)
    built = list(wheels.glob("*.whl"))
    if len(built) != 1:
        raise ValueError(f"Expected one wheel rebuilt from {source.name}")
    return built[0]


def _probe(path: Path, *, workspace: Path, label: str) -> dict[str, object]:
    environment = workspace / f"environment-{label}"
    create_venv(environment, system_site_packages=True)
    python = venv_path(environment, "python")
    executable = venv_path(environment, "anatomize")
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--force-reinstall",
            "--no-build-isolation",
            "--no-deps",
            str(path),
        ],
        cwd=workspace,
        env=env,
    )

    imported = _run(
        [
            str(python),
            "-c",
            "from anatomize import __version__; "
            "from anatomize.diagnostics import SarifLog; "
            "from anatomize.dossiers import DossierEngine; "
            "from anatomize.evidence import RepositoryEvidence; "
            "from anatomize.identity import SourceCoordinateMap; "
            "from anatomize.index import RepositoryIndex, build_repository_index; "
            "from anatomize.lifecycle import ChangeDossier; "
            "from anatomize.providers import ProviderEnvelope; "
            "from anatomize.research import ResearchGraphArtifact; "
            "from anatomize.review import ReviewApplication; "
            "from anatomize.semantic import LspSemanticArtifact; "
            "from anatomize.sessions import ReviewSessionBundle; "
            "from anatomize.temporal import RepositoryComparison; "
            "print(__version__); "
            "print(' '.join(item.__name__ for item in "
            "(RepositoryIndex, RepositoryEvidence, SourceCoordinateMap, ProviderEnvelope, "
            "ReviewSessionBundle, DossierEngine, RepositoryComparison, ChangeDossier, "
            "LspSemanticArtifact, SarifLog, ResearchGraphArtifact, ReviewApplication, "
            "build_repository_index)))",
        ],
        cwd=workspace,
        env=env,
    ).stdout.splitlines()

    help_text = _run([str(executable), "--help"], cwd=workspace, env=env).stdout
    for current in ("review", "mcp"):
        if current not in help_text:
            raise ValueError(f"Installed CLI omits {current}")
    for retired in ("pack", "index", "generate", "init"):
        if f"  {retired} " in help_text:
            raise ValueError(f"Installed CLI still exposes retired command {retired}")

    capabilities = json.loads(
        _run([str(executable), "review", "capabilities", "--format", "json"], cwd=workspace, env=env).stdout
    )
    repository = workspace / f"repository-{label}"
    source = repository / "src" / "probe"
    source.mkdir(parents=True)
    (source / "core.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    (repository / "README.md").write_text("# Probe\n\nUse `answer`.\n", encoding="utf-8")
    session = workspace / f"session-{label}.json"
    dossier = workspace / f"dossier-{label}.json"
    _run(
        [
            str(executable),
            "review",
            "start",
            str(repository),
            "--repository-id",
            "repository:consumer-probe",
            "--format",
            "json",
            "--output",
            str(session),
        ],
        cwd=workspace,
        env=env,
    )
    _run(
        [
            str(executable),
            "review",
            "dossier",
            str(session),
            "src/probe/core.py",
            "--profile",
            "implementation",
            "--target-kind",
            "file",
            "--format",
            "json",
            "--output",
            str(dossier),
        ],
        cwd=workspace,
        env=env,
    )
    dossier_payload = json.loads(dossier.read_text(encoding="utf-8"))
    if dossier_payload["dossier"]["repository_id"] != "repository:consumer-probe":
        raise ValueError("Installed dossier lost repository identity")

    return {
        "installed_from": label,
        "artifact": path.name,
        "version": imported[0],
        "public_imports": imported[1],
        "application_api_version": capabilities["application_api_version"],
        "operation_count": len(capabilities["operations"]),
        "dossier_status": dossier_payload["dossier"]["status"],
    }


def verify(root: Path) -> dict[str, object]:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="anatomize-consumer-install-") as temporary_name:
        workspace = Path(temporary_name)
        distributions = workspace / "dist"
        _run([sys.executable, "-m", "build", "--outdir", str(distributions), str(root)], cwd=workspace)
        artifacts = sorted(distributions.iterdir())
        wheels = [item for item in artifacts if item.suffix == ".whl"]
        sdists = [item for item in artifacts if item.name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError("Expected exactly one wheel and one source distribution")

        _run([sys.executable, "-m", "twine", "check", "--strict", *map(str, artifacts)], cwd=workspace)
        inspected = []
        private_home_marker = b"/home/" + b"bradl/"
        private_ticket_marker = b"tickets/" + b"anatomize-agentic-lifecycle"
        for artifact in artifacts:
            members = _members(artifact)
            is_sdist = artifact.name.endswith(".tar.gz")
            carries_site_sources = any(name.endswith("/mkdocs.yml") or name == "mkdocs.yml" for name in members)
            if carries_site_sources is not is_sdist:
                raise ValueError(
                    f"Documentation site sources have the wrong package boundary in {artifact.name}"
                )
            if is_sdist:
                required_documentation = (
                    "/docs/index.md",
                    "/docs/generated/capabilities.md",
                    "/docs/generated/python-api.md",
                    "/docs/generated/schemas/session.schema.json",
                    "/scripts/generate_documentation.py",
                    "/scripts/verify_documentation_site.py",
                )
                absent = [
                    expected
                    for expected in required_documentation
                    if not any(f"/{name}".endswith(expected) for name in members)
                ]
                if absent:
                    raise ValueError(f"Source distribution omits documentation release inputs: {absent}")
            forbidden = [
                name
                for name in members
                if "/tickets/" in f"/{name}"
                or name.startswith("tickets/")
                or "__pycache__" in name
                or name.endswith((".pyc", ".pyo"))
            ]
            if forbidden:
                raise ValueError(f"Private or generated files leaked into {artifact.name}: {forbidden[:3]}")
            private_paths = [
                name
                for name, content in _member_bytes(artifact)
                if private_home_marker in content or private_ticket_marker in content
            ]
            if private_paths:
                raise ValueError(f"Local paths or ticket narration leaked into {artifact.name}: {private_paths[:3]}")
            metadata = _metadata(artifact)
            dependencies = [
                line.removeprefix("Requires-Dist:").strip()
                for line in metadata.splitlines()
                if line.startswith("Requires-Dist:")
            ]
            if any("writing-tools" in item.casefold() for item in dependencies):
                raise ValueError(f"writing-tools leaked into dependencies for {artifact.name}")
            inspected.append(
                {
                    "artifact": artifact.name,
                    "member_count": len(members),
                    "runtime_dependencies": [item for item in dependencies if "extra ==" not in item],
                    "documentation_site_sources": carries_site_sources,
                }
            )

        rebuilt = _wheel_from_sdist(sdists[0], workspace)
        probes = [
            _probe(wheels[0], workspace=workspace, label="wheel"),
            _probe(rebuilt, workspace=workspace, label="sdist"),
        ]
        return {
            "schema_version": "1.0.0",
            "passed": True,
            "inspected": inspected,
            "probes": probes,
            "foreign_working_directory": True,
            "tickets_excluded": True,
            "writing_tools_dependency_absent": True,
            "documentation_site_boundary": "sdist-only",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.root)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
