"""Static notebook/executable-document inventory and separate run evidence."""

from __future__ import annotations

import ast
import re
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from anatomize._artifacts import BoundedJsonError, JsonLimits, content_id, parse_bounded_json_object, sha256_digest
from anatomize._errors import AnatomizeError
from anatomize.evidence import ContentClass, EvidenceModel, RepositoryEvidence, validate_repository_path
from anatomize.providers import ProviderEnvelope

NOTEBOOK_ARTIFACT_TYPE: Literal["anatomize.notebook"] = "anatomize.notebook"
NOTEBOOK_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
NOTEBOOK_EXECUTION_TYPE: Literal["anatomize.notebook-execution"] = "anatomize.notebook-execution"
NOTEBOOK_EXECUTION_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class NotebookArtifactError(AnatomizeError):
    """Stable failure while importing an executable-document artifact."""


class NotebookCellKind(str, Enum):
    CODE = "code"
    MARKDOWN = "markdown"
    RAW = "raw"


class CellIdentityStrength(str, Enum):
    EXACT = "exact"
    CANDIDATE = "candidate"


class OutputState(str, Enum):
    CLEARED = "cleared"
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class NotebookLocator(EvidenceModel):
    path: str
    cell_id: str
    ordinal: int = Field(ge=0)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_locator(self) -> NotebookLocator:
        validate_repository_path(self.path)
        if self.end_line < self.start_line:
            raise ValueError("notebook source range is reversed")
        return self


class EmbeddedInventory(EvidenceModel):
    inventory_id: str
    name: str | None = None
    media_types: list[str]
    byte_size: int = Field(ge=0)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_class: ContentClass
    content_included: Literal[False] = False


class NotebookCell(EvidenceModel):
    cell_key: str
    native_cell_id: str | None = None
    identity_strength: CellIdentityStrength
    kind: NotebookCellKind
    locator: NotebookLocator
    language: str | None = None
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_preview: str = Field(max_length=2_048)
    execution_count: int | None = Field(default=None, ge=0)
    parameter: bool = False
    hidden_source: bool = False
    hidden_outputs: bool = False
    definitions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    output_state: OutputState
    outputs: list[EmbeddedInventory] = Field(default_factory=list)
    attachments: list[EmbeddedInventory] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cell(self) -> NotebookCell:
        if self.cell_key != _cell_key(self):
            raise ValueError("notebook cell key does not match stable source identity")
        if self.output_state is OutputState.CLEARED and self.outputs:
            raise ValueError("cleared cell cannot contain output inventory")
        return self


class NotebookArtifact(EvidenceModel):
    artifact_type: Literal["anatomize.notebook"] = NOTEBOOK_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = NOTEBOOK_SCHEMA_VERSION
    repository_id: str
    source_state_id: str
    path: str
    document_format: Literal["jupyter", "quarto", "rmarkdown"]
    default_language: str | None = None
    document_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cells: list[NotebookCell]
    limitations: list[str]

    @model_validator(mode="after")
    def validate_artifact(self) -> NotebookArtifact:
        validate_repository_path(self.path)
        keys = [item.cell_key for item in self.cells]
        if len(keys) != len(set(keys)):
            raise ValueError("notebook cell keys must be unique")
        native = [item.native_cell_id for item in self.cells if item.native_cell_id is not None]
        if len(native) != len(set(native)):
            raise ValueError("notebook native cell ids must be unique")
        return self


class NotebookExecutionObservation(EvidenceModel):
    cell_key: str
    status: Literal["passed", "failed", "skipped", "unknown"]
    execution_count: int | None = Field(default=None, ge=0)
    output_digests: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class NotebookExecutionArtifact(EvidenceModel):
    artifact_type: Literal["anatomize.notebook-execution"] = NOTEBOOK_EXECUTION_TYPE
    schema_version: Literal["1.0.0"] = NOTEBOOK_EXECUTION_SCHEMA_VERSION
    repository_id: str
    source_state_id: str
    notebook_document_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_run_id: str
    provider_id: str
    provider_version: str
    environment_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["complete", "partial", "failed", "unavailable"]
    observations: list[NotebookExecutionObservation]
    limitations: list[str] = Field(default_factory=list)
    source_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CellDeltaKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MOVED = "moved"
    REORDERED = "reordered"
    SPLIT = "split"
    MERGED = "merged"
    OUTPUT_ONLY = "output_only"
    SOURCE_CHANGED = "source_changed"
    UNCHANGED = "unchanged"


