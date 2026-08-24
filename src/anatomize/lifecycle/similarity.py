"""Multi-granular similarity evidence and external clone-artifact ingestion."""

from __future__ import annotations

from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from anatomize._artifacts import (
    BoundedJsonError,
    JsonLimits,
    canonical_ordered_json_bytes,
    content_id,
    parse_bounded_json_object,
    sha256_digest,
)
from anatomize._errors import AnatomizeError
from anatomize.evidence import EvidenceModel, EvidenceStrength, RepositoryEvidence, validate_repository_path
from anatomize.providers.models import ProviderEnvelope

SIMILARITY_ARTIFACT_TYPE: Literal["anatomize.similarity"] = "anatomize.similarity"
SIMILARITY_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class SimilarityArtifactError(AnatomizeError):
    """Bounded, actionable similarity artifact failure."""

class CandidateGranularity(str, Enum):
    FILE = "file"
    DEFINITION = "definition"
    BLOCK = "block"
    EXPRESSION = "expression"


class SimilarityMethod(str, Enum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    EXTERNAL_NEAR_MATCH = "external_near_match"


class HunkKind(str, Enum):
    EQUAL = "equal"
    REPLACE = "replace"
    INSERT = "insert"
    DELETE = "delete"


class CandidateDeltaKind(str, Enum):
    NEW = "new"
    REMOVED = "removed"
    EXPANDED = "expanded"
    REDUCED = "reduced"
    MOVED = "moved"
    DIVERGED = "diverged"
    UNCHANGED = "unchanged"


class SimilarityQuery(EvidenceModel):
    """Explicit candidate policy; values are acquisition bounds, not quality scores."""

    granularities: list[CandidateGranularity] = Field(
        default_factory=lambda: [CandidateGranularity.DEFINITION, CandidateGranularity.BLOCK]
    )
    methods: list[SimilarityMethod] = Field(
        default_factory=lambda: [SimilarityMethod.EXACT, SimilarityMethod.NORMALIZED]
    )
    roles: list[str] = Field(default_factory=list)
    minimum_lines: int = Field(default=3, ge=1)
    minimum_tokens: int = Field(default=50, ge=1)
    maximum_candidates: int = Field(default=1_000, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_unique(self) -> SimilarityQuery:
        if len(self.granularities) != len(set(self.granularities)):
            raise ValueError("similarity query granularities must be unique")
        if len(self.methods) != len(set(self.methods)):
            raise ValueError("similarity query methods must be unique")
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("similarity query roles must be unique")
        return self


class CandidateRegion(EvidenceModel):
    """One exact member coordinate in a candidate group."""

    source_state_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    entity_id: str | None = None
    role: str | None = None
    language: str | None = None
    content_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_region(self) -> CandidateRegion:
        validate_repository_path(self.path)
        if self.end_line < self.start_line:
            raise ValueError("candidate region end must not precede start")
        return self


class AlignedHunk(EvidenceModel):
    """One bounded correspondence or difference between two members."""

    hunk_id: str = Field(min_length=1)
    kind: HunkKind
    left_member: int = Field(ge=0)
    right_member: int = Field(ge=0)
    left_start_line: int = Field(ge=1)
    left_end_line: int = Field(ge=1)
    right_start_line: int = Field(ge=1)
    right_end_line: int = Field(ge=1)
    left_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    right_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    preview: str | None = Field(default=None, max_length=2_048)

    @model_validator(mode="after")
    def validate_hunk(self) -> AlignedHunk:
        if self.hunk_id != content_id("similarity-hunk", self.model_dump(mode="json", exclude={"hunk_id"})):
            raise ValueError("aligned hunk identifier does not match its content")
        return self


class SimilarityCandidate(EvidenceModel):
    """A review candidate with method-native evidence and no decision verdict."""

    candidate_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    granularity: CandidateGranularity
    method: SimilarityMethod
    method_version: str = Field(min_length=1)
    configuration_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_run_id: str | None = None
    threshold: float | None = Field(default=None, ge=0)
    token_count: int | None = Field(default=None, ge=0)
    members: list[CandidateRegion] = Field(min_length=2)
    aligned_hunks: list[AlignedHunk] = Field(default_factory=list)
    unmatched_member_regions: list[CandidateRegion] = Field(default_factory=list)
    strength: EvidenceStrength
    limitations: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> SimilarityCandidate:
        if any(item.source_state_id != self.source_state_id for item in self.members):
            raise ValueError("similarity candidate members must belong to its source state")
        if self.method is not SimilarityMethod.EXTERNAL_NEAR_MATCH and self.threshold is not None:
            raise ValueError("only an external near-match method may expose its native threshold")
        if self.candidate_id != _candidate_id(self):
            raise ValueError("similarity candidate identifier does not match stable acquisition identity")
        if len({item.hunk_id for item in self.aligned_hunks}) != len(self.aligned_hunks):
            raise ValueError("aligned hunk identities must be unique")
        return self


class SimilarityArtifact(EvidenceModel):
    """Portable candidates from baseline or independently executed providers."""

    artifact_type: Literal["anatomize.similarity"] = SIMILARITY_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = SIMILARITY_SCHEMA_VERSION
    repository_id: str = Field(min_length=1)
    source_state_id: str = Field(min_length=1)
    query: SimilarityQuery
    candidates: list[SimilarityCandidate] = Field(default_factory=list)
    provider_artifact_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_artifact(self) -> SimilarityArtifact:
        if any(item.source_state_id != self.source_state_id for item in self.candidates):
            raise ValueError("similarity artifact candidates must belong to its source state")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("similarity candidate identities must be unique")
        if len(self.candidates) > self.query.maximum_candidates:
            raise ValueError("similarity artifact exceeds its declared candidate bound")
        return self


class CandidateDelta(EvidenceModel):
    """Cross-state candidate change with explicit lineage certainty."""

    delta_id: str = Field(min_length=1)
    predecessor_candidate_id: str | None = None
    successor_candidate_id: str | None = None
    kinds: list[CandidateDeltaKind] = Field(min_length=1)
    lineage: Literal["exact", "candidate", "unavailable"]
    reason_codes: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_delta(self) -> CandidateDelta:
        if len(self.kinds) != len(set(self.kinds)) or len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("candidate delta kinds and reason codes must be unique")
        if self.delta_id != content_id("candidate-delta", self.model_dump(mode="json", exclude={"delta_id"})):
            raise ValueError("candidate delta identifier does not match its content")
        return self


def build_aligned_hunk(**values: Any) -> AlignedHunk:
    return AlignedHunk(hunk_id=content_id("similarity-hunk", values), **values)


def build_similarity_candidate(**values: Any) -> SimilarityCandidate:
    provisional = SimilarityCandidate.model_construct(candidate_id="pending", **values)
    return SimilarityCandidate(candidate_id=_candidate_id(provisional), **values)


def parse_jscpd_report(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    provider_run_id: str,
    provider_version: str,
    configuration_digest: str,
    query: SimilarityQuery,
    repository_root: Path | None = None,
    threshold: float | None = None,
    max_bytes: int = 64 * 1024 * 1024,
) -> SimilarityArtifact:
    """Normalize bounded jscpd JSON without executing jscpd or reading source."""
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(max_bytes=max_bytes, max_depth=64, max_values=2_000_000, max_string_bytes=max_bytes),
        )
    except BoundedJsonError as error:
        raise SimilarityArtifactError(
            f"jscpd_{error.code}",
            f"jscpd report {error}",
            remediation="Regenerate a bounded jscpd JSON report or raise an explicit trusted limit.",
        ) from error
    duplicates = payload.get("duplicates")
    if not isinstance(duplicates, list):
        raise SimilarityArtifactError(
            "jscpd_shape_invalid",
            "jscpd report requires a duplicates array",
            remediation="Generate the report with the supported jscpd JSON reporter.",
        )
    candidates: list[SimilarityCandidate] = []
    for index, clone in enumerate(duplicates):
        if not isinstance(clone, dict):
            raise SimilarityArtifactError(
                "jscpd_clone_invalid",
                f"jscpd duplicate {index} is not an object",
                remediation="Regenerate the jscpd JSON report.",
            )
        first = _jscpd_region(clone.get("firstFile"), source_state_id, repository_root)
        second = _jscpd_region(clone.get("secondFile"), source_state_id, repository_root)
        lines = clone.get("lines")
        tokens = clone.get("tokens")
        if (
            not isinstance(lines, int)
            or not isinstance(tokens, int)
            or lines < query.minimum_lines
            or tokens < query.minimum_tokens
        ):
            continue
        fragment = clone.get("fragment")
        fragment_text = fragment if isinstance(fragment, str) else None
        digest = sha256_digest(fragment_text.encode("utf-8")) if fragment_text is not None else None
        hunk = build_aligned_hunk(
            kind=HunkKind.EQUAL,
            left_member=0,
            right_member=1,
            left_start_line=first.start_line,
            left_end_line=first.end_line,
            right_start_line=second.start_line,
            right_end_line=second.end_line,
            left_digest=digest,
            right_digest=digest,
            preview=fragment_text[:2_048] if fragment_text is not None else None,
        )
        candidates.append(
            build_similarity_candidate(
                source_state_id=source_state_id,
                granularity=CandidateGranularity.BLOCK,
                method=SimilarityMethod.EXTERNAL_NEAR_MATCH,
                method_version=provider_version,
                configuration_digest=configuration_digest,
                provider_run_id=provider_run_id,
                threshold=threshold,
                token_count=tokens,
                members=[first, second],
                aligned_hunks=[hunk],
                unmatched_member_regions=[],
                strength=EvidenceStrength.CONSERVATIVE,
                limitations=[
                    "jscpd tokenization, mode, thresholds, exclusions, and format support remain provider-native.",
                    "Absence from this artifact is not evidence that no duplication exists.",
                ],
                rationale=f"jscpd reported {lines} lines and {tokens} tokens of duplicated content.",
            )
        )
    return SimilarityArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        query=query,
        candidates=select_similarity_candidates(candidates, query),
        provider_artifact_digest=sha256_digest(raw),
        limitations=["External candidate evidence is conservative and never a merge or deletion verdict."],
    )


