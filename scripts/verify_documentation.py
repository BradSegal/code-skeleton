#!/usr/bin/env python3
"""Execute the public review documentation against a freshly built wheel."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _verification import create_venv, venv_path


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expected_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, check=False, capture_output=True, text=True)
    if completed.returncode != expected_exit:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(
            f"Command exited {completed.returncode}, expected {expected_exit} "
            f"({' '.join(command)}): {detail}"
        )
    return completed


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return parsed


def verify(root: Path) -> dict[str, Any]:
    """Build one wheel and execute the documented public journey."""
    root = root.resolve()
    fixture_root = root / "tests" / "fixtures"
    with tempfile.TemporaryDirectory(prefix="anatomize-documentation-") as temporary_name:
        workspace = Path(temporary_name)
        foreign = workspace / "foreign"
        foreign.mkdir()
        distributions = workspace / "dist"
        distributions.mkdir()
        build_env = dict(os.environ)
        _run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(distributions), str(root)],
            cwd=foreign,
            env=build_env,
        )
        wheels = list(distributions.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError(f"Expected one wheel, found {len(wheels)}")

        environment = workspace / "environment"
        create_venv(environment, system_site_packages=True)
        python = venv_path(environment, "python")
        anatomize = venv_path(environment, "anatomize")
        isolated_env = dict(os.environ)
        isolated_env["PYTHONNOUSERSITE"] = "1"
        isolated_env["NO_COLOR"] = "1"
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
                str(wheels[0]),
            ],
            cwd=foreign,
            env=isolated_env,
        )

        repository = foreign / "repository"
        shutil.copytree(fixture_root / "agentic_workflow", repository)
        artifacts = foreign / "review"
        artifacts.mkdir()
        command = [str(anatomize), "review"]

        capabilities = json.loads(
            _run([*command, "capabilities", "--format", "json"], cwd=foreign, env=isolated_env).stdout
        )
        required_operations = {
            "capabilities",
            "source_state",
            "start",
            "dossier",
            "expand",
            "similarity",
            "change",
            "consolidation",
            "decision_overlay",
            "evaluate_overlay",
            "implementation_intent",
            "verify",
            "check",
            "export",
            "recover",
        }
        missing = required_operations - set(capabilities["operations"])
        if missing:
            raise ValueError(f"Review capabilities omit documented operations: {sorted(missing)}")

        before = artifacts / "before.json"
        store = artifacts / "store"
        source_state = json.loads(
            _run(
                [
                    *command,
                    "state",
                    str(repository),
                    "--repository-id",
                    "repository:documentation",
                    "--format",
                    "json",
                ],
                cwd=foreign,
                env=isolated_env,
            ).stdout
        )
        _run(
            [
                *command,
                "start",
                str(repository),
                "--repository-id",
                "repository:documentation",
                "--format",
                "json",
                "--output",
                str(before),
                "--store",
                str(store),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        session_state = _artifact(before)["manifest"]["source_states"][0]["source_state"]
        if source_state != session_state:
            raise ValueError("Lightweight source state differs from the documented review session")
        orientation = artifacts / "orientation.json"
        _run(
            [
                *command,
                "dossier",
                str(before),
                "--profile",
                "orientation",
                "--format",
                "json",
                "--output",
                str(orientation),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        implementation = artifacts / "implementation.json"
        _run(
            [
                *command,
                "dossier",
                str(before),
                "src/left.py",
                "--profile",
                "implementation",
                "--target-kind",
                "file",
                "--question",
                "What must remain true while simplifying this module?",
                "--format",
                "json",
                "--output",
                str(implementation),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        exchange = _artifact(orientation)
        expansions = exchange["dossier"]["expansions"]
        if not expansions:
            raise ValueError("Documented orientation journey exposed no expansion")
        expanded = artifacts / "expanded.json"
        _run(
            [
                *command,
                "expand",
                str(before),
                str(orientation),
                expansions[0]["action_id"],
                "--format",
                "json",
                "--output",
                str(expanded),
            ],
            cwd=foreign,
            env=isolated_env,
        )

        similarity = artifacts / "similarity.json"
        _run(
            [
                *command,
                "similarity",
                str(before),
                "--format",
                "json",
                "--output",
                str(similarity),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        candidates = _artifact(similarity)["candidates"]
        if len(candidates) != 1:
            raise ValueError(f"Documentation fixture expected one similarity candidate, found {len(candidates)}")
        candidate_id = candidates[0]["candidate_id"]
        consolidation = artifacts / "consolidation.json"
        _run(
            [
                *command,
                "consolidate",
                str(implementation),
                str(similarity),
                candidate_id,
                "--format",
                "json",
                "--output",
                str(consolidation),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        decision = artifacts / "decision.json"
        _run(
            [
                *command,
                "overlay-create",
                str(before),
                str(similarity),
                candidate_id,
                "--disposition",
                "keep",
                "--rationale",
                "The example records a human decision before source changes.",
                "--owner",
                "documentation-verifier",
                "--format",
                "json",
                "--output",
                str(decision),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        current_evaluation = json.loads(
            _run(
                [
                    *command,
                    "overlay-check",
                    str(decision),
                    str(before),
                    str(similarity),
                    candidate_id,
                    "--format",
                    "json",
                ],
                cwd=foreign,
                env=isolated_env,
            ).stdout
        )
        if current_evaluation["current"] is not True:
            raise ValueError("Fresh documented decision was unexpectedly stale")

        obligations = artifacts / "obligations.json"
        _write_json(
            obligations,
            {
                "obligations": [
                    {
                        "kind": "owner",
                        "subject_ref": "src/left.py",
                        "expectation": "The implementation remains owned by the reviewed source file.",
                        "required_evidence_kinds": ["source_state"],
                    }
                ],
                "declared_unknowns": [],
            },
        )
        intent = artifacts / "intent.json"
        _run(
            [
                *command,
                "intent",
                str(before),
                str(implementation),
                str(obligations),
                "--decision-overlay",
                _artifact(decision)["decision_id"],
                "--format",
                "json",
                "--output",
                str(intent),
            ],
            cwd=foreign,
            env=isolated_env,
        )

        for changed_source in (repository / "src" / "left.py", repository / "src" / "right.py"):
            changed_source.write_text(
                changed_source.read_text(encoding="utf-8").replace(
                    "    return [value + total for value in ordered]\n",
                    "    result = [value + total for value in ordered]\n    return result\n",
                ),
                encoding="utf-8",
            )
        after = artifacts / "after.json"
        _run(
            [
                *command,
                "start",
                str(repository),
                "--repository-id",
                "repository:documentation",
                "--format",
                "json",
                "--output",
                str(after),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        change = artifacts / "change.json"
        _run(
            [
                *command,
                "change",
                str(before),
                str(after),
                "--format",
                "json",
                "--output",
                str(change),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        after_similarity = artifacts / "after-similarity.json"
        _run(
            [
                *command,
                "similarity",
                str(after),
                "--format",
                "json",
                "--output",
                str(after_similarity),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        after_candidates = _artifact(after_similarity)["candidates"]
        if len(after_candidates) != 1:
            raise ValueError("After-state similarity no longer exercises stale-decision documentation")
        stale_evaluation = json.loads(
            _run(
                [
                    *command,
                    "overlay-check",
                    str(decision),
                    str(after),
                    str(after_similarity),
                    after_candidates[0]["candidate_id"],
                    "--format",
                    "json",
                ],
                cwd=foreign,
                env=isolated_env,
                expected_exit=4,
            ).stdout
        )
        if stale_evaluation["current"] is not False:
            raise ValueError("Changed source did not make the documented decision stale")

        intent_payload = _artifact(intent)
        after_payload = _artifact(after)
        after_source_state_id = after_payload["manifest"]["source_states"][0]["source_state"]["state_id"]
        observations = artifacts / "observations.json"
        _write_json(
            observations,
            {
                "observations": [
                    {
                        "obligation_id": intent_payload["obligations"][0]["obligation_id"],
                        "source_state_id": after_source_state_id,
                        "status": "satisfied",
                        "evidence_refs": [after_source_state_id],
                        "observed": "The fresh after-session contains the reviewed owning source file.",
                    }
                ]
            },
        )
        closure = artifacts / "closure.json"
        _run(
            [
                *command,
                "verify",
                str(intent),
                str(after),
                str(observations),
                "--format",
                "json",
                "--output",
                str(closure),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        if _artifact(closure)["outcome"] != "closed":
            raise ValueError("Documented closure workflow did not close")

        exported = artifacts / "implementation.md"
        _run(
            [
                *command,
                "export",
                str(implementation),
                "--format",
                "markdown",
                "--output",
                str(exported),
            ],
            cwd=foreign,
            env=isolated_env,
        )
        if not exported.read_text(encoding="utf-8").startswith("# Implementation review\n"):
            raise ValueError("Markdown export lacks its human-readable heading")
        for artifact in (
            before,
            orientation,
            implementation,
            expanded,
            similarity,
            consolidation,
            decision,
            intent,
            after,
            change,
            closure,
        ):
            _run([*command, "check", str(artifact), "--format", "json"], cwd=foreign, env=isolated_env)

        recovered = artifacts / "recovered.json"
        _run(
            [*command, "recover", str(store), "--format", "json", "--output", str(recovered)],
            cwd=foreign,
            env=isolated_env,
        )
        if recovered.read_bytes() != before.read_bytes():
            raise ValueError("Documented store recovery did not reproduce the portable bundle")

        failures: dict[str, int] = {"stale_decision": 4}
        invalid_inputs = {
            "old": fixture_root / "dossiers" / "dossier-legacy-v0.json",
            "incompatible": fixture_root / "dossiers" / "dossier-future-v2.json",
        }
        corrupt = artifacts / "corrupt.json"
        corrupt.write_text("{", encoding="utf-8")
        invalid_inputs["corrupt"] = corrupt
        for label, path in invalid_inputs.items():
            completed = _run(
                [*command, "check", str(path), "--format", "json"],
                cwd=foreign,
                env=isolated_env,
                expected_exit=2,
            )
            if "error[" not in completed.stderr or "remediation:" not in completed.stderr:
                raise ValueError(f"{label} artifact failure was not actionable")
            failures[label] = completed.returncode
        missing_provider = _run(
            [
                *command,
                "start",
                str(repository),
                "--provider",
                str(artifacts / "missing-provider.json"),
                "--format",
                "json",
            ],
            cwd=foreign,
            env=isolated_env,
            expected_exit=2,
        )
        if "error[" not in missing_provider.stderr or "remediation:" not in missing_provider.stderr:
            raise ValueError("Missing-provider failure was not actionable")
        failures["missing_provider"] = missing_provider.returncode

        return {
            "schema_version": "1.0.0",
            "passed": True,
            "distribution": wheels[0].name,
            "foreign_working_directory": True,
            "operations_exercised": sorted(required_operations),
            "dossier_status": _artifact(implementation)["dossier"]["status"],
            "expansions_exercised": 1,
            "similarity_candidates": len(candidates),
            "consolidation_groups": len(_artifact(consolidation)["groups"]),
            "decision_current_before": current_evaluation["current"],
            "decision_current_after": stale_evaluation["current"],
            "change_records": len(_artifact(change)["changes"]),
            "closure_outcome": _artifact(closure)["outcome"],
            "markdown_exported": True,
            "recovery_byte_identical": True,
            "failure_cases": failures,
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