class NotebookCellDelta(EvidenceModel):
    delta_id: str
    kinds: list[CellDeltaKind]
    before_cell_keys: list[str]
    after_cell_keys: list[str]
    lineage: Literal["exact", "candidate", "unavailable"]
    invalidates_execution: bool
    reason: str


def parse_jupyter_notebook(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    path: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> NotebookArtifact:
    """Parse bounded nbformat JSON without exposing output or attachment content."""
    validate_repository_path(path)
    payload = _bounded_json(raw, max_bytes, "notebook")
    cells = payload.get("cells")
    if not isinstance(cells, list) or not isinstance(payload.get("nbformat"), int):
        raise NotebookArtifactError(
            "notebook_shape_invalid",
            "Jupyter notebook requires nbformat and cells",
            remediation="Validate the notebook with nbformat and retry.",
        )
    metadata = _mapping(payload.get("metadata"))
    kernelspec = _mapping(metadata.get("kernelspec"))
    language_info = _mapping(metadata.get("language_info"))
    default_language = _string(language_info.get("name")) or _string(kernelspec.get("language"))
    parsed: list[NotebookCell] = []
    limitations: set[str] = set()
    for ordinal, value in enumerate(cells):
        if not isinstance(value, dict):
            limitations.add(f"Cell {ordinal} is not an object and was omitted.")
            continue
        parsed.append(
            _jupyter_cell(
                value,
                path=path,
                ordinal=ordinal,
                default_language=default_language,
                limitations=limitations,
            )
        )
    limitations.add("Notebook outputs are mutable inventory and never source truth or execution proof.")
    return NotebookArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        path=path,
        document_format="jupyter",
        default_language=default_language,
        document_digest=sha256_digest(raw),
        cells=parsed,
        limitations=sorted(limitations),
    )


def parse_executable_document(
    source: str,
    *,
    repository_id: str,
    source_state_id: str,
    path: str,
) -> NotebookArtifact:
    """Inventory Quarto/R Markdown prose and executable fences without rendering."""
    validate_repository_path(path)
    lowered = path.casefold()
    document_format: Literal["quarto", "rmarkdown"] = "quarto" if lowered.endswith(".qmd") else "rmarkdown"
    lines = source.splitlines()
    cells: list[NotebookCell] = []
    prose_start = 0
    ordinal = 0
    index = 0
    while index < len(lines):
        match = re.match(r"^```+\{([A-Za-z0-9_+-]+)(?:[\s,]+([^}]+))?\}\s*$", lines[index])
        if match is None:
            index += 1
            continue
        if index > prose_start:
            prose = "\n".join(lines[prose_start:index]).strip()
            if prose:
                cells.append(_text_cell(prose, path=path, ordinal=ordinal, start=prose_start + 1, end=index))
                ordinal += 1
        language, inline_options = match.groups()
        start = index + 1
        index += 1
        body_start = index
        while index < len(lines) and not lines[index].startswith("```"):
            index += 1
        body = "\n".join(lines[body_start:index])
        label_match = re.search(r"(?m)^#\|\s*label:\s*([\w.-]+)\s*$", body)
        inline_label = None
        if inline_options:
            candidate_label = inline_options.split(",", maxsplit=1)[0].strip()
            if candidate_label and "=" not in candidate_label:
                inline_label = candidate_label
        native_id = label_match.group(1) if label_match else inline_label
        cells.append(
            _build_cell(
                path=path,
                ordinal=ordinal,
                native_cell_id=native_id,
                kind=NotebookCellKind.CODE,
                language=language.casefold(),
                source=body,
                start_line=start + 1,
                end_line=max(start + 1, index),
                execution_count=None,
                parameter=bool(
                    re.search(r"(?m)^#\|\s*parameters?:", body) or re.search(r"(?m)^#\|\s*tags:.*parameters", body)
                ),
                hidden_source=bool(re.search(r"(?m)^#\|\s*(echo|include):\s*false", body)),
                hidden_outputs=bool(re.search(r"(?m)^#\|\s*(output|include):\s*(false|none)", body)),
                output_state=OutputState.CLEARED,
                outputs=[],
                attachments=[],
            )
        )
        ordinal += 1
        index += 1
        prose_start = index
    if prose_start < len(lines):
        prose = "\n".join(lines[prose_start:]).strip()
        if prose:
            cells.append(_text_cell(prose, path=path, ordinal=ordinal, start=prose_start + 1, end=max(1, len(lines))))
    return NotebookArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        path=path,
        document_format=document_format,
        default_language=None,
        document_digest=sha256_digest(source.encode()),
        cells=cells,
        limitations=[
            "Executable-document source is inventory; rendering and execution require a separate provider artifact."
        ],
    )