def jscpd_provider_envelope(
    raw: bytes,
    *,
    baseline: RepositoryEvidence,
    provider_run_id: str,
    provider_version: str,
    configuration_digest: str,
    policy_digest: str,
    query: SimilarityQuery,
    repository_root: Path | None = None,
    threshold: float | None = None,
) -> ProviderEnvelope:
    """Project imported jscpd evidence through the canonical provider envelope."""
    from anatomize.evidence import (
        CandidateKind,
        CandidateRecord,
        CompletenessRecord,
        CompletenessStatus,
        FileCoordinateSpace,
        FileEntity,
        LimitationRecord,
        LocationOrigin,
        LocationRecord,
        ObservationStance,
        OmissionRecord,
        ProviderRunStatus,
        RangeEntity,
        SimilarityEntity,
        SimilarityObservation,
        SourcePosition,
        SourceRange,
    )
    from anatomize.providers import (
        AuthorityLevel,
        InvocationAuthority,
        InvocationMode,
        ProviderBatchBuilder,
        ProviderScope,
        ProviderToolIdentity,
        build_provider_envelope,
    )

    state = baseline.states[-1]
    artifact = parse_jscpd_report(
        raw,
        repository_id=baseline.repository_id,
        source_state_id=state.state_id,
        provider_run_id=provider_run_id,
        provider_version=provider_version,
        configuration_digest=configuration_digest,
        query=query,
        repository_root=repository_root,
        threshold=threshold,
    )
    builder = ProviderBatchBuilder(baseline)
    repository = builder.repository_entity(state)
    files = {
        item.path: item
        for item in baseline.entities
        if isinstance(item, FileEntity) and item.source_state_id == state.state_id
    }
    limitation = LimitationRecord(
        limitation_id=content_id("limitation:jscpd", {"run": provider_run_id, "version": provider_version}),
        provider_run_id=provider_run_id,
        code="external_near_match_not_exhaustive",
        summary="jscpd candidates depend on provider-native tokenization, format, mode, threshold, and exclusions.",
    )
    builder.limitations[limitation.limitation_id] = limitation
    omission = OmissionRecord(
        omission_id=content_id("omission:jscpd", {"run": provider_run_id, "state": state.state_id}),
        source_state_id=state.state_id,
        provider_run_id=provider_run_id,
        reason="External near-match acquisition is not exhaustive outside the declared jscpd scope.",
        scope_type="repository",
        scope_id=repository.entity_id,
        recoverable=True,
        remediation="Run an explicitly configured additional provider or expand jscpd scope.",
    )
    builder.omissions[omission.omission_id] = omission
    for candidate in artifact.candidates:
        member_ids: list[str] = []
        for ordinal, member in enumerate(candidate.members):
            file = files.get(member.path)
            if file is None:
                builder.add_degradation(
                    run_id=provider_run_id,
                    state_id=state.state_id,
                    code="jscpd_member_unmapped",
                    summary=f"jscpd member path did not map to canonical inventory: {member.path}",
                    scope_type="repository",
                    scope_id=repository.entity_id,
                    remediation="Rebuild inventory and jscpd evidence from the same source state.",
                    discriminator=candidate.candidate_id + member.path,
                )
                continue
            builder.include_baseline_entity(file)
            location_id = content_id(
                "location:jscpd-region",
                {
                    "candidate": candidate.candidate_id,
                    "ordinal": ordinal,
                    "path": member.path,
                    "start": member.start_line,
                    "end": member.end_line,
                },
            )
            range_id = content_id(
                "entity:jscpd-region",
                {"candidate": candidate.candidate_id, "location": location_id},
            )
            builder.locations[location_id] = LocationRecord(
                location_id=location_id,
                source_state_id=state.state_id,
                origin=LocationOrigin.REPOSITORY,
                file_id=file.entity_id,
                path=file.path,
                coordinate_space=FileCoordinateSpace(),
                source_range=SourceRange(
                    start=SourcePosition(line=member.start_line, column=0),
                    end=SourcePosition(line=member.end_line, column=0),
                ),
            )
            builder.entities[range_id] = RangeEntity(
                entity_id=range_id,
                source_state_id=state.state_id,
                display_name=f"{member.path}:{member.start_line}-{member.end_line}",
                location_ids=[location_id],
                provider_run_ids=[provider_run_id],
                range_kind="similarity_region",
                parent_entity_id=file.entity_id,
            )
            member_ids.append(range_id)
        if len(member_ids) < 2:
            continue
        similarity_id = content_id("entity:similarity", {"candidate": candidate.candidate_id})
        similarity = SimilarityEntity(
            entity_id=similarity_id,
            source_state_id=state.state_id,
            display_name=f"jscpd candidate {candidate.candidate_id}",
            method="jscpd-json",
            normalization="provider-native",
            member_entity_ids=member_ids,
        )
        builder.entities[similarity.entity_id] = similarity
        observation_id = content_id("observation:jscpd", {"candidate": candidate.candidate_id})
        observation = SimilarityObservation(
            observation_id=observation_id,
            source_state_id=state.state_id,
            provider_run_id=provider_run_id,
            method="jscpd-json",
            method_version=provider_version,
            strength=EvidenceStrength.CONSERVATIVE,
            stance=ObservationStance.QUALIFIES,
            completeness_id=None,
            limitation_ids=[limitation.limitation_id],
            rationale=candidate.rationale,
            similarity_entity_id=similarity.entity_id,
            member_entity_ids=similarity.member_entity_ids,
            score=candidate.threshold,
        )
        builder.observations[observation.observation_id] = observation
        builder.candidates[candidate.candidate_id] = CandidateRecord(
            candidate_id=candidate.candidate_id,
            source_state_id=state.state_id,
            kind=CandidateKind.DUPLICATION,
            member_entity_ids=similarity.member_entity_ids,
            method="jscpd-json",
            strength=EvidenceStrength.CONSERVATIVE,
            observation_ids=[observation.observation_id],
            rationale="External near-match candidate; inspect aligned evidence before any decision.",
        )
    completeness = CompletenessRecord(
        completeness_id=content_id("completeness:jscpd", {"run": provider_run_id, "state": state.state_id}),
        source_state_id=state.state_id,
        provider_run_id=provider_run_id,
        scope_type="repository",
        scope_id=repository.entity_id,
        evidence_families=["similarity.candidates"],
        status=CompletenessStatus.PARTIAL,
        omission_ids=sorted(builder.omissions),
    )
    payload = builder.build_payload([completeness])
    return build_provider_envelope(
        provider_run_id=provider_run_id,
        provider_id="jscpd",
        provider_version=provider_version,
        tool=ProviderToolIdentity(name="jscpd", version=provider_version),
        capabilities=["similarity.candidates"],
        languages=[],
        repository_id=baseline.repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=configuration_digest,
        scope=ProviderScope(
            scope_id=content_id(
                "provider-scope:jscpd",
                {"state": state.state_id, "query": query.model_dump(mode="json")},
            ),
            source_state_ids=[state.state_id],
            paths=sorted({item.path for candidate in artifact.candidates for item in candidate.members}),
            entity_ids=sorted(builder.entities),
            evidence_families=["similarity.candidates"],
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.ARTIFACT_IMPORT,
            level=AuthorityLevel.A1_ARTIFACT,
            policy_digest=policy_digest,
        ),
        status=ProviderRunStatus.PARTIAL,
        payload=payload,
    )


