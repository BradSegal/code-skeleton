from __future__ import annotations

import json
from pathlib import Path

import pytest

from anatomize._artifacts import sha256_digest
from anatomize.evidence import EvidenceStrength
from anatomize.lifecycle import (
    CandidateDeltaKind,
    CandidateGranularity,
    CandidateRegion,
    HunkKind,
    SimilarityArtifact,
    SimilarityArtifactError,
    SimilarityCandidate,
    SimilarityMethod,
    SimilarityQuery,
    build_aligned_hunk,
    build_similarity_candidate,
    canonical_similarity_bytes,
    compare_similarity_artifacts,
    jscpd_provider_envelope,
    parse_jscpd_report,
    parse_similarity_artifact,
    select_similarity_candidates,
)
from anatomize.providers import InvocationMode
from tests.unit.test_evidence_models import _known_truth_evidence


def _digest(value: str) -> str:
    return sha256_digest(value.encode("utf-8"))


def _candidate(
    state: str,
    granularity: CandidateGranularity,
    method: SimilarityMethod,
    *,
    suffix: str = "",
    second_path: str = "src/b.py",
    end_line: int = 10,
    second_digest: str = "same",
) -> SimilarityCandidate:
    members = [
        CandidateRegion(
            source_state_id=state,
            path="src/a.py",
            start_line=1,
            end_line=end_line,
            entity_id=f"entity:a{suffix}",
            role="implementation",
            language="python",
            content_digest=_digest("same"),
        ),
        CandidateRegion(
            source_state_id=state,
            path=second_path,
            start_line=2,
            end_line=end_line + 1,
            entity_id=f"entity:b{suffix}",
            role="implementation",
            language="python",
            content_digest=_digest(second_digest),
        ),
    ]
    hunk = build_aligned_hunk(
        kind=HunkKind.EQUAL,
        left_member=0,
        right_member=1,
        left_start_line=1,
        left_end_line=end_line,
        right_start_line=2,
        right_end_line=end_line + 1,
        left_digest=_digest("same"),
        right_digest=_digest(second_digest),
        preview="bounded preview",
    )
    return build_similarity_candidate(
        source_state_id=state,
        granularity=granularity,
        method=method,
        method_version="1.0",
        configuration_digest=_digest("configuration"),
        provider_run_id=None if method is not SimilarityMethod.EXTERNAL_NEAR_MATCH else "provider:jscpd",
        threshold=None if method is not SimilarityMethod.EXTERNAL_NEAR_MATCH else 0.8,
        token_count=100,
        members=members,
        aligned_hunks=[hunk],
        unmatched_member_regions=[],
        strength=EvidenceStrength.EXACT if method is SimilarityMethod.EXACT else EvidenceStrength.CONSERVATIVE,
        limitations=[] if method is not SimilarityMethod.EXTERNAL_NEAR_MATCH else ["Provider-native threshold."],
        rationale="Candidate evidence only; no consolidation verdict.",
    )


def test_candidate_query_supports_all_granularities_and_distinct_methods() -> None:
    candidates = [
        _candidate("state:after", granularity, method, suffix=f":{granularity.value}:{method.value}")
        for granularity in CandidateGranularity
        for method in SimilarityMethod
    ]
    query = SimilarityQuery(
        granularities=list(CandidateGranularity),
        methods=list(SimilarityMethod),
        roles=["implementation"],
        minimum_lines=1,
        minimum_tokens=1,
    )
    selected = select_similarity_candidates(candidates, query)

    assert {(item.granularity, item.method) for item in selected} == {
        (granularity, method) for granularity in CandidateGranularity for method in SimilarityMethod
    }
    assert all("verdict" in item.rationale for item in selected)
    assert len({item.candidate_id for item in selected}) == len(selected)


def test_jscpd_json_normalizes_through_bounded_external_evidence() -> None:
    raw = json.dumps(
        {
            "duplicates": [
                {
                    "format": "python",
                    "lines": 8,
                    "tokens": 80,
                    "fragment": "def repeated():\n    return 1\n",
                    "firstFile": {"name": "src/a.py", "start": 2, "end": 9},
                    "secondFile": {"name": "src/b.py", "start": 4, "end": 11},
                }
            ],
            "statistics": {"total": {"clones": 1}},
        }
    ).encode()
    query = SimilarityQuery(
        granularities=[CandidateGranularity.BLOCK],
        methods=[SimilarityMethod.EXTERNAL_NEAR_MATCH],
        minimum_lines=5,
        minimum_tokens=50,
    )
    artifact = parse_jscpd_report(
        raw,
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="provider:jscpd",
        provider_version="5.0",
        configuration_digest=_digest("strict-min-lines-5-min-tokens-50"),
        query=query,
        threshold=0.8,
    )

    assert len(artifact.candidates) == 1
    candidate = artifact.candidates[0]
    assert candidate.method is SimilarityMethod.EXTERNAL_NEAR_MATCH
    assert candidate.threshold == 0.8
    assert candidate.aligned_hunks[0].kind is HunkKind.EQUAL
    assert candidate.limitations
    assert parse_similarity_artifact(canonical_similarity_bytes(artifact)) == artifact

    with pytest.raises(SimilarityArtifactError, match="absolute path"):
        parse_jscpd_report(
            raw.replace(b"src/a.py", b"/outside/a.py"),
            repository_id="repository:fixture",
            source_state_id="state:after",
            provider_run_id="provider:jscpd",
            provider_version="5.0",
            configuration_digest=_digest("configuration"),
            query=query,
        )


