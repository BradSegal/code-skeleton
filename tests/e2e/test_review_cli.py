from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from anatomize._artifacts import sha256_digest
from anatomize.cli import app
from anatomize.dossiers import DossierContext
from anatomize.evidence import DiagnosticEntity, EvidenceStrength
from anatomize.lifecycle import (
    CandidateGranularity,
    CandidateRegion,
    SimilarityArtifact,
    SimilarityMethod,
    SimilarityQuery,
    build_similarity_candidate,
    canonical_similarity_bytes,
)
from anatomize.review import DossierExchange, parse_review_artifact
from anatomize.sessions import load_session_bundle

pytestmark = pytest.mark.e2e


def _repository(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "core.py").write_text(
        "def calculate(values: list[int]) -> int:\n    return sum(values)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_core.py").write_text(
        "from pkg.core import calculate\n\ndef test_calculate() -> None:\n    assert calculate([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Review fixture\n\n`calculate` returns a total.\n", encoding="utf-8")
    return root


def _invoke(runner: CliRunner, arguments: list[str], expected: int = 0) -> Result:
    result = runner.invoke(app, arguments)
    assert result.exit_code == expected, result.output
    return result


def _similarity(session_path: Path, destination: Path) -> str:
    bundle = load_session_bundle(session_path)
    state_id = bundle.manifest.source_states[0].source_state.state_id
    candidate = build_similarity_candidate(
        source_state_id=state_id,
        granularity=CandidateGranularity.DEFINITION,
        method=SimilarityMethod.NORMALIZED,
        method_version="fixture:1",
        configuration_digest=sha256_digest(b"review-e2e"),
        members=[
            CandidateRegion(
                source_state_id=state_id,
                path="src/pkg/core.py",
                start_line=1,
                end_line=2,
                entity_id="python:pkg.core.calculate@src/pkg/core.py",
                content_digest=sha256_digest(b"implementation"),
            ),
            CandidateRegion(
                source_state_id=state_id,
                path="tests/test_core.py",
                start_line=3,
                end_line=4,
                entity_id="python:tests.test_core.test_calculate@tests/test_core.py",
                content_digest=sha256_digest(b"test"),
            ),
        ],
        strength=EvidenceStrength.CONSERVATIVE,
        limitations=["Candidate similarity does not establish interchangeable intent."],
        rationale="Fixture candidate for a complete review journey.",
    )
    artifact = SimilarityArtifact(
        repository_id=bundle.manifest.repository_id,
        source_state_id=state_id,
        query=SimilarityQuery(minimum_lines=1, minimum_tokens=1),
        candidates=[candidate],
    )
    destination.write_bytes(canonical_similarity_bytes(artifact))
    return candidate.candidate_id