def compare_similarity_artifacts(before: SimilarityArtifact, after: SimilarityArtifact) -> list[CandidateDelta]:
    """Describe supported candidate change without guessing ambiguous lineage."""
    if before.repository_id != after.repository_id or before.source_state_id == after.source_state_id:
        raise ValueError("candidate comparison requires different states from one repository")
    before_by_key = {_lineage_key(item): item for item in before.candidates}
    after_by_key = {_lineage_key(item): item for item in after.candidates}
    result: list[CandidateDelta] = []
    for key in sorted(set(before_by_key) | set(after_by_key)):
        old = before_by_key.get(key)
        new = after_by_key.get(key)
        if old is None:
            result.append(_build_delta(None, new, [CandidateDeltaKind.NEW], "candidate", ["new_group"]))
            continue
        if new is None:
            result.append(_build_delta(old, None, [CandidateDeltaKind.REMOVED], "candidate", ["removed_group"]))
            continue
        kinds: list[CandidateDeltaKind] = []
        reasons: list[str] = []
        old_paths = [item.path for item in old.members]
        new_paths = [item.path for item in new.members]
        if old_paths != new_paths:
            kinds.append(CandidateDeltaKind.MOVED)
            reasons.append("member_path_changed")
        old_lines = sum(item.end_line - item.start_line + 1 for item in old.members)
        new_lines = sum(item.end_line - item.start_line + 1 for item in new.members)
        if new_lines > old_lines:
            kinds.append(CandidateDeltaKind.EXPANDED)
            reasons.append("matched_extent_increased")
        elif new_lines < old_lines:
            kinds.append(CandidateDeltaKind.REDUCED)
            reasons.append("matched_extent_decreased")
        if _evidence_digest(old) != _evidence_digest(new):
            kinds.append(CandidateDeltaKind.DIVERGED)
            reasons.append("aligned_evidence_changed")
        if not kinds:
            kinds = [CandidateDeltaKind.UNCHANGED]
            reasons = ["stable_candidate_evidence"]
        lineage: Literal["exact", "candidate", "unavailable"] = (
            "exact" if all(item.entity_id for item in [*old.members, *new.members]) else "candidate"
        )
        result.append(_build_delta(old, new, kinds, lineage, reasons))
    return result


