"""JSONL streaming output for `anatomize pack`."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from anatomize.pack.formats import ContentEncoding, PackPayload
from anatomize.pack.mode import PackMode
from anatomize.pack.representations import FileRepresentation


@dataclass(frozen=True)
class JsonlFile:
    path: str
    language: str | None
    is_binary: bool
    size_bytes: int
    content_tokens: int
    representation: FileRepresentation
    summary: dict[str, Any] | None
    content_encoding: ContentEncoding | None
    content: str | None
    content_field_tokens: int | None


def iter_jsonl_prefix(
    payload: PackPayload,
    *,
    include_structure: bool,
    include_overview: bool,
    mode: PackMode,
    max_output: str | None,
    split_output: str | None,
    fit_to_max_output: bool,
    representation_rules: dict[str, list[str]] | None,
    summary_config: dict[str, Any] | None,
    selection_trace: list[dict[str, Any]] | None,
) -> Iterable[str]:
    structure_included = bool(include_structure and payload.include_structure)
    meta: dict[str, Any] = {
        "type": "meta",
        "schema_version": 2,
        "mode": mode.value,
        "root_name": payload.root_name,
        "overview": payload.overview if include_overview else None,
        "encoding_name": payload.encoding_name,
        "compressed": payload.compressed,
        "content_encoding": payload.content_encoding.value,
        "content_total_tokens": payload.content_total_tokens,
        "line_numbers": payload.line_numbers,
        "structure_included": structure_included,
        "include_files": payload.include_files,
        "max_output": max_output,
        "split_output": split_output,
        "fit_to_max_output": fit_to_max_output,
        "representation_rules": representation_rules,
        "summary_config": summary_config,
        "selection_trace": selection_trace,
    }
    yield _dump(meta)

    if structure_included:
        structure: dict[str, Any] = {"type": "structure", "paths": payload.structure_paths}
        yield _dump(structure)


def iter_jsonl_file_records(payload: PackPayload, *, files: list[JsonlFile]) -> Iterable[str]:
    for f in files:
        yield _dump(_file_record(payload, f))


def _file_record(payload: PackPayload, f: JsonlFile) -> dict[str, Any]:
    content: str | None = None
    content_encoding: str | None = None
    if f.representation is FileRepresentation.CONTENT:
        if not payload.include_files:
            raise ValueError("Internal error: content representation requested with include_files=False")
        if f.is_binary:
            raise ValueError("Internal error: content representation requested for binary file")
        if f.content_encoding is None:
            raise ValueError("Internal error: content encoding required for content representation")
        if f.content is None:
            raise ValueError("Internal error: content required for content representation")
        content_encoding = f.content_encoding.value
        content = _encode_content(f.content, f.content_encoding)

    return {
        "type": "file",
        "path": f.path,
        "language": f.language,
        "is_binary": f.is_binary,
        "size_bytes": f.size_bytes,
        "content_tokens": f.content_tokens,
        "representation": f.representation.value,
        "summary": f.summary,
        "content_encoding": content_encoding,
        "content": content,
        "content_field_tokens": f.content_field_tokens,
    }


def _encode_content(content: str, encoding: ContentEncoding) -> str:
    if encoding is ContentEncoding.BASE64:
        return base64.b64encode(content.encode("utf-8")).decode("ascii")
    return content


def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
