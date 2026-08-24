"""Explicit source snapshots and deterministic dossier slicing."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from anatomize._artifacts import sha256_digest
from anatomize.dossiers.models import ContentRange, DossierContent, SourceSlicePolicy, build_dossier_content
from anatomize.evidence import ContentClass, EvidenceModel, validate_repository_path


class DossierSource(EvidenceModel):
    """Caller-authorized source bytes bound to one repository state and path."""

    source_state_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    language: str | None = None
    content_class: ContentClass = ContentClass.ORDINARY
    text: str | None = None
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_source(self) -> DossierSource:
        validate_repository_path(self.path)
        if self.text is not None:
            raw = self.text.encode("utf-8")
            if self.size_bytes != len(raw):
                raise ValueError("dossier source size does not match its UTF-8 content")
            if self.digest != sha256_digest(raw):
                raise ValueError("dossier source digest does not match its UTF-8 content")
        return self


def build_dossier_source(
    *,
    source_state_id: str,
    path: str,
    media_type: str,
    text: str | None,
    digest: str | None = None,
    size_bytes: int | None = None,
    language: str | None = None,
    content_class: ContentClass = ContentClass.ORDINARY,
) -> DossierSource:
    """Build an exact source snapshot without granting ambient file access."""
    raw = text.encode("utf-8") if text is not None else None
    if raw is not None:
        digest = digest or sha256_digest(raw)
        size_bytes = len(raw) if size_bytes is None else size_bytes
    if digest is None or size_bytes is None:
        raise ValueError("content-free dossier sources require an explicit digest and size")
    return DossierSource(
        source_state_id=source_state_id,
        path=path,
        digest=digest,
        media_type=media_type,
        language=language,
        content_class=content_class,
        text=text,
        size_bytes=size_bytes,
    )


def slice_dossier_source(
    source: DossierSource,
    *,
    start_line: int | None,
    start_column: int | None,
    end_line: int | None,
    end_column: int | None,
    enclosing_entity_id: str | None,
    policy: SourceSlicePolicy,
    extra_context: int = 0,
    complete_file: bool = False,
    max_text_bytes: int | None = None,
) -> DossierContent:
    """Create one stable, bounded source slice from an authorized snapshot."""
    if source.text is None:
        return build_dossier_content(
            digest=source.digest,
            full_content_digest=source.digest,
            source_state_id=source.source_state_id,
            path=source.path,
            external_locator=None,
            content_class=source.content_class,
            media_type=source.media_type,
            language=source.language,
            source_range=None,
            enclosing_entity_id=enclosing_entity_id,
            context_before=0,
            context_after=0,
            text=None,
            truncated=False,
        )

    lines = source.text.splitlines(keepends=True)
    if complete_file or start_line is None:
        first = 1
        last = max(1, len(lines))
        before = 0
        after = 0
    else:
        target_last = end_line or start_line
        requested_before = policy.context_before + extra_context
        requested_after = policy.context_after + extra_context
        first = max(1, start_line - requested_before)
        last = min(max(1, len(lines)), target_last + requested_after)
        before = start_line - first
        after = last - target_last
    full_slice = "".join(lines[first - 1 : last]) if lines else ""
    full_slice_raw = full_slice.encode("utf-8")
    included = full_slice if policy.inline_content else None
    truncated = False
    if included is not None and max_text_bytes is not None and len(full_slice_raw) > max_text_bytes:
        included = _utf8_prefix(full_slice_raw, max_text_bytes)
        truncated = True
    final_line = lines[last - 1].rstrip("\r\n") if lines else ""
    source_range = ContentRange(
        start_line=first,
        start_column=0 if first != start_line else (start_column or 0),
        end_line=last,
        end_column=len(final_line) if last != end_line else (end_column or len(final_line)),
    )
    return build_dossier_content(
        digest=sha256_digest(full_slice_raw),
        full_content_digest=source.digest,
        source_state_id=source.source_state_id,
        path=source.path,
        external_locator=None,
        content_class=source.content_class,
        media_type=source.media_type,
        language=source.language,
        source_range=source_range,
        enclosing_entity_id=enclosing_entity_id,
        context_before=before,
        context_after=after,
        text=included,
        truncated=truncated,
    )


def source_identity(source: DossierSource) -> tuple[str, str]:
    """Return the unique state/path key used by a dossier context."""
    return source.source_state_id, source.path


def _utf8_prefix(raw: bytes, maximum: int) -> str:
    value = raw[:maximum]
    while value:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            value = value[: error.start]
    return ""


def dossier_source_json_schema() -> dict[str, Any]:
    """Return the generated schema for explicit source snapshots."""
    return DossierSource.model_json_schema(mode="serialization")