def select_similarity_candidates(
    candidates: list[SimilarityCandidate],
    query: SimilarityQuery,
) -> list[SimilarityCandidate]:
    """Apply explicit method, granularity, role, size, and count policy."""
    selected = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        if candidate.granularity not in query.granularities or candidate.method not in query.methods:
            continue
        if query.roles and not set(query.roles).intersection(
            item.role for item in candidate.members if item.role is not None
        ):
            continue
        if min(item.end_line - item.start_line + 1 for item in candidate.members) < query.minimum_lines:
            continue
        if candidate.token_count is not None and candidate.token_count < query.minimum_tokens:
            continue
        selected.append(candidate)
        if len(selected) == query.maximum_candidates:
            break
    return selected


def similarity_json_schema() -> dict[str, Any]:
    return SimilarityArtifact.model_json_schema(mode="serialization")


def parse_similarity_artifact(raw: bytes, *, max_bytes: int = 64 * 1024 * 1024) -> SimilarityArtifact:
    """Parse one bounded current similarity artifact without legacy fallback."""
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(max_bytes=max_bytes, max_depth=64, max_values=2_000_000, max_string_bytes=max_bytes),
        )
    except BoundedJsonError as error:
        raise SimilarityArtifactError(
            f"similarity_{error.code}",
            f"Similarity artifact {error}",
            remediation="Regenerate a bounded current similarity artifact.",
        ) from error
    if (
        payload.get("artifact_type") != SIMILARITY_ARTIFACT_TYPE
        or payload.get("schema_version") != SIMILARITY_SCHEMA_VERSION
    ):
        raise SimilarityArtifactError(
            "similarity_schema_incompatible",
            "Expected current anatomize.similarity schema 1.0.0",
            remediation="Regenerate the artifact; legacy and future schemas are not interpreted.",
        )
    try:
        return SimilarityArtifact.model_validate(payload)
    except ValidationError as error:
        raise SimilarityArtifactError(
            "similarity_invalid",
            f"Similarity artifact failed validation ({error.error_count()} errors)",
            remediation="Inspect and regenerate the similarity artifact.",
        ) from error