def test_baseline_exact_and_normalized_methods_need_no_external_provider() -> None:
    candidates = [
        _candidate("state:after", CandidateGranularity.DEFINITION, SimilarityMethod.EXACT, suffix=":exact"),
        _candidate(
            "state:after",
            CandidateGranularity.DEFINITION,
            SimilarityMethod.NORMALIZED,
            suffix=":normalized",
        ),
    ]
    selected = select_similarity_candidates(
        candidates,
        SimilarityQuery(minimum_lines=5, minimum_tokens=50),
    )

    assert {item.method for item in selected} == {SimilarityMethod.EXACT, SimilarityMethod.NORMALIZED}
    assert all(item.provider_run_id is None for item in selected)


def test_jscpd_import_projects_through_the_common_provider_envelope() -> None:
    raw = json.dumps(
        {
            "duplicates": [
                {
                    "format": "python",
                    "lines": 5,
                    "tokens": 60,
                    "firstFile": {"name": "src/pkg/core.py", "start": 1, "end": 5},
                    "secondFile": {"name": "build/generated.py", "start": 1, "end": 5},
                }
            ]
        }
    ).encode()
    envelope = jscpd_provider_envelope(
        raw,
        baseline=_known_truth_evidence(),
        provider_run_id="provider:jscpd",
        provider_version="5.0",
        configuration_digest=_digest("configuration"),
        policy_digest=_digest("policy"),
        query=SimilarityQuery(
            granularities=[CandidateGranularity.BLOCK],
            methods=[SimilarityMethod.EXTERNAL_NEAR_MATCH],
        ),
    )

    assert envelope.artifact_type == "anatomize.provider"
    assert envelope.invocation.mode is InvocationMode.ARTIFACT_IMPORT
    assert envelope.status.value == "partial"
    assert len(envelope.payload.candidates) == 1
    assert envelope.payload.observations[0].record_type == "similarity_observation"
    assert envelope.payload.limitations and envelope.payload.omissions


def test_candidate_delta_reports_move_expansion_divergence_new_and_removed() -> None:
    before_stable = _candidate("state:before", CandidateGranularity.BLOCK, SimilarityMethod.NORMALIZED)
    after_changed = _candidate(
        "state:after",
        CandidateGranularity.BLOCK,
        SimilarityMethod.NORMALIZED,
        second_path="src/moved.py",
        end_line=14,
        second_digest="diverged",
    )
    before_removed = _candidate(
        "state:before",
        CandidateGranularity.FILE,
        SimilarityMethod.EXACT,
        suffix=":removed",
    )
    after_new = _candidate(
        "state:after",
        CandidateGranularity.EXPRESSION,
        SimilarityMethod.NORMALIZED,
        suffix=":new",
    )
    query = SimilarityQuery(
        granularities=list(CandidateGranularity),
        methods=list(SimilarityMethod),
        minimum_lines=1,
        minimum_tokens=1,
    )
    before = SimilarityArtifact(
        repository_id="repository:fixture",
        source_state_id="state:before",
        query=query,
        candidates=[before_stable, before_removed],
    )
    after = SimilarityArtifact(
        repository_id="repository:fixture",
        source_state_id="state:after",
        query=query,
        candidates=[after_changed, after_new],
    )
    deltas = compare_similarity_artifacts(before, after)
    kind_sets = [set(item.kinds) for item in deltas]

    assert {CandidateDeltaKind.MOVED, CandidateDeltaKind.EXPANDED, CandidateDeltaKind.DIVERGED} in kind_sets
    assert {CandidateDeltaKind.NEW} in kind_sets
    assert {CandidateDeltaKind.REMOVED} in kind_sets
    changed = next(item for item in deltas if CandidateDeltaKind.MOVED in item.kinds)
    assert changed.lineage == "exact"
    assert changed.reason_codes == ["member_path_changed", "matched_extent_increased", "aligned_evidence_changed"]


def test_future_similarity_schema_fails_closed() -> None:
    raw = b'{"artifact_type":"anatomize.similarity","schema_version":"2.0.0"}'
    with pytest.raises(SimilarityArtifactError, match="current"):
        parse_similarity_artifact(raw)


def test_jscpd_absolute_path_can_be_relativized_only_to_explicit_root(tmp_path: Path) -> None:
    source = tmp_path / "repo" / "src"
    source.mkdir(parents=True)
    report = {
        "duplicates": [
            {
                "format": "python",
                "lines": 5,
                "tokens": 50,
                "firstFile": {"name": str(source / "a.py"), "start": 1, "end": 5},
                "secondFile": {"name": str(source / "b.py"), "start": 2, "end": 6},
            }
        ]
    }
    artifact = parse_jscpd_report(
        json.dumps(report).encode(),
        repository_id="repository:fixture",
        source_state_id="state:after",
        provider_run_id="provider:jscpd",
        provider_version="5.0",
        configuration_digest=_digest("configuration"),
        query=SimilarityQuery(
            granularities=[CandidateGranularity.BLOCK],
            methods=[SimilarityMethod.EXTERNAL_NEAR_MATCH],
        ),
        repository_root=tmp_path / "repo",
    )
    assert [item.path for item in artifact.candidates[0].members] == ["src/a.py", "src/b.py"]
