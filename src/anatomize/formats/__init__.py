"""Output formatters for skeleton data.

This module provides formatters for converting skeleton data to
various output formats (YAML, JSON, Markdown).
"""

from __future__ import annotations

import os
from enum import Enum
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from anatomize.formats.json_fmt import JsonFormatter
from anatomize.formats.markdown_fmt import MarkdownFormatter
from anatomize.formats.yaml_fmt import YamlFormatter

if TYPE_CHECKING:
    from anatomize.core.types import Skeleton


class Formatter(Protocol):
    def write(self, skeleton: Skeleton, output_dir: Path) -> None: ...
    def format_string(self, skeleton: Skeleton) -> str: ...


class OutputFormat(str, Enum):
    """Output format for skeleton files.

    Attributes
    ----------
    YAML
        YAML format (token efficient, human readable).
    JSON
        JSON format (schema validatable).
    MARKDOWN
        Markdown format (documentation friendly).
    """

    YAML = "yaml"
    JSON = "json"
    MARKDOWN = "markdown"


def get_formatter(fmt: OutputFormat) -> YamlFormatter | JsonFormatter | MarkdownFormatter:
    """Get the appropriate formatter for the given format.

    Parameters
    ----------
    fmt
        Output format.

    Returns
    -------
    Formatter
        Formatter instance.
    """
    formatters: dict[OutputFormat, type[Formatter]] = {
        OutputFormat.YAML: YamlFormatter,
        OutputFormat.JSON: JsonFormatter,
        OutputFormat.MARKDOWN: MarkdownFormatter,
    }
    return formatters[fmt]()  # type: ignore[return-value]


def write_schemas(output_dir: Path) -> None:
    """Write packaged JSON schemas into the output directory."""
    schemas_dir = output_dir / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    package = files("anatomize.schemas")
    for name in ("hierarchy.schema.json", "module.schema.json"):
        content = package.joinpath(name).read_bytes()
        (schemas_dir / name).write_bytes(content)


def write_skeleton(
    skeleton: Skeleton,
    output_dir: str | Path,
    formats: list[OutputFormat] | None = None,
    *,
    metadata_base_dir: Path | None = None,
) -> None:
    """Write skeleton to output directory in specified formats.

    Parameters
    ----------
    skeleton
        Skeleton data to write.
    output_dir
        Output directory path.
    formats
        List of output formats. Defaults to [YAML].
    metadata_base_dir
        Base directory used to render `metadata.sources` as relative paths.
    """
    if formats is None:
        formats = [OutputFormat.YAML]

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    write_schemas(output_path)

    base = metadata_base_dir or output_path
    rendered = _render_sources_relative_to(skeleton, base)

    for fmt in formats:
        formatter = get_formatter(fmt)
        formatter.write(rendered, output_path)

    _write_manifest(output_path, formats=formats, resolution=rendered.metadata.resolution.value)


def _render_sources_relative_to(skeleton: Skeleton, base_dir: Path) -> Skeleton:
    base_dir = base_dir.resolve()
    rendered_sources: list[str] = []
    for s in skeleton.metadata.sources:
        src = Path(s)
        if not src.is_absolute():
            raise ValueError(f"metadata.sources must contain absolute paths, got: {s}")
        src = src.resolve()
        rel = os.path.relpath(src, base_dir)
        rendered_sources.append(Path(rel).as_posix())
    return skeleton.model_copy(update={"metadata": skeleton.metadata.model_copy(update={"sources": rendered_sources})})


def _write_manifest(output_dir: Path, *, formats: list[OutputFormat], resolution: str) -> None:
    import hashlib
    import json

    files: dict[str, str] = {}
    for p in sorted(output_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(output_dir).as_posix()
        if rel == "manifest.json":
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        files[rel] = h

    payload = {"formats": [f.value for f in formats], "resolution": resolution, "files": files}
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "OutputFormat",
    "Formatter",
    "get_formatter",
    "write_skeleton",
    "write_schemas",
    "YamlFormatter",
    "JsonFormatter",
    "MarkdownFormatter",
]