def parse_notebook_execution(
    raw: bytes,
    *,
    repository_id: str,
    source_state_id: str,
    notebook_document_digest: str,
    provider_run_id: str,
    provider_id: str,
    provider_version: str,
    environment_digest: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> NotebookExecutionArtifact:
    """Parse a bounded provider-neutral execution summary, never notebook source."""
    payload = _bounded_json(raw, max_bytes, "notebook execution")
    records = payload.get("cells")
    status = payload.get("status")
    if not isinstance(records, list) or status not in {"complete", "partial", "failed", "unavailable"}:
        raise NotebookArtifactError(
            "notebook_execution_shape_invalid",
            "Execution artifact requires status and cells",
            remediation="Export a supported execution summary.",
        )
    observations = []
    for value in records:
        if not isinstance(value, dict) or not isinstance(value.get("cell_key"), str):
            continue
        output_digests = value.get("output_digests", [])
        observations.append(
            NotebookExecutionObservation(
                cell_key=value["cell_key"],
                status=str(value.get("status", "unknown")),
                execution_count=value.get("execution_count")
                if isinstance(value.get("execution_count"), int)
                else None,
                output_digests=[item for item in output_digests if isinstance(item, str)],
                error_type=_string(value.get("error_type")),
                error_digest=_string(value.get("error_digest")),
            )
        )
    return NotebookExecutionArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        notebook_document_digest=notebook_document_digest,
        provider_run_id=provider_run_id,
        provider_id=provider_id,
        provider_version=provider_version,
        environment_digest=environment_digest,
        status=status,
        observations=observations,
        limitations=["Execution applies only to the captured source, environment, selection, and provider run."],
        source_artifact_digest=sha256_digest(raw),
    )


