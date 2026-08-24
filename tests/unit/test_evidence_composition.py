from __future__ import annotations

from pathlib import Path

import pytest

from anatomize.evidence import merge_repository_evidence
from anatomize.index import build_repository_index
from anatomize.providers import repository_index_evidence
from tests.unit.test_evidence_models import _known_truth_evidence


def test_repository_index_bridge_preserves_source_entities_edges_candidates_and_omissions(tmp_path: Path) -> None:
    (tmp_path / "owner.py").write_text(
        "def normalise(value: str) -> str:\n"
        "    stripped = value.strip()\n"
        "    folded = stripped.casefold()\n"
        "    return folded.replace(' ', '-')\n"
    )
    (tmp_path / "legacy.py").write_text(
        "def canonicalise(value: str) -> str:\n"
        "    stripped = value.strip()\n"
        "    folded = stripped.casefold()\n"
        "    return folded.replace(' ', '-')\n"
    )
    (tmp_path / "test_owner.py").write_text(
        "from owner import normalise\n\ndef test_normalise():\n    assert normalise(' A ') == 'a'\n"
    )
    index = build_repository_index(tmp_path)
    evidence = repository_index_evidence(index, repository_id="repository:bridge-fixture")

    assert evidence.states[0].state_id == f"state:{index.source_state.fact_digest}"
    assert {item.entity_type.value for item in evidence.entities}.issuperset({"repository", "file", "symbol"})
    assert evidence.edges
    assert evidence.candidates[0].kind.value == "duplication"
    assert evidence.completeness[0].omission_ids
    assert evidence.provider_runs[0].provider_id == "anatomize.repository-index"


def test_evidence_merge_deduplicates_identical_records_and_rejects_conflicting_identity() -> None:
    evidence = _known_truth_evidence()
    merged = merge_repository_evidence([evidence, evidence])
    assert len(merged.states) == len(evidence.states)
    assert len(merged.entities) == len(evidence.entities)

    changed_state = evidence.states[0].model_copy(update={"content_digest": "conflicting-digest"})
    conflicting = evidence.model_copy(update={"states": [changed_state, *evidence.states[1:]]})
    with pytest.raises(ValueError, match="conflicting record identity"):
        merge_repository_evidence([evidence, conflicting])

    another_repository = evidence.model_copy(update={"repository_id": "repository:other"})
    with pytest.raises(ValueError, match="cannot cross repository identities"):
        merge_repository_evidence([evidence, another_repository])


def test_evidence_merge_materializes_cross_provider_structural_conflicts() -> None:
    evidence = _known_truth_evidence()
    ast = evidence.model_copy(
        update={
            "observations": [evidence.observations[0]],
            "candidates": [],
            "conflicts": [],
        }
    )
    semantic = evidence.model_copy(
        update={
            "observations": [evidence.observations[1]],
            "candidates": [],
            "conflicts": [],
        }
    )

    merged = merge_repository_evidence([ast, semantic])

    assert len(merged.conflicts) == 1
    conflict = merged.conflicts[0]
    assert conflict.target_type == "edge"
    assert conflict.target_id == "edge:defines"
    assert conflict.observation_ids == [
        "observation:ast-defines",
        "observation:semantic-conflict",
    ]


def test_evidence_merge_keeps_declared_conflict_as_target_authority() -> None:
    evidence = _known_truth_evidence()

    merged = merge_repository_evidence([evidence])

    assert merged.conflicts == evidence.conflicts
