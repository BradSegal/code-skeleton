from __future__ import annotations

import json
import random
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from anatomize.evidence import (
    EvidenceArtifactError,
    SourcePosition,
    SourceRange,
    parse_evidence,
)
from anatomize.identity import (
    ColumnEncoding,
    CoordinateConvention,
    SourceCoordinateMap,
)
from anatomize.providers import (
    ProviderArtifactLimits,
    ProviderEnvelopeError,
    parse_provider_envelope,
)
from anatomize.sessions import (
    ReviewSessionManifest,
    SessionArtifactError,
    parse_session_bundle,
)
from anatomize.temporal import ComparisonArtifactError, parse_comparison

SESSION_FIXTURE = Path("tests/fixtures/sessions/session-manifest-golden.json")


def test_unicode_coordinate_property_round_trips_across_all_provider_conventions() -> None:
    generator = random.Random(20260823)
    alphabet = ["a", "Z", "0", " ", "\t", "é", "e\u0301", "漢", "😀"]
    conventions = [
        CoordinateConvention(line_base=line_base, column_base=column_base, column_encoding=encoding)
        for line_base in (0, 1)
        for column_base in (0, 1)
        for encoding in ColumnEncoding
    ]
    for _ in range(300):
        lines = ["".join(generator.choice(alphabet) for _ in range(generator.randrange(0, 25))) for _ in range(4)]
        source = "\n".join(lines)
        line_index = generator.randrange(len(lines))
        start = generator.randrange(len(lines[line_index]) + 1)
        end = generator.randrange(start, len(lines[line_index]) + 1)
        canonical = SourceRange(
            start=SourcePosition(line=line_index + 1, column=start),
            end=SourcePosition(line=line_index + 1, column=end),
        )
        coordinate_map = SourceCoordinateMap(source)
        for convention in conventions:
            provider_range = coordinate_map.from_canonical(canonical, convention)
            assert coordinate_map.to_canonical(provider_range, convention) == canonical


@pytest.mark.parametrize(
    "mutation",
    [
        ("configuration_digest", "sha256:" + "0" * 64),
        ("policy_digest", "sha256:" + "1" * 64),
        ("query.parameters.focus", "different.py"),
        ("budgets.0.used", 0),
        ("omissions.0.rationale", "Changed rationale."),
        ("providers.0.provider_version", "2.0.0"),
        ("source_states.0.source_state.content_digest", "sha256:" + "2" * 64),
        ("artifacts.0.portable_path", "changed/output.json"),
    ],
)
def test_manifest_mutation_cannot_reuse_content_id(mutation: tuple[str, object]) -> None:
    payload = json.loads(SESSION_FIXTURE.read_text(encoding="utf-8"))
    path, replacement = mutation
    target: object = payload
    parts = path.split(".")
    for part in parts[:-1]:
        if isinstance(target, list):
            target = target[int(part)]
        else:
            assert isinstance(target, dict)
            target = target[part]
    if isinstance(target, list):
        target[int(parts[-1])] = replacement
    else:
        assert isinstance(target, dict)
        target[parts[-1]] = replacement
    with pytest.raises(ValidationError):
        ReviewSessionManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("parser", "kwargs", "error_type", "expected_code"),
    [
        (parse_evidence, {"max_depth": 3}, EvidenceArtifactError, "artifact_depth_limit"),
        (parse_comparison, {"max_depth": 3}, ComparisonArtifactError, "comparison_artifact_depth_limit"),
        (parse_session_bundle, {"max_depth": 3}, SessionArtifactError, "session_bundle_depth_limit"),
        (
            parse_provider_envelope,
            {"limits": ProviderArtifactLimits(max_depth=3)},
            ProviderEnvelopeError,
            "provider_artifact_depth_limit",
        ),
    ],
)
def test_all_public_artifact_parsers_enforce_structural_resource_bounds(
    parser: Callable[..., object],
    kwargs: dict[str, object],
    error_type: type[Exception],
    expected_code: str,
) -> None:
    nested = json.dumps({"a": {"b": {"c": {"d": 1}}}}).encode()
    with pytest.raises(error_type) as caught:
        parser(nested, **kwargs)
    assert getattr(caught.value, "code") == expected_code
    assert getattr(caught.value, "remediation")


def test_all_public_artifact_parsers_fail_closed_under_deterministic_byte_fuzz() -> None:
    generator = random.Random(8128)
    parsers: list[tuple[Callable[..., object], type[Exception]]] = [
        (parse_evidence, EvidenceArtifactError),
        (parse_comparison, ComparisonArtifactError),
        (parse_session_bundle, SessionArtifactError),
        (parse_provider_envelope, ProviderEnvelopeError),
    ]
    corpus = [
        b"",
        b"null",
        b"[]",
        b"{}",
        b'{"schema_version":"99.0.0"}',
        bytes(range(256)),
    ]
    corpus.extend(
        bytes(generator.randrange(256) for _ in range(generator.randrange(0, 512)))
        for _ in range(500)
    )
    for raw in corpus:
        for parser, error_type in parsers:
            with pytest.raises(error_type) as caught:
                parser(raw)
            assert getattr(caught.value, "code")
            assert getattr(caught.value, "remediation")


def test_session_manifest_rejects_absolute_traversal_and_checkout_specific_paths() -> None:
    for path in ["/tmp/report.json", "../report.json", "reports/../../secret", r"C:\\secret.json"]:
        payload = deepcopy(json.loads(SESSION_FIXTURE.read_text(encoding="utf-8")))
        payload["artifacts"][0]["portable_path"] = path
        with pytest.raises(ValidationError):
            ReviewSessionManifest.model_validate(payload)