def canonical_similarity_bytes(artifact: SimilarityArtifact) -> bytes:
    return canonical_ordered_json_bytes(artifact.model_dump(mode="json"))


def _candidate_id(candidate: SimilarityCandidate) -> str:
    identity = {
        "source_state_id": candidate.source_state_id,
        "granularity": candidate.granularity.value,
        "method": candidate.method.value,
        "method_version": candidate.method_version,
        "configuration_digest": candidate.configuration_digest,
        "provider_run_id": candidate.provider_run_id,
        "members": [
            {
                "entity_id": item.entity_id,
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "role": item.role,
            }
            for item in sorted(candidate.members, key=lambda value: (value.path, value.start_line, value.end_line))
        ],
    }
    return content_id("similarity-candidate", identity)


def _jscpd_region(value: Any, source_state_id: str, root: Path | None) -> CandidateRegion:
    if not isinstance(value, dict):
        raise SimilarityArtifactError(
            "jscpd_location_invalid",
            "jscpd clone requires firstFile and secondFile objects",
            remediation="Regenerate the jscpd JSON report.",
        )
    name, start, end = value.get("name"), value.get("start"), value.get("end")
    if not isinstance(name, str) or not isinstance(start, int) or not isinstance(end, int):
        raise SimilarityArtifactError(
            "jscpd_location_invalid",
            "jscpd file location requires name, start, and end",
            remediation="Regenerate the jscpd JSON report.",
        )
    path = Path(name)
    is_absolute = PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute()
    if is_absolute:
        if root is None:
            raise SimilarityArtifactError(
                "jscpd_absolute_path",
                "jscpd report contains an absolute path without an explicit repository root",
                remediation="Pass the trusted repository root used for the external run.",
            )
        if not path.is_absolute():
            raise SimilarityArtifactError(
                "jscpd_path_escape",
                "jscpd report uses an absolute path from another operating system",
                remediation="Regenerate jscpd output on this checkout or provide repository-relative paths.",
            )
        try:
            name = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise SimilarityArtifactError(
                "jscpd_path_escape",
                "jscpd report path is outside the explicit repository root",
                remediation="Reject the artifact and rerun jscpd inside the intended repository.",
            ) from error
    validate_repository_path(name)
    return CandidateRegion(source_state_id=source_state_id, path=name, start_line=start, end_line=end)


