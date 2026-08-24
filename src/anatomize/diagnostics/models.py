"""Typed SARIF 2.1.0 subset and exact Anatomize ingestion binding."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from anatomize._artifacts import canonical_ordered_json_bytes, sha256_digest
from anatomize.evidence import ContractKind, EvidenceModel, SourceStateRecord

SARIF_VERSION: Literal["2.1.0"] = "2.1.0"
DEFAULT_MAX_SARIF_BYTES = 128 * 1024 * 1024


class SarifModel(BaseModel):
    """Frozen SARIF projection that retains standard and vendor extension fields."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)


class SarifMessage(SarifModel):
    text: str | None = None
    markdown: str | None = None
    message_id: str | None = Field(default=None, alias="id")
    arguments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_message_identity(self) -> SarifMessage:
        if self.text is None and self.markdown is None and self.message_id is None:
            raise ValueError("SARIF message requires text, markdown, or id")
        return self


class SarifArtifactLocation(SarifModel):
    uri: str | None = None
    uri_base_id: str | None = Field(default=None, alias="uriBaseId")
    index: int | None = Field(default=None, ge=0)


class SarifRegion(SarifModel):
    start_line: int | None = Field(default=None, alias="startLine", ge=1)
    start_column: int | None = Field(default=None, alias="startColumn", ge=1)
    end_line: int | None = Field(default=None, alias="endLine", ge=1)
    end_column: int | None = Field(default=None, alias="endColumn", ge=1)
    char_offset: int | None = Field(default=None, alias="charOffset", ge=0)
    char_length: int | None = Field(default=None, alias="charLength", ge=0)


class SarifPhysicalLocation(SarifModel):
    artifact_location: SarifArtifactLocation | None = Field(default=None, alias="artifactLocation")
    region: SarifRegion | None = None


class SarifLogicalLocation(SarifModel):
    name: str | None = None
    fully_qualified_name: str | None = Field(default=None, alias="fullyQualifiedName")
    kind: str | None = None


class SarifLocation(SarifModel):
    physical_location: SarifPhysicalLocation | None = Field(default=None, alias="physicalLocation")
    logical_locations: list[SarifLogicalLocation] = Field(default_factory=list, alias="logicalLocations")
    message: SarifMessage | None = None


class SarifSuppression(SarifModel):
    kind: Literal["inSource", "external"]
    status: Literal["accepted", "underReview", "rejected"] | None = None
    justification: str | None = None


class SarifArtifactContent(SarifModel):
    text: str | None = None


class SarifReplacement(SarifModel):
    deleted_region: SarifRegion = Field(alias="deletedRegion")
    inserted_content: SarifArtifactContent | None = Field(default=None, alias="insertedContent")


class SarifArtifactChange(SarifModel):
    artifact_location: SarifArtifactLocation = Field(alias="artifactLocation")
    replacements: list[SarifReplacement] = Field(min_length=1)


class SarifFix(SarifModel):
    description: SarifMessage | None = None
    artifact_changes: list[SarifArtifactChange] = Field(min_length=1, alias="artifactChanges")


class SarifReportingConfiguration(SarifModel):
    level: Literal["none", "note", "warning", "error"] | None = None


class SarifReportingDescriptor(SarifModel):
    rule_id: str = Field(alias="id", min_length=1)
    name: str | None = None
    short_description: SarifMessage | None = Field(default=None, alias="shortDescription")
    full_description: SarifMessage | None = Field(default=None, alias="fullDescription")
    help_uri: str | None = Field(default=None, alias="helpUri")
    default_configuration: SarifReportingConfiguration | None = Field(
        default=None,
        alias="defaultConfiguration",
    )
    message_strings: dict[str, SarifMessage] = Field(default_factory=dict, alias="messageStrings")
    properties: dict[str, Any] = Field(default_factory=dict)


class SarifToolComponent(SarifModel):
    name: str = Field(min_length=1)
    version: str | None = None
    semantic_version: str | None = Field(default=None, alias="semanticVersion")
    information_uri: str | None = Field(default=None, alias="informationUri")
    rules: list[SarifReportingDescriptor] = Field(default_factory=list)


class SarifTool(SarifModel):
    driver: SarifToolComponent
    extensions: list[SarifToolComponent] = Field(default_factory=list)