def test_novice_expert_automation_and_lifecycle_cli_journeys(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    runner = CliRunner()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    store = tmp_path / "store"
    orientation = tmp_path / "orientation.json"
    implementation = tmp_path / "implementation.json"
    expanded = tmp_path / "expanded.json"

    help_result = _invoke(runner, ["review", "--help"])
    for command in (
        "state",
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
        assert command in help_result.output

    state = json.loads(
        _invoke(runner, ["review", "state", str(root), "--repository-id", "repository:e2e"]).output
    )
    assert state["repository_id"] == "repository:e2e"
    assert state["state_id"].startswith("state:")

    _invoke(
        runner,
        [
            "review",
            "start",
            str(root),
            "--repository-id",
            "repository:e2e",
            "--format",
            "json",
            "--output",
            str(before),
            "--store",
            str(store),
        ],
    )
    _invoke(
        runner,
        [
            "review",
            "dossier",
            str(before),
            "--profile",
            "orientation",
            "--format",
            "json",
            "--output",
            str(orientation),
        ],
    )
    parsed = parse_review_artifact(orientation.read_bytes())
    assert isinstance(parsed, DossierExchange)
    assert parsed.dossier.expansions

    repository_audit = _invoke(
        runner,
        ["review", "dossier", str(before), "--profile", "audit", "--format", "json"],
    )
    audit_payload = json.loads(repository_audit.output)
    assert audit_payload["dossier"]["boundary"]["targets"][0]["status"] == "exact"

    missing_target = _invoke(
        runner,
        ["review", "dossier", str(before), "--profile", "implementation"],
        expected=2,
    )
    assert "error[dossier_target_required]" in missing_target.output
    action_id = parsed.dossier.expansions[0].action_id
    _invoke(
        runner,
        [
            "review",
            "expand",
            str(before),
            str(orientation),
            action_id,
            "--format",
            "json",
            "--output",
            str(expanded),
        ],
    )
    checked = _invoke(runner, ["review", "check", str(expanded), "--format", "json"])
    assert json.loads(checked.output)["valid"] is True

    projected_similarity = tmp_path / "projected-similarity.json"
    _invoke(
        runner,
        ["review", "similarity", str(before), "--format", "json", "--output", str(projected_similarity)],
    )
    assert json.loads(projected_similarity.read_text(encoding="utf-8"))["artifact_type"] == "anatomize.similarity"
    checked_similarity = _invoke(runner, ["review", "check", str(projected_similarity), "--format", "json"])
    assert json.loads(checked_similarity.output)["valid"] is True

    expert = _invoke(
        runner,
        [
            "review",
            "dossier",
            str(before),
            "pkg.core.calculate",
            "--profile",
            "localisation",
            "--target-kind",
            "symbol",
            "--role",
            "definition",
            "--width",
            "40",
        ],
    )
    assert "src/pkg/core.py:1:0" in expert.output
    assert "\x1b" not in expert.output
    assert "Omissions and unknowns" in expert.output

    _invoke(
        runner,
        [
            "review",
            "dossier",
            str(before),
            "src/pkg/core.py",
            "--profile",
            "implementation",
            "--target-kind",
            "file",
            "--format",
            "json",
            "--output",
            str(implementation),
        ],
    )
    similarity = tmp_path / "similarity.json"
    candidate_id = _similarity(before, similarity)
    consolidation = tmp_path / "consolidation.json"
    _invoke(
        runner,
        [
            "review",
            "consolidate",
            str(orientation),
            str(similarity),
            candidate_id,
            "--format",
            "json",
            "--output",
            str(consolidation),
        ],
    )
    consolidation_payload = json.loads(consolidation.read_text(encoding="utf-8"))
    assert len(consolidation_payload["groups"]) == 12
    assert consolidation_payload["disposition_authority"] == "consumer_overlay_only"

    overlay = tmp_path / "overlay.json"
    _invoke(
        runner,
        [
            "review",
            "overlay-create",
            str(before),
            str(similarity),
            candidate_id,
            "--disposition",
            "keep",
            "--rationale",
            "Implementation and verification have distinct responsibilities.",
            "--owner",
            "review:e2e",
            "--output",
            str(overlay),
        ],
    )
    current = _invoke(
        runner,
        ["review", "overlay-check", str(overlay), str(before), str(similarity), candidate_id, "--format", "json"],
    )
    assert json.loads(current.output)["current"] is True

    obligations = tmp_path / "obligations.json"
    obligations.write_text(
        json.dumps(
            {
                "obligations": [
                    {
                        "kind": "test",
                        "subject_ref": "tests/test_core.py",
                        "expectation": "The selected test remains passing.",
                        "required_evidence_kinds": ["test_runtime"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    intent = tmp_path / "intent.json"
    decision_id = json.loads(overlay.read_text(encoding="utf-8"))["decision_id"]
    _invoke(
        runner,
        [
            "review",
            "intent",
            str(before),
            str(implementation),
            str(obligations),
            "--decision-overlay",
            decision_id,
            "--output",
            str(intent),
        ],
    )

    (root / "src" / "pkg" / "core.py").write_text(
        "def calculate(values: list[int]) -> int:\n    return sum(value for value in values)\n",
        encoding="utf-8",
    )
    _invoke(
        runner,
        [
            "review",
            "start",
            str(root),
            "--repository-id",
            "repository:e2e",
            "--format",
            "json",
            "--output",
            str(after),
        ],
    )
    changed = _invoke(runner, ["review", "change", str(before), str(after), "--format", "json"])
    change_payload = json.loads(changed.output)
    assert change_payload["changes"]
    assert "file:src/pkg/core.py" in change_payload["affected_evidence_refs"]

    intent_payload = json.loads(intent.read_text(encoding="utf-8"))
    observations = tmp_path / "observations.json"
    observations.write_text(
        json.dumps(
            {
                "observations": [
                    {
                        "obligation_id": intent_payload["obligations"][0]["obligation_id"],
                        "status": "satisfied",
                        "evidence_refs": ["test-runtime:selected"],
                        "observed": "The selected test passed.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    closure = _invoke(
        runner,
        ["review", "verify", str(intent), str(after), str(observations), "--format", "json"],
        expected=3,
    )
    closure_payload = json.loads(closure.output)
    assert closure_payload["outcome"] == "incomplete"
    assert closure_payload["observations"][0]["status"] == "unresolved"

    markdown = tmp_path / "review.md"
    _invoke(
        runner,
        ["review", "export", str(orientation), "--format", "markdown", "--output", str(markdown)],
    )
    rendered = markdown.read_text(encoding="utf-8")
    assert "## Evidence" in rendered
    assert "## Expansion actions" in rendered
    assert parsed.dossier.dossier_id in rendered

    recovered = tmp_path / "recovered.json"
    _invoke(
        runner,
        ["review", "recover", str(store), "--format", "json", "--output", str(recovered)],
    )
    assert load_session_bundle(recovered).manifest.session_id == load_session_bundle(before).manifest.session_id
    assert recovered.read_bytes() == before.read_bytes()

    recovered_markdown = tmp_path / "recovered.md"
    _invoke(
        runner,
        ["review", "recover", str(store), "--format", "markdown", "--output", str(recovered_markdown)],
    )
    assert "# anatomize.session-bundle" in recovered_markdown.read_text(encoding="utf-8")


def test_start_normalizes_captured_sarif_through_the_public_cli(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    source = root / "src" / "pkg" / "core.py"
    sarif = tmp_path / "ruff.sarif"
    sarif.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "ruff", "version": "0.14", "rules": []}},
                        "results": [
                            {
                                "ruleId": "F401",
                                "level": "warning",
                                "message": {"text": "Imported name is unused"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": source.resolve().as_uri()},
                                            "region": {"startLine": 1, "startColumn": 1},
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    session = tmp_path / "sarif-session.json"
    result = CliRunner().invoke(
        app,
        [
            "review",
            "start",
            str(root),
            "--artifact",
            f"sarif@0.14={sarif}",
            "--format",
            "json",
            "--output",
            str(session),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = session.read_text(encoding="utf-8")
    evidence = DossierContext.from_bundle(load_session_bundle(session)).evidence[0]
    assert any(item.provider_id == "sarif:ruff" for item in evidence.provider_runs)
    assert any(isinstance(item, DiagnosticEntity) and item.rule_id == "F401" for item in evidence.entities)
    assert str(root.resolve()) not in payload


def test_review_cli_failure_is_stable_and_actionable(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text('{"artifact_type":"anatomize.dossier-exchange"}', encoding="utf-8")
    result = CliRunner().invoke(app, ["review", "check", str(corrupt)])

    assert result.exit_code == 2
    assert "error[review_artifact_invalid]" in result.output
    assert "remediation:" in result.output
    assert "Traceback" not in result.output