def compare_notebooks(before: NotebookArtifact, after: NotebookArtifact) -> list[NotebookCellDelta]:
    """Compare cell source/order/output with exact ids and conservative fallback lineage."""
    deltas: list[NotebookCellDelta] = []
    before_native = {item.native_cell_id: item for item in before.cells if item.native_cell_id is not None}
    after_native = {item.native_cell_id: item for item in after.cells if item.native_cell_id is not None}
    matched_before: set[str] = set()
    matched_after: set[str] = set()
    for native_id in sorted(set(before_native) & set(after_native)):
        left, right = before_native[native_id], after_native[native_id]
        kinds: list[CellDeltaKind] = []
        if left.locator.ordinal != right.locator.ordinal:
            kinds.append(CellDeltaKind.REORDERED)
        if left.source_digest != right.source_digest:
            kinds.append(CellDeltaKind.SOURCE_CHANGED)
        elif [item.digest for item in left.outputs] != [item.digest for item in right.outputs]:
            kinds.append(CellDeltaKind.OUTPUT_ONLY)
        if not kinds:
            kinds.append(CellDeltaKind.UNCHANGED)
        deltas.append(
            _delta(
                kinds,
                [left],
                [right],
                "exact",
                bool({CellDeltaKind.SOURCE_CHANGED, CellDeltaKind.REORDERED}.intersection(kinds)),
                "Native cell id.",
            )
        )
        matched_before.add(left.cell_key)
        matched_after.add(right.cell_key)
    remaining_before = [item for item in before.cells if item.cell_key not in matched_before]
    remaining_after = [item for item in after.cells if item.cell_key not in matched_after]
    for left in list(remaining_before):
        match = next(
            (
                item
                for item in remaining_after
                if item.locator.path == left.locator.path
                and item.locator.ordinal == left.locator.ordinal
                and item.kind is left.kind
            ),
            None,
        )
        if match is None:
            continue
        kinds = (
            [CellDeltaKind.SOURCE_CHANGED]
            if left.source_digest != match.source_digest
            else [CellDeltaKind.OUTPUT_ONLY]
            if [item.digest for item in left.outputs] != [item.digest for item in match.outputs]
            else [CellDeltaKind.UNCHANGED]
        )
        deltas.append(
            _delta(
                kinds,
                [left],
                [match],
                "candidate",
                CellDeltaKind.SOURCE_CHANGED in kinds,
                "Stable path, ordinal, and cell kind without a native cell id.",
            )
        )
        remaining_before.remove(left)
        remaining_after.remove(match)
    for left in list(remaining_before):
        match = next((item for item in remaining_after if item.source_digest == left.source_digest), None)
        if match is None:
            continue
        kind = CellDeltaKind.MOVED if left.locator.path != match.locator.path else CellDeltaKind.REORDERED
        deltas.append(_delta([kind], [left], [match], "candidate", True, "Equal source digest without stable id."))
        remaining_before.remove(left)
        remaining_after.remove(match)
    deltas.extend(
        _delta([CellDeltaKind.REMOVED], [item], [], "unavailable", True, "No successor cell.")
        for item in remaining_before
    )
    deltas.extend(
        _delta([CellDeltaKind.ADDED], [], [item], "unavailable", True, "No predecessor cell.")
        for item in remaining_after
    )
    return sorted(deltas, key=lambda item: item.delta_id)


