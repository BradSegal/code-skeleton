"""Typed semantic-change dossiers and evidence-bound implementation closure."""

from __future__ import annotations

from collections.abc import Mapping, Set
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from anatomize._artifacts import content_id
from anatomize.evidence import EvidenceModel

CHANGE_DOSSIER_TYPE: Literal["anatomize.change-dossier"] = "anatomize.change-dossier"
CHANGE_DOSSIER_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
IMPLEMENTATION_INTENT_TYPE: Literal["anatomize.implementation-intent"] = (
    "anatomize.implementation-intent"
)
IMPLEMENTATION_INTENT_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
CLOSURE_REPORT_TYPE: Literal["anatomize.closure-report"] = "anatomize.closure-report"
CLOSURE_REPORT_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class ChangeDimension(str, Enum):
    TEXTUAL = "textual"
    ENTITY = "entity"
    API = "api"
    RELATIONSHIP = "relationship"
    DIAGNOSTIC = "diagnostic"
    TEST = "test"
    RUNTIME = "runtime"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    WORKFLOW = "workflow"
    DATA = "data"
    ENVIRONMENT = "environment"
    ARTIFACT = "artifact"
    CANDIDATE = "candidate"


class ChangeBoundaryKind(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    MOVED = "moved"
    RENAMED = "renamed"
    DELETED = "deleted"
    SPLIT = "split"
    MERGED = "merged"
    REEXPORTED = "reexported"
    GENERATED = "generated"
    ABSENT_BASELINE = "absent_baseline"
    PROVIDER_MISMATCH = "provider_mismatch"


class ChangeEvidence(EvidenceModel):
    change_id: str
    dimension: ChangeDimension
    kinds: list[ChangeBoundaryKind]
    predecessor_refs: list[str] = Field(default_factory=list)
    successor_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    lineage: Literal["exact", "candidate", "unavailable"]
    rationale: str

    @model_validator(mode="after")
    def validate_change(self) -> ChangeEvidence:
        if len(self.kinds) != len(set(self.kinds)):
            raise ValueError("change kinds must be unique")
        if not self.predecessor_refs and not self.successor_refs:
            raise ValueError("change evidence requires a predecessor or successor reference")
        if self.change_id != _change_id(self):
            raise ValueError("change identifier does not match its evidence")
        return self


class ChangeDossier(EvidenceModel):
    artifact_type: Literal["anatomize.change-dossier"] = CHANGE_DOSSIER_TYPE
    schema_version: Literal["1.0.0"] = CHANGE_DOSSIER_SCHEMA_VERSION
    dossier_id: str
    repository_id: str
    before_source_state_id: str | None
    after_source_state_id: str
    before_dossier_id: str | None
    after_dossier_id: str
    before_evidence_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    after_evidence_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    before_provider_digests: dict[str, str] = Field(default_factory=dict)
    after_provider_digests: dict[str, str] = Field(default_factory=dict)
    changes: list[ChangeEvidence]
    affected_evidence_refs: list[str] = Field(default_factory=list)
    blast_radius_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dossier(self) -> ChangeDossier:
        if len({item.change_id for item in self.changes}) != len(self.changes):
            raise ValueError("change dossier change identities must be unique")
        if self.before_source_state_id is None and not any(
            ChangeBoundaryKind.ABSENT_BASELINE in item.kinds for item in self.changes
        ):
            raise ValueError("missing before state requires absent-baseline evidence")
        if self.before_provider_digests.keys() != self.after_provider_digests.keys() and not any(
            ChangeBoundaryKind.PROVIDER_MISMATCH in item.kinds for item in self.changes
        ):
            raise ValueError("provider-set mismatch requires explicit change evidence")
        if self.dossier_id != _change_dossier_id(self):
            raise ValueError("change dossier identifier does not match its evidence")
        return self


class ObligationKind(str, Enum):
    OWNER = "owner"
    CONTRACT = "contract"
    CONSUMER = "consumer"
    CANDIDATE = "candidate"
    TEST = "test"
    DOCUMENTATION = "documentation"
    WORKFLOW = "workflow"
    UNKNOWN = "unknown"


class ImplementationObligation(EvidenceModel):
    obligation_id: str
    kind: ObligationKind
    subject_ref: str
    expectation: str
    required_evidence_kinds: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_obligation(self) -> ImplementationObligation:
        if self.obligation_id != _obligation_id(self):
            raise ValueError("implementation obligation identifier does not match its content")
        return self


class ImplementationIntent(EvidenceModel):
    artifact_type: Literal["anatomize.implementation-intent"] = IMPLEMENTATION_INTENT_TYPE
    schema_version: Literal["1.0.0"] = IMPLEMENTATION_INTENT_SCHEMA_VERSION
    intent_id: str
    repository_id: str
    before_source_state_id: str
    before_dossier_id: str
    before_evidence_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    before_provider_digests: dict[str, str]
    decision_overlay_id: str | None = None
    obligations: list[ImplementationObligation]
    declared_unknowns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_intent(self) -> ImplementationIntent:
        if len({item.obligation_id for item in self.obligations}) != len(self.obligations):
            raise ValueError("implementation obligation identities must be unique")
        if self.intent_id != _intent_id(self):
            raise ValueError("implementation intent identifier does not match its content")
        return self


class ObligationStatus(str, Enum):
    SATISFIED = "satisfied"
    DISCREPANCY = "discrepancy"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class ClosureObservation(EvidenceModel):
    obligation_id: str
    source_state_id: str
    status: ObligationStatus
    evidence_refs: list[str] = Field(default_factory=list)
    observed: str
    limitations: list[str] = Field(default_factory=list)


class ClosureOutcome(str, Enum):
    CLOSED = "closed"
    DISCREPANCY = "discrepancy"
    INCOMPLETE = "incomplete"
    STALE = "stale"


class ClosureReport(EvidenceModel):
    artifact_type: Literal["anatomize.closure-report"] = CLOSURE_REPORT_TYPE
    schema_version: Literal["1.0.0"] = CLOSURE_REPORT_SCHEMA_VERSION
    report_id: str
    intent_id: str
    repository_id: str
    after_source_state_id: str
    after_source_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    after_provider_digests: dict[str, str]
    provider_completeness: dict[str, Literal["complete", "partial", "unavailable", "unknown"]]
    observations: list[ClosureObservation]
    outcome: ClosureOutcome
    discrepancy_obligation_ids: list[str]
    unresolved_obligation_ids: list[str]
    limitations: list[str]

    @model_validator(mode="after")
    def validate_report(self) -> ClosureReport:
        if self.report_id != _closure_report_id(self):
            raise ValueError("closure report identifier does not match its evidence")
        return self


def build_change_evidence(**values: Any) -> ChangeEvidence:
    provisional = ChangeEvidence.model_construct(change_id="pending", **values)
    return ChangeEvidence(change_id=_change_id(provisional), **values)


def build_change_dossier(**values: Any) -> ChangeDossier:
    provisional = ChangeDossier.model_construct(dossier_id="pending", **values)
    return ChangeDossier(dossier_id=_change_dossier_id(provisional), **values)


def build_implementation_obligation(**values: Any) -> ImplementationObligation:
    provisional = ImplementationObligation.model_construct(obligation_id="pending", **values)
    return ImplementationObligation(obligation_id=_obligation_id(provisional), **values)


def build_implementation_intent(**values: Any) -> ImplementationIntent:
    provisional = ImplementationIntent.model_construct(intent_id="pending", **values)
    return ImplementationIntent(intent_id=_intent_id(provisional), **values)


def verify_implementation_closure(
    intent: ImplementationIntent,
    *,
    after_source_state_id: str,
    after_source_state_digest: str,
    after_provider_digests: dict[str, str],
    provider_completeness: dict[str, Literal["complete", "partial", "unavailable", "unknown"]],
    observations: list[ClosureObservation],
    evidence_kinds_by_ref: Mapping[str, Set[str]],
) -> ClosureReport:
    """Evaluate each declared obligation at one exact after state without inference across kinds."""
    if after_source_state_id == intent.before_source_state_id:
        raise ValueError("closure requires a distinct after source state")
    if provider_completeness.keys() != after_provider_digests.keys():
        raise ValueError("provider completeness must cover the exact after provider set")
    reused = sorted(
        provider
        for provider, digest in after_provider_digests.items()
        if intent.before_provider_digests.get(provider) == digest
    )
    if reused:
        raise ValueError("closure cannot reuse pre-change provider evidence: " + ", ".join(reused))
    expected = {item.obligation_id for item in intent.obligations}
    by_id = {item.obligation_id: item for item in observations}
    if len(by_id) != len(observations) or not set(by_id).issubset(expected):
        raise ValueError("closure observations must uniquely reference declared obligations")
    normalized: list[ClosureObservation] = []
    for obligation in intent.obligations:
        observation = by_id.get(obligation.obligation_id)
        if observation is None:
            normalized.append(
                ClosureObservation(
                    obligation_id=obligation.obligation_id,
                    source_state_id=after_source_state_id,
                    status=ObligationStatus.UNRESOLVED,
                    observed="No after-state observation was supplied.",
                    limitations=["Absence of evidence is not evidence that the obligation failed."],
                )
            )
        elif observation.source_state_id != after_source_state_id:
            normalized.append(
                observation.model_copy(
                    update={
                        "status": ObligationStatus.STALE,
                        "limitations": [
                            *observation.limitations,
                            "Observation belongs to a different source state.",
                        ],
                    }
                )
            )
        elif observation.status is ObligationStatus.SATISFIED:
            unknown_refs = sorted(set(observation.evidence_refs).difference(evidence_kinds_by_ref))
            observed_kinds = {
                kind
                for reference in observation.evidence_refs
                for kind in evidence_kinds_by_ref.get(reference, set())
            }
            missing_kinds = sorted(set(obligation.required_evidence_kinds).difference(observed_kinds))
            limitations = list(observation.limitations)
            if not observation.evidence_refs:
                limitations.append("A satisfied obligation requires explicit after-state evidence.")
            if unknown_refs:
                limitations.append("Evidence references are absent from the after session: " + ", ".join(unknown_refs))
            if missing_kinds:
                limitations.append("Required after-state evidence kinds are absent: " + ", ".join(missing_kinds))
            normalized.append(
                observation.model_copy(
                    update={
                        "status": ObligationStatus.UNRESOLVED if limitations else observation.status,
                        "limitations": limitations,
                    }
                )
            )
        else:
            normalized.append(observation)
    statuses = {item.status for item in normalized}
    partial_providers = sorted(
        provider for provider, status in provider_completeness.items() if status != "complete"
    )
    limitations = [
        "A satisfied test obligation establishes only its declared selected test evidence, not complete safety."
    ]
    if partial_providers:
        limitations.append("Provider evidence is incomplete: " + ", ".join(partial_providers))
    if intent.declared_unknowns:
        limitations.append("Declared unknowns remain: " + "; ".join(intent.declared_unknowns))
    if ObligationStatus.STALE in statuses:
        outcome = ClosureOutcome.STALE
    elif ObligationStatus.DISCREPANCY in statuses:
        outcome = ClosureOutcome.DISCREPANCY
    elif (
        statuses == {ObligationStatus.SATISFIED}
        and not partial_providers
        and not intent.declared_unknowns
        and len(normalized) == len(intent.obligations)
    ):
        outcome = ClosureOutcome.CLOSED
    else:
        outcome = ClosureOutcome.INCOMPLETE
    sorted_after_providers = dict(sorted(after_provider_digests.items()))
    sorted_completeness = dict(sorted(provider_completeness.items()))
    discrepancies = sorted(
        item.obligation_id for item in normalized if item.status is ObligationStatus.DISCREPANCY
    )
    unresolved = sorted(
        item.obligation_id
        for item in normalized
        if item.status in {ObligationStatus.UNRESOLVED, ObligationStatus.UNAVAILABLE, ObligationStatus.STALE}
    )
    provisional = ClosureReport.model_construct(
        report_id="pending",
        intent_id=intent.intent_id,
        repository_id=intent.repository_id,
        after_source_state_id=after_source_state_id,
        after_source_state_digest=after_source_state_digest,
        after_provider_digests=sorted_after_providers,
        provider_completeness=sorted_completeness,
        observations=normalized,
        outcome=outcome,
        discrepancy_obligation_ids=discrepancies,
        unresolved_obligation_ids=unresolved,
        limitations=limitations,
    )
    return ClosureReport(
        report_id=_closure_report_id(provisional),
        intent_id=intent.intent_id,
        repository_id=intent.repository_id,
        after_source_state_id=after_source_state_id,
        after_source_state_digest=after_source_state_digest,
        after_provider_digests=sorted_after_providers,
        provider_completeness=sorted_completeness,
        observations=normalized,
        outcome=outcome,
        discrepancy_obligation_ids=discrepancies,
        unresolved_obligation_ids=unresolved,
        limitations=limitations,
    )


def _change_id(change: ChangeEvidence) -> str:
    return content_id("semantic-change", change.model_dump(mode="json", exclude={"change_id", "rationale"}))


def _change_dossier_id(dossier: ChangeDossier) -> str:
    return content_id(
        "change-dossier",
        dossier.model_dump(mode="json", exclude={"dossier_id", "artifact_type", "schema_version"}),
    )


def _obligation_id(obligation: ImplementationObligation) -> str:
    return content_id(
        "implementation-obligation",
        obligation.model_dump(mode="json", exclude={"obligation_id"}),
    )


def _intent_id(intent: ImplementationIntent) -> str:
    return content_id(
        "implementation-intent",
        intent.model_dump(mode="json", exclude={"intent_id", "artifact_type", "schema_version"}),
    )


def _closure_report_id(report: ClosureReport) -> str:
    return content_id(
        "closure-report",
        report.model_dump(mode="json", exclude={"report_id", "artifact_type", "schema_version"}),
    )
