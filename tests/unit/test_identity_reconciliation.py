from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from anatomize.evidence import (
    ContentClass,
    EmbeddedRegionCoordinateSpace,
    EvidenceProducer,
    EvidenceStrength,
    ExternalEntity,
    FileCoordinateSpace,
    FileEntity,
    GeneratedCoordinateSpace,
    IdentityReason,
    IdentityResolutionStatus,
    LocationOrigin,
    LocationRecord,
    NotebookCellCoordinateSpace,
    ProjectionCompleteness,
    RepositoryEntity,
    RepositoryEvidence,
    SourcePosition,
    SourceRange,
    SourceStateRecord,
)
from anatomize.identity import (
    ClaimCertainty,
    ColumnEncoding,
    CoordinateConvention,
    CoordinateError,
    FileIdentityKey,
    GeneratedIdentityKey,
    IdentityMappingClaim,
    PathKind,
    ProviderRange,
    SourceCoordinateMap,
    canonical_identity_id,
    parse_identity_key,
    reconcile_identity_claims,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "identity"


def test_coordinate_known_truth_round_trips_all_supported_conventions() -> None:
    fixture: dict[str, Any] = json.loads((FIXTURE_ROOT / "coordinate-corpus.json").read_text())
    mapper = SourceCoordinateMap(fixture["source"])

    for case in fixture["cases"]:
        convention = CoordinateConvention.model_validate(case["convention"])
        provider_range = ProviderRange.model_validate(case["provider_range"])
        expected = SourceRange.model_validate(case["canonical_range"])
        canonical = mapper.to_canonical(provider_range, convention)

        assert canonical == expected, case["case_id"]
        assert mapper.from_canonical(canonical, convention) == provider_range, case["case_id"]

    utf8 = CoordinateConvention(line_base=0, column_encoding=ColumnEncoding.UTF8_BYTE)
    with pytest.raises(CoordinateError) as split:
        mapper.to_canonical(
            ProviderRange(
                start={"line": 0, "column": 2},
                end={"line": 0, "column": 3},
            ),
            utf8,
        )
    assert split.value.code == "coordinate_column_splits_character"


def test_notebook_embedded_and_generated_coordinate_spaces_validate_as_one_graph() -> None:
    state = SourceStateRecord(
        state_id="state:coordinate",
        repository_id="repository:coordinate",
        revision="abc123",
        dirty=False,
        content_digest="sha256:coordinate",
        file_count=3,
    )
    locations = [
        LocationRecord(
            location_id="location:notebook-file",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:notebook",
            path="notebooks/analysis.ipynb",
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:cell",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:notebook",
            path="notebooks/analysis.ipynb",
            coordinate_space=NotebookCellCoordinateSpace(
                cell_id="cell-7",
                cell_index=3,
                source_digest="sha256:cell",
            ),
            source_range=SourceRange(
                start=SourcePosition(line=1, column=0),
                end=SourcePosition(line=2, column=8),
            ),
        ),
        LocationRecord(
            location_id="location:qmd-file",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:qmd",
            path="reports/analysis.qmd",
            coordinate_space=FileCoordinateSpace(),
        ),
        LocationRecord(
            location_id="location:host-region",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:qmd",
            path="reports/analysis.qmd",
            coordinate_space=FileCoordinateSpace(),
            source_range=SourceRange(
                start=SourcePosition(line=10, column=0),
                end=SourcePosition(line=18, column=3),
            ),
        ),
        LocationRecord(
            location_id="location:embedded-r",
            source_state_id=state.state_id,
            origin=LocationOrigin.REPOSITORY,
            file_id="entity:qmd",
            path="reports/analysis.qmd",
            coordinate_space=EmbeddedRegionCoordinateSpace(
                region_id="chunk-fit",
                language="r",
                host_location_id="location:host-region",
                projection=ProjectionCompleteness.EXACT,
            ),
            source_range=SourceRange(
                start=SourcePosition(line=1, column=0),
                end=SourcePosition(line=6, column=3),
            ),
        ),
        LocationRecord(
            location_id="location:generated",
            source_state_id=state.state_id,
            origin=LocationOrigin.GENERATED,
            file_id="entity:generated",
            path="generated/model.py",
            coordinate_space=GeneratedCoordinateSpace(
                generator_identity_id="workflow:render",
                source_location_ids=["location:cell", "location:embedded-r"],
                projection=ProjectionCompleteness.PARTIAL,
            ),
        ),
    ]
    evidence = RepositoryEvidence(
        producer=EvidenceProducer(version="identity-test"),
        repository_id=state.repository_id,
        states=[state],
        locations=locations,
        entities=[
            RepositoryEntity(
                entity_id="entity:repository",
                source_state_id=state.state_id,
                repository_id=state.repository_id,
                display_name="coordinate",
                root_name="coordinate",
            ),
            FileEntity(
                entity_id="entity:notebook",
                source_state_id=state.state_id,
                display_name="notebooks/analysis.ipynb",
                location_ids=["location:notebook-file", "location:cell"],
                path="notebooks/analysis.ipynb",
                size_bytes=10,
            ),
            FileEntity(
                entity_id="entity:qmd",
                source_state_id=state.state_id,
                display_name="reports/analysis.qmd",
                location_ids=["location:qmd-file", "location:host-region", "location:embedded-r"],
                path="reports/analysis.qmd",
                size_bytes=10,
            ),
            FileEntity(
                entity_id="entity:generated",
                source_state_id=state.state_id,
                display_name="generated/model.py",
                location_ids=["location:generated"],
                path="generated/model.py",
                size_bytes=10,
                content_class=ContentClass.GENERATED,
            ),
        ],
    )
    assert RepositoryEvidence.model_validate(evidence.model_dump(mode="json")) == evidence

    cyclic = evidence.model_dump(mode="json")
    cyclic["locations"][3]["coordinate_space"] = {
        "space_type": "embedded_region",
        "region_id": "cycle",
        "language": "r",
        "host_location_id": "location:embedded-r",
        "projection": "exact",
    }
    with pytest.raises(ValidationError, match="projection cycle"):
        RepositoryEvidence.model_validate(cyclic)


def test_identity_corpus_is_collision_resistant_case_sensitive_and_portable() -> None:
    fixture: dict[str, Any] = json.loads((FIXTURE_ROOT / "identity-corpus.json").read_text())
    identities = {
        case["case_id"]: canonical_identity_id(parse_identity_key(case["key"]))
        for case in fixture["cases"]
    }

    assert len(identities) == len(set(identities.values()))
    assert identities["colliding-root-a"] != identities["colliding-root-b"]
    assert identities["symbol-definition"] != identities["symbol-reexport"]
    assert identities["overload-int"] != identities["overload-str"]
    assert identities["case-lower"] != identities["case-upper"]
    assert all("/home/" not in identity for identity in identities.values())

    generated = GeneratedIdentityKey(
        repository_id="repository:a",
        source_state_id="state:a",
        path="generated/model.py",
        generator_identity_id="workflow:render",
        source_identity_ids=["identity:cell", "identity:chunk"],
        mapping_strength=EvidenceStrength.DERIVED,
    )
    assert canonical_identity_id(generated) == canonical_identity_id(
        GeneratedIdentityKey(
            repository_id=generated.repository_id,
            source_state_id=generated.source_state_id,
            path=generated.path,
            generator_identity_id=generated.generator_identity_id,
            source_identity_ids=list(reversed(generated.source_identity_ids)),
            mapping_strength=generated.mapping_strength,
        )
    )

    with pytest.raises(ValidationError, match="normalized and relative"):
        FileIdentityKey(
            repository_id="repository:a",
            source_state_id="state:a",
            path="/home/person/checkout/core.py",
        )
    with pytest.raises(ValidationError, match="portable target"):
        FileIdentityKey(
            repository_id="repository:a",
            source_state_id="state:a",
            path="src/current.py",
            path_kind=PathKind.SYMLINK,
        )
    with pytest.raises(ValidationError, match="checkout-specific"):
        ExternalEntity(
            entity_id="entity:external",
            source_state_id="state:a",
            display_name="leak",
            identity_scheme="debug-path",
            external_identity="file:///home/person/checkout/core.py",
            entity_kind="symbol",
        )


def _claim(
    provider: str,
    certainty: ClaimCertainty,
    candidates: list[str],
    reason: IdentityReason,
) -> IdentityMappingClaim:
    return IdentityMappingClaim(
        provider_run_id=provider,
        certainty=certainty,
        candidate_entity_ids=candidates,
        reason_codes=[reason],
    )


@pytest.mark.parametrize(
    ("claims", "expected", "candidate_ids"),
    [
        (
            [
                _claim("provider:ast", ClaimCertainty.EXACT, ["entity:a"], IdentityReason.DECLARED_ALIAS),
                _claim("provider:semantic", ClaimCertainty.EXACT, ["entity:a"], IdentityReason.REEXPORT),
            ],
            IdentityResolutionStatus.EXACT,
            ["entity:a"],
        ),
        (
            [_claim("provider:ast", ClaimCertainty.UNRESOLVED, [], IdentityReason.NO_CANDIDATE)],
            IdentityResolutionStatus.UNRESOLVED,
            [],
        ),
        (
            [
                _claim(
                    "provider:semantic",
                    ClaimCertainty.CANDIDATE,
                    ["entity:a", "entity:b"],
                    IdentityReason.MULTIPLE_CANDIDATES,
                )
            ],
            IdentityResolutionStatus.AMBIGUOUS,
            ["entity:a", "entity:b"],
        ),
        (
            [
                _claim("provider:ast", ClaimCertainty.EXACT, ["entity:a"], IdentityReason.DECLARED_ALIAS),
                _claim(
                    "provider:semantic",
                    ClaimCertainty.EXACT,
                    ["entity:b"],
                    IdentityReason.PROVIDER_DISAGREEMENT,
                ),
            ],
            IdentityResolutionStatus.CONFLICTING,
            ["entity:a", "entity:b"],
        ),
        (
            [
                _claim("provider:ast", ClaimCertainty.EXACT, ["entity:a"], IdentityReason.DECLARED_ALIAS),
                _claim(
                    "provider:runtime",
                    ClaimCertainty.CANDIDATE,
                    ["entity:a", "entity:b"],
                    IdentityReason.INCOMPLETE_SCOPE,
                ),
            ],
            IdentityResolutionStatus.AMBIGUOUS,
            ["entity:a", "entity:b"],
        ),
    ],
)
def test_reconciliation_retains_exact_unresolved_ambiguous_and_conflicting_claims(
    claims: list[IdentityMappingClaim],
    expected: IdentityResolutionStatus,
    candidate_ids: list[str],
) -> None:
    alias = reconcile_identity_claims(
        repository_id="repository:a",
        source_state_id="state:a",
        scheme="python.import",
        value="pkg.run",
        claims=claims,
        known_entity_ids={"entity:a", "entity:b"},
        rationale="All provider claims are retained without selecting a preferred tool.",
    )

    assert alias.resolution is expected
    assert [candidate.entity_id for candidate in alias.candidates] == candidate_ids
    assert alias.provider_run_ids == sorted(claim.provider_run_id for claim in claims)
    assert alias.reason_codes


def test_reconciliation_rejects_unknown_candidates_and_checkout_identity_leaks() -> None:
    claim = _claim("provider:ast", ClaimCertainty.EXACT, ["entity:missing"], IdentityReason.DECLARED_ALIAS)
    with pytest.raises(ValueError, match="unknown candidate"):
        reconcile_identity_claims(
            repository_id="repository:a",
            source_state_id="state:a",
            scheme="python.import",
            value="pkg.run",
            claims=[claim],
            known_entity_ids={"entity:a"},
            rationale="Unknown candidates cannot enter canonical evidence.",
        )

    with pytest.raises(ValidationError, match="checkout-specific"):
        reconcile_identity_claims(
            repository_id="repository:a",
            source_state_id="state:a",
            scheme="debug.path",
            value="/home/person/checkout/src/core.py",
            claims=[
                _claim("provider:ast", ClaimCertainty.UNRESOLVED, [], IdentityReason.NO_CANDIDATE)
            ],
            known_entity_ids=set(),
            rationale="Local paths must remain private.",
        )