def notebook_execution_provider_envelope(
    artifact: NotebookExecutionArtifact,
    *,
    notebook: NotebookArtifact,
    baseline: RepositoryEvidence,
    configuration_digest: str,
    policy_digest: str,
) -> ProviderEnvelope:
    """Project captured cell execution through the common provider ABI."""
    from anatomize.evidence import (
        CompletenessRecord,
        CompletenessStatus,
        EvidenceStrength,
        FileEntity,
        ObservationStance,
        ProviderRunStatus,
        RuntimeEntity,
        RuntimeObservation,
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

    if (
        artifact.repository_id != notebook.repository_id
        or artifact.source_state_id != notebook.source_state_id
        or artifact.notebook_document_digest != notebook.document_digest
    ):
        raise ValueError("notebook execution does not match the selected static notebook")
    state = next(item for item in baseline.states if item.state_id == notebook.source_state_id)
    subject = next(
        (
            item
            for item in baseline.entities
            if isinstance(item, FileEntity) and item.source_state_id == state.state_id and item.path == notebook.path
        ),
        None,
    )
    if subject is None:
        raise ValueError("notebook execution requires its file in baseline evidence")
    builder = ProviderBatchBuilder(baseline)
    builder.repository_entity(state)
    builder.include_baseline_entity(subject)
    completeness_id = content_id(
        "completeness:notebook-execution",
        {"run": artifact.provider_run_id, "notebook": notebook.document_digest},
    )
    for observation in artifact.observations:
        runtime = RuntimeEntity(
            entity_id=content_id(
                "entity:notebook-cell-run",
                {"run": artifact.provider_run_id, "cell": observation.cell_key},
            ),
            source_state_id=state.state_id,
            display_name=f"cell {observation.cell_key}: {observation.status}",
            runtime_kind="notebook_cell_execution",
            status=observation.status,
            run_identity=artifact.provider_run_id,
        )
        builder.entities[runtime.entity_id] = runtime
        normalized = RuntimeObservation(
            observation_id=content_id(
                "observation:notebook-cell-run",
                {"run": artifact.provider_run_id, "cell": observation.model_dump(mode="json")},
            ),
            source_state_id=state.state_id,
            provider_run_id=artifact.provider_run_id,
            method=artifact.provider_id,
            method_version=artifact.provider_version,
            strength=EvidenceStrength.EXACT,
            stance=ObservationStance.QUALIFIES,
            completeness_id=completeness_id,
            rationale="Captured execution qualifies one cell under an exact source and environment.",
            runtime_entity_id=runtime.entity_id,
            subject_entity_ids=[subject.entity_id],
            outcome=observation.status,
            metrics={
                "cell_key": observation.cell_key,
                "execution_count": observation.execution_count or 0,
                "output_count": len(observation.output_digests),
                "environment_digest": artifact.environment_digest,
                **({"error_type": observation.error_type} if observation.error_type else {}),
            },
        )
        builder.observations[normalized.observation_id] = normalized
    completeness_status = {
        "complete": CompletenessStatus.COMPLETE,
        "partial": CompletenessStatus.PARTIAL,
        "failed": CompletenessStatus.PARTIAL,
        "unavailable": CompletenessStatus.UNAVAILABLE,
    }[artifact.status]
    completeness = CompletenessRecord(
        completeness_id=completeness_id,
        source_state_id=state.state_id,
        provider_run_id=artifact.provider_run_id,
        scope_type="entity",
        scope_id=subject.entity_id,
        evidence_families=["notebook_execution"],
        status=completeness_status,
    )
    payload = builder.build_payload([completeness])
    provider_status = (
        ProviderRunStatus.COMPLETE
        if artifact.status == "complete"
        else ProviderRunStatus.UNAVAILABLE
        if artifact.status == "unavailable"
        else ProviderRunStatus.PARTIAL
    )
    return build_provider_envelope(
        provider_run_id=artifact.provider_run_id,
        provider_id=artifact.provider_id,
        provider_version=artifact.provider_version,
        tool=ProviderToolIdentity(name=artifact.provider_id, version=artifact.provider_version),
        capabilities=["notebook_execution"],
        languages=sorted({item.language for item in notebook.cells if item.language is not None}),
        repository_id=artifact.repository_id,
        source_states=[state],
        primary_source_state_id=state.state_id,
        configuration_digest=configuration_digest,
        scope=ProviderScope(
            scope_id=content_id(
                "provider-scope:notebook-execution",
                {"run": artifact.provider_run_id},
            ),
            source_state_ids=[state.state_id],
            paths=[notebook.path],
            entity_ids=sorted(builder.entities),
            evidence_families=["notebook_execution"],
        ),
        invocation=InvocationAuthority(
            mode=InvocationMode.ARTIFACT_IMPORT,
            level=AuthorityLevel.A1_ARTIFACT,
            policy_digest=policy_digest,
        ),
        status=provider_status,
        payload=payload,
    )


def _jupyter_cell(
    value: dict[str, Any],
    *,
    path: str,
    ordinal: int,
    default_language: str | None,
    limitations: set[str],
) -> NotebookCell:
    source = _multiline(value.get("source"))
    raw_kind = value.get("cell_type")
    kind = (
        NotebookCellKind(raw_kind) if raw_kind in {item.value for item in NotebookCellKind} else NotebookCellKind.RAW
    )
    metadata = _mapping(value.get("metadata"))
    native_id = (
        value.get("id")
        if isinstance(value.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["id"])
        else None
    )
    if native_id is None:
        limitations.add(f"Cell {ordinal} has no valid stable id; lineage is candidate-strength.")
    tags = _sequence(metadata.get("tags"))
    jupyter = _mapping(metadata.get("jupyter"))
    outputs_raw = _sequence(value.get("outputs"))
    outputs = [_embedded(item, name=None, sensitive=bool(metadata.get("anatomize_sensitive"))) for item in outputs_raw]
    attachments_raw = _mapping(value.get("attachments"))
    attachments = [
        _embedded(item, name=name, sensitive=bool(metadata.get("anatomize_sensitive")))
        for name, item in sorted(attachments_raw.items())
    ]
    source_digest = sha256_digest(source.encode())
    recorded_source = _string(metadata.get("anatomize_output_source_digest"))
    output_state = (
        OutputState.CLEARED
        if not outputs
        else OutputState.CURRENT
        if recorded_source == source_digest
        else OutputState.STALE
        if recorded_source is not None
        else OutputState.UNKNOWN
    )
    return _build_cell(
        path=path,
        ordinal=ordinal,
        native_cell_id=native_id,
        kind=kind,
        language=default_language if kind is NotebookCellKind.CODE else None,
        source=source,
        start_line=1,
        end_line=max(1, len(source.splitlines())),
        execution_count=value.get("execution_count") if isinstance(value.get("execution_count"), int) else None,
        parameter="parameters" in tags,
        hidden_source=bool(jupyter.get("source_hidden") or metadata.get("source_hidden")),
        hidden_outputs=bool(jupyter.get("outputs_hidden") or metadata.get("outputs_hidden")),
        output_state=output_state,
        outputs=outputs,
        attachments=attachments,
    )


def _text_cell(source: str, *, path: str, ordinal: int, start: int, end: int) -> NotebookCell:
    return _build_cell(
        path=path,
        ordinal=ordinal,
        native_cell_id=None,
        kind=NotebookCellKind.MARKDOWN,
        language=None,
        source=source,
        start_line=start,
        end_line=end,
        execution_count=None,
        parameter=False,
        hidden_source=False,
        hidden_outputs=False,
        output_state=OutputState.CLEARED,
        outputs=[],
        attachments=[],
    )


def _build_cell(
    *,
    path: str,
    ordinal: int,
    native_cell_id: str | None,
    kind: NotebookCellKind,
    language: str | None,
    source: str,
    start_line: int,
    end_line: int,
    execution_count: int | None,
    parameter: bool,
    hidden_source: bool,
    hidden_outputs: bool,
    output_state: OutputState,
    outputs: list[EmbeddedInventory],
    attachments: list[EmbeddedInventory],
) -> NotebookCell:
    source_digest = sha256_digest(source.encode())
    locator_id = native_cell_id or f"candidate-{source_digest.removeprefix('sha256:')[:12]}-{ordinal}"
    locator = NotebookLocator(
        path=path,
        cell_id=locator_id,
        ordinal=ordinal,
        start_line=start_line,
        end_line=end_line,
    )
    definitions, references = _source_semantics(source, language, kind)
    provisional = NotebookCell.model_construct(
        cell_key="pending",
        native_cell_id=native_cell_id,
        identity_strength=CellIdentityStrength.EXACT if native_cell_id else CellIdentityStrength.CANDIDATE,
        kind=kind,
        locator=locator,
        language=language,
        source_digest=source_digest,
        source_preview=source[:2_048],
        execution_count=execution_count,
        parameter=parameter,
        hidden_source=hidden_source,
        hidden_outputs=hidden_outputs,
        definitions=definitions,
        references=references,
        output_state=output_state,
        outputs=outputs,
        attachments=attachments,
    )
    return NotebookCell(
        cell_key=_cell_key(provisional),
        native_cell_id=native_cell_id,
        identity_strength=CellIdentityStrength.EXACT if native_cell_id else CellIdentityStrength.CANDIDATE,
        kind=kind,
        locator=locator,
        language=language,
        source_digest=source_digest,
        source_preview=source[:2_048],
        execution_count=execution_count,
        parameter=parameter,
        hidden_source=hidden_source,
        hidden_outputs=hidden_outputs,
        definitions=definitions,
        references=references,
        output_state=output_state,
        outputs=outputs,
        attachments=attachments,
    )


def _source_semantics(source: str, language: str | None, kind: NotebookCellKind) -> tuple[list[str], list[str]]:
    if kind is NotebookCellKind.MARKDOWN:
        return [], sorted(set(re.findall(r"`([A-Za-z_][\w.:]*)\(?\)?`", source)))
    if language == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return [], []
        definitions = sorted(
            item.name
            for item in ast.walk(tree)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        references = sorted(
            {item.id for item in ast.walk(tree) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)}
        )
        return definitions, references
    if language in {"r", "R"}:
        definitions = sorted(set(re.findall(r"(?m)^\s*([A-Za-z.][\w.]*)\s*<-", source)))
        references = sorted(set(re.findall(r"\b([A-Za-z.][\w.]*)\s*\(", source)))
        return definitions, references
    return [], []


def _embedded(value: Any, *, name: str | None, sensitive: bool) -> EmbeddedInventory:
    from anatomize._artifacts import canonical_ordered_json_bytes

    raw = canonical_ordered_json_bytes(value)
    mapping = _mapping(value)
    data = _mapping(mapping.get("data"))
    media_types = (
        sorted(data)
        if data
        else sorted(key for key in mapping if "/" in key)
        if any("/" in key for key in mapping)
        else ["text/plain"]
        if mapping.get("output_type") == "stream"
        else ["application/x.notebook-error"]
        if mapping.get("output_type") == "error"
        else []
    )
    content_class = (
        ContentClass.SENSITIVE
        if sensitive
        else ContentClass.LARGE
        if len(raw) > 65_536
        else ContentClass.BINARY
        if any(not item.startswith(("text/", "application/json")) for item in media_types)
        else ContentClass.GENERATED
    )
    values = {
        "name": name,
        "media_types": media_types,
        "byte_size": len(raw),
        "digest": sha256_digest(raw),
        "content_class": content_class,
    }
    return EmbeddedInventory(inventory_id=content_id("embedded-inventory", values), **values)


def _cell_key(cell: NotebookCell) -> str:
    return content_id(
        "notebook-cell",
        {
            "path": cell.locator.path,
            "native_cell_id": cell.native_cell_id,
            "fallback": None
            if cell.native_cell_id is not None
            else {"source_digest": cell.source_digest, "ordinal": cell.locator.ordinal},
        },
    )


def _delta(
    kinds: list[CellDeltaKind],
    before: list[NotebookCell],
    after: list[NotebookCell],
    lineage: Literal["exact", "candidate", "unavailable"],
    invalidates_execution: bool,
    reason: str,
) -> NotebookCellDelta:
    values = {
        "kinds": kinds,
        "before_cell_keys": [item.cell_key for item in before],
        "after_cell_keys": [item.cell_key for item in after],
        "lineage": lineage,
        "invalidates_execution": invalidates_execution,
        "reason": reason,
    }
    return NotebookCellDelta(
        delta_id=content_id(
            "notebook-cell-delta",
            {**values, "kinds": [item.value for item in kinds]},
        ),
        **values,
    )


def _concatenated_matches(source: str, candidates: list[NotebookCell]) -> list[NotebookCell]:
    normalized = _normalize_source(source)
    for start in range(len(candidates)):
        selected: list[NotebookCell] = []
        combined = ""
        for item in candidates[start:]:
            selected.append(item)
            combined += _normalize_source(item.source_preview)
            if combined == normalized:
                return selected
            if len(combined) > len(normalized):
                break
    return []


def _normalize_source(source: str) -> str:
    return "".join(source.split())


def _bounded_json(raw: bytes, maximum: int, label: str) -> dict[str, Any]:
    try:
        return parse_bounded_json_object(
            raw,
            limits=JsonLimits(max_bytes=maximum, max_depth=96, max_values=2_000_000, max_string_bytes=maximum),
        )
    except BoundedJsonError as error:
        raise NotebookArtifactError(
            f"{label.replace(' ', '_')}_{error.code}",
            f"{label.title()} artifact {error}",
            remediation=f"Regenerate bounded {label} JSON.",
        ) from error


def _multiline(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "".join(value)
    return ""


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