def _lineage_key(candidate: SimilarityCandidate) -> tuple[str, ...]:
    entity_ids = sorted(item.entity_id for item in candidate.members if item.entity_id is not None)
    if len(entity_ids) == len(candidate.members):
        return (candidate.granularity.value, candidate.method.value, *entity_ids)
    digests = sorted(item.content_digest for item in candidate.members if item.content_digest is not None)
    if len(digests) == len(candidate.members):
        return (candidate.granularity.value, candidate.method.value, *digests)
    return (candidate.candidate_id,)


def _evidence_digest(candidate: SimilarityCandidate) -> str:
    return sha256_digest(
        canonical_ordered_json_bytes(
            {
                "members": [item.model_dump(mode="json") for item in candidate.members],
                "aligned_hunks": [item.model_dump(mode="json") for item in candidate.aligned_hunks],
                "unmatched": [item.model_dump(mode="json") for item in candidate.unmatched_member_regions],
            }
        )
    )


def _build_delta(
    before: SimilarityCandidate | None,
    after: SimilarityCandidate | None,
    kinds: list[CandidateDeltaKind],
    lineage: Literal["exact", "candidate", "unavailable"],
    reasons: list[str],
) -> CandidateDelta:
    values = {
        "predecessor_candidate_id": before.candidate_id if before else None,
        "successor_candidate_id": after.candidate_id if after else None,
        "kinds": kinds,
        "lineage": lineage,
        "reason_codes": reasons,
    }
    return CandidateDelta(delta_id=content_id("candidate-delta", _json(values)), **values)


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