class SarifInvocation(SarifModel):
    execution_successful: bool = Field(alias="executionSuccessful")
    exit_code: int | None = Field(default=None, alias="exitCode")
    exit_code_description: str | None = Field(default=None, alias="exitCodeDescription")
    process_start_failure_message: str | None = Field(default=None, alias="processStartFailureMessage")
    command_line: str | None = Field(default=None, alias="commandLine")
    arguments: list[str] = Field(default_factory=list)
    working_directory: SarifArtifactLocation | None = Field(default=None, alias="workingDirectory")


class SarifArtifact(SarifModel):
    location: SarifArtifactLocation | None = None
    source_language: str | None = Field(default=None, alias="sourceLanguage")
    hashes: dict[str, str] = Field(default_factory=dict)
    roles: list[str] = Field(default_factory=list)


class SarifVersionControlDetails(SarifModel):
    repository_uri: str = Field(alias="repositoryUri", min_length=1)
    revision_id: str | None = Field(default=None, alias="revisionId")
    branch: str | None = None


class SarifRunAutomationDetails(SarifModel):
    automation_id: str | None = Field(default=None, alias="id")
    guid: str | None = None


class SarifResult(SarifModel):
    rule_id: str | None = Field(default=None, alias="ruleId")
    rule_index: int | None = Field(default=None, alias="ruleIndex", ge=0)
    message: SarifMessage
    level: Literal["none", "note", "warning", "error"] | None = None
    kind: Literal["notApplicable", "pass", "fail", "review", "open", "informational"] | None = None
    baseline_state: Literal["new", "unchanged", "updated", "absent"] | None = Field(
        default=None,
        alias="baselineState",
    )
    locations: list[SarifLocation] = Field(default_factory=list)
    related_locations: list[SarifLocation] = Field(default_factory=list, alias="relatedLocations")
    suppressions: list[SarifSuppression] | None = None
    fixes: list[SarifFix] = Field(default_factory=list)
    fingerprints: dict[str, str] = Field(default_factory=dict)
    partial_fingerprints: dict[str, str] = Field(default_factory=dict, alias="partialFingerprints")
    properties: dict[str, Any] = Field(default_factory=dict)


class SarifRun(SarifModel):
    tool: SarifTool
    invocations: list[SarifInvocation] = Field(default_factory=list)
    artifacts: list[SarifArtifact] = Field(default_factory=list)
    results: list[SarifResult] | None = None
    version_control_provenance: list[SarifVersionControlDetails] = Field(
        default_factory=list,
        alias="versionControlProvenance",
    )
    automation_details: SarifRunAutomationDetails | None = Field(default=None, alias="automationDetails")
    original_uri_base_ids: dict[str, SarifArtifactLocation] = Field(
        default_factory=dict,
        alias="originalUriBaseIds",
    )
    column_kind: Literal["utf16CodeUnits", "unicodeCodePoints"] | None = Field(
        default=None,
        alias="columnKind",
    )
    redaction_tokens: list[str] = Field(default_factory=list, alias="redactionTokens")
    properties: dict[str, Any] = Field(default_factory=dict)


class SarifLog(SarifModel):
    schema_uri: str | None = Field(default=None, alias="$schema")
    version: Literal["2.1.0"]
    runs: list[SarifRun]


class SarifArtifactBinding(EvidenceModel):
    """Trusted acquisition metadata binding otherwise portable SARIF to one state."""

    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository_id: str = Field(min_length=1)
    source_state: SourceStateRecord
    configuration_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_repository(self) -> SarifArtifactBinding:
        if self.source_state.repository_id != self.repository_id:
            raise ValueError("SARIF binding source state belongs to another repository")
        return self


class DeclaredDiagnosticContract(EvidenceModel):
    """Caller-owned contract that a diagnostic rule may report against."""

    kind: ContractKind
    summary: str = Field(min_length=1)
    terms_digest: str = Field(min_length=1)


def canonical_sarif_bytes(log: SarifLog) -> bytes:
    """Serialize SARIF deterministically without reordering index-bearing arrays."""
    return canonical_ordered_json_bytes(
        log.model_dump(mode="json", by_alias=True, exclude_none=True)
    )


def sarif_digest(log: SarifLog) -> str:
    """Return the canonical content identity of one parsed SARIF log."""
    return sha256_digest(canonical_sarif_bytes(log))


def build_sarif_binding(
    log: SarifLog,
    *,
    source_state: SourceStateRecord,
    configuration_digest: str,
) -> SarifArtifactBinding:
    """Bind one immutable parsed log to trusted acquisition context."""
    return SarifArtifactBinding(
        artifact_digest=sarif_digest(log),
        repository_id=source_state.repository_id,
        source_state=source_state,
        configuration_digest=configuration_digest,
    )
