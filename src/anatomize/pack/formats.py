"""Pack output formats."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast
from xml.etree.ElementTree import Element, SubElement, tostring


class PackFormat(str, Enum):
    MARKDOWN = "markdown"
    PLAIN = "plain"
    JSON = "json"
    XML = "xml"
    JSONL = "jsonl"


class ContentEncoding(str, Enum):
    VERBATIM = "verbatim"
    FENCE_SAFE = "fence-safe"
    BASE64 = "base64"


@dataclass(frozen=True)
class PackFile:
    path: str
    language: str | None
    is_binary: bool
    content: str | None
    content_tokens: int


@dataclass(frozen=True)
class PackPayload:
    root_name: str
    structure_paths: list[str]
    overview: dict[str, Any] | None
    files: list[PackFile]
    content_total_tokens: int
    encoding_name: str
    compressed: bool
    content_encoding: ContentEncoding
    line_numbers: bool
    include_structure: bool
    include_files: bool


def default_output_path(fmt: PackFormat) -> Path:
    if fmt is PackFormat.MARKDOWN:
        return Path("anatomize-pack.md")
    if fmt is PackFormat.PLAIN:
        return Path("anatomize-pack.txt")
    if fmt is PackFormat.JSON:
        return Path("anatomize-pack.json")
    if fmt is PackFormat.JSONL:
        return Path("anatomize-pack.jsonl")
    return Path("anatomize-pack.xml")


def infer_pack_format_from_output_path(path: Path) -> PackFormat | None:
    """Infer `PackFormat` from a known output file extension.

    Returns None if the extension is unknown or missing.
    """
    suf = path.suffix.lower()
    if suf in (".md", ".markdown"):
        return PackFormat.MARKDOWN
    if suf in (".txt",):
        return PackFormat.PLAIN
    if suf in (".json",):
        return PackFormat.JSON
    if suf in (".xml",):
        return PackFormat.XML
    if suf in (".jsonl",):
        return PackFormat.JSONL
    return None


def render(payload: PackPayload, *, fmt: PackFormat) -> str:
    if fmt is PackFormat.MARKDOWN:
        return _render_markdown(payload)
    if fmt is PackFormat.PLAIN:
        return _render_plain(payload)
    if fmt is PackFormat.JSON:
        return _render_json(payload)
    if fmt is PackFormat.XML:
        return _render_xml(payload)
    if fmt is PackFormat.JSONL:
        raise ValueError("JSONL output must be written via the streaming writer")
    raise ValueError(f"Unsupported pack format: {fmt}")


def _render_markdown(payload: PackPayload) -> str:
    if payload.content_encoding is ContentEncoding.VERBATIM:
        raise ValueError("Markdown output requires --content-encoding fence-safe or base64")

    prefix = render_prefix(payload, fmt=PackFormat.MARKDOWN, include_structure=payload.include_structure)
    suffix = render_suffix(payload, fmt=PackFormat.MARKDOWN)
    if not payload.include_files:
        return prefix + suffix

    blocks = [render_file_block(payload, fmt=PackFormat.MARKDOWN, file=f) for f in payload.files]
    return prefix + "".join(blocks) + suffix


def _render_plain(payload: PackPayload) -> str:
    prefix = render_prefix(payload, fmt=PackFormat.PLAIN, include_structure=payload.include_structure)
    suffix = render_suffix(payload, fmt=PackFormat.PLAIN)
    if not payload.include_files:
        return prefix + suffix
    blocks = [render_file_block(payload, fmt=PackFormat.PLAIN, file=f) for f in payload.files]
    return prefix + "".join(blocks) + suffix


def _render_json(payload: PackPayload) -> str:
    obj: dict[str, Any] = {
        "root_name": payload.root_name,
        "overview": payload.overview,
        "encoding_name": payload.encoding_name,
        "compressed": payload.compressed,
        "content_encoding": payload.content_encoding.value,
        "content_total_tokens": payload.content_total_tokens,
        "structure_paths": payload.structure_paths if payload.include_structure else [],
        "files": [
            {
                "path": f.path,
                "language": f.language,
                "is_binary": f.is_binary,
                "content_tokens": f.content_tokens,
                "content_encoding": payload.content_encoding.value if payload.include_files else None,
                "content": _encoded_content(f.content or "", payload.content_encoding)
                if payload.include_files and not f.is_binary
                else None,
            }
            for f in payload.files
        ],
    }
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _render_xml(payload: PackPayload) -> str:
    root = Element("code_pack")
    root.set("root_name", payload.root_name)
    root.set("encoding", payload.encoding_name)
    root.set("compressed", str(payload.compressed).lower())
    root.set("content_encoding", payload.content_encoding.value)
    root.set("content_total_tokens", str(payload.content_total_tokens))

    if payload.overview is not None:
        overview = SubElement(root, "overview")
        overview.text = json.dumps(payload.overview, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    if payload.include_structure:
        structure = SubElement(root, "structure")
        for p in payload.structure_paths:
            e = SubElement(structure, "path")
            e.text = p

    files = SubElement(root, "files")
    for f in payload.files:
        e = SubElement(files, "file")
        e.set("path", f.path)
        e.set("content_tokens", str(f.content_tokens))
        if f.language:
            e.set("language", f.language)
        e.set("binary", str(f.is_binary).lower())
        if payload.include_files and not f.is_binary:
            content = SubElement(e, "content")
            content.text = _encoded_content(f.content or "", payload.content_encoding)

    xml_bytes = cast(bytes, tostring(root, encoding="utf-8"))
    return xml_bytes.decode("utf-8") + "\n"


def _safe_fence(content: str) -> str:
    # Use the shortest backtick fence that cannot appear in content.
    # Markdown fences match a run of backticks at line start.
    max_run = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return "`" * max(3, max_run + 1)


def _encoded_content(content: str, encoding: ContentEncoding) -> str:
    if encoding is ContentEncoding.BASE64:
        return base64.b64encode(content.encode("utf-8")).decode("ascii")
    return content


def render_prefix(
    payload: PackPayload, *, fmt: PackFormat, include_structure: bool, include_overview: bool = True
) -> str:
    """Render the deterministic prefix for streaming/splitting writers."""
    if fmt is PackFormat.MARKDOWN:
        if payload.content_encoding is ContentEncoding.VERBATIM:
            raise ValueError("Markdown output requires --content-encoding fence-safe or base64")

        lines: list[str] = []
        lines.append("# Code Pack")
        lines.append("")
        lines.append(f"- Root: `{payload.root_name}`")
        lines.append(f"- Encoding: `{payload.encoding_name}`")
        lines.append(f"- Compressed: `{payload.compressed}`")
        lines.append(f"- Content encoding: `{payload.content_encoding.value}`")
        lines.append(f"- Content tokens: `{payload.content_total_tokens}`")
        if include_overview and payload.overview is not None:
            lines.append("")
            lines.append("## Overview")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(payload.overview, ensure_ascii=False, sort_keys=True, indent=2))
            lines.append("```")
        lines.append("")
        if include_structure and payload.include_structure:
            lines.append("## Structure")
            lines.append("")
            lines.append("```")
            lines.extend(payload.structure_paths)
            lines.append("```")
            lines.append("")
        if payload.include_files:
            lines.append("## Files")
        return "\n".join(lines) + "\n"

    if fmt is PackFormat.PLAIN:
        lines = [
            f"ROOT: {payload.root_name}",
            f"ENCODING: {payload.encoding_name}",
            f"COMPRESSED: {payload.compressed}",
            f"CONTENT_ENCODING: {payload.content_encoding.value}",
            f"CONTENT_TOKENS: {payload.content_total_tokens}",
            (
                "OVERVIEW_JSON: "
                + json.dumps(payload.overview, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if include_overview and payload.overview is not None
                else "OVERVIEW_JSON: null"
            ),
            "",
        ]
        if include_structure and payload.include_structure:
            lines.append("STRUCTURE:")
            lines.extend(payload.structure_paths)
            lines.append("")
        if payload.include_files:
            lines.append("FILES:")
        return "\n".join(lines) + "\n"

    raise ValueError(f"Prefix rendering is only supported for markdown/plain: {fmt.value}")


def render_file_block(payload: PackPayload, *, fmt: PackFormat, file: PackFile) -> str:
    if not payload.include_files:
        raise ValueError("Internal error: file blocks require include_files=True")

    if fmt is PackFormat.MARKDOWN:
        lines: list[str] = []
        lines.append("")
        lines.append(f"### {file.path}")
        lines.append("")
        if file.is_binary:
            lines.append(f"_Binary file (content omitted). Content tokens: `{file.content_tokens}`_")
            return "\n".join(lines) + "\n"

        lines.append(f"_Content tokens: `{file.content_tokens}`_")
        if payload.content_encoding is ContentEncoding.BASE64:
            lines.append("")
            lines.append("_Content is base64-encoded UTF-8._")
        lines.append("")

        raw = (file.content or "").rstrip("\n")
        content = _encoded_content(raw, payload.content_encoding)

        lang = file.language or ""
        if payload.content_encoding is ContentEncoding.BASE64:
            lang = "base64"

        fence = "```"
        if payload.content_encoding in (ContentEncoding.FENCE_SAFE, ContentEncoding.BASE64):
            fence = _safe_fence(content)
        lines.append(f"{fence}{lang}")
        lines.append(content)
        lines.append(fence)
        return "\n".join(lines) + "\n"

    if fmt is PackFormat.PLAIN:
        lines = [
            "",
            "=" * 80,
            file.path,
            f"CONTENT_TOKENS: {file.content_tokens}",
        ]
        if file.is_binary:
            lines.append("[BINARY CONTENT OMITTED]")
            return "\n".join(lines) + "\n"
        lines.append("-" * 80)
        raw = (file.content or "").rstrip("\n")
        lines.append(_encoded_content(raw, payload.content_encoding))
        return "\n".join(lines) + "\n"

    raise ValueError(f"File block rendering is only supported for markdown/plain: {fmt.value}")


def render_suffix(payload: PackPayload, *, fmt: PackFormat) -> str:
    _ = payload
    if fmt in (PackFormat.MARKDOWN, PackFormat.PLAIN):
        return "\n"
    raise ValueError(f"Suffix rendering is only supported for markdown/plain: {fmt.value}")
