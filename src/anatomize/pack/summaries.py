"""Type-aware summaries for hybrid pack mode."""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from typing import Any

import tomli
import yaml
from pydantic import BaseModel, Field

from anatomize.core.extractor import SymbolExtractor
from anatomize.core.types import ResolutionLevel


class SummaryConfig(BaseModel):
    """Configuration for summary generation limits.

    Attributes
    ----------
    max_depth
        Maximum nesting depth to traverse in structured data.
    max_keys
        Maximum number of dictionary keys to include.
    max_items
        Maximum total items (keys + list elements) to include.
    max_headings
        Maximum number of Markdown headings to extract.
    """

    max_depth: int = Field(default=3, ge=1)
    max_keys: int = Field(default=200, ge=1)
    max_items: int = Field(default=200, ge=1)
    max_headings: int = Field(default=200, ge=1)

    model_config = {"frozen": True, "extra": "forbid"}


def python_summary(path: Path, *, module_name: str, relative_path: str) -> dict[str, Any]:
    """Generate a structural summary of a Python module.

    Parameters
    ----------
    path
        Absolute path to the Python file.
    module_name
        Fully qualified module name (e.g., 'pkg.sub.module').
    relative_path
        Path relative to the source root (POSIX style).

    Returns
    -------
    dict[str, Any]
        Module info as a JSON-serializable dictionary.
    """
    extractor = SymbolExtractor(resolution=ResolutionLevel.SIGNATURES)
    info = extractor.extract_module(path, module_name, relative_path=relative_path, source=0)
    return info.model_dump(mode="json")


def json_summary(text: str, *, cfg: SummaryConfig) -> dict[str, Any]:
    """Generate a structural summary of JSON content.

    Parameters
    ----------
    text
        Raw JSON text to parse and summarize.
    cfg
        Summary configuration limits.

    Returns
    -------
    dict[str, Any]
        Summary with type='json' and list of dotted paths.

    Raises
    ------
    ValueError
        If the JSON cannot be parsed.
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("Failed to parse JSON for summary") from e
    paths = _outline_paths(obj, max_depth=cfg.max_depth, max_items=cfg.max_items, max_keys=cfg.max_keys)
    return {"type": "json", "paths": paths}


def yaml_summary(text: str, *, cfg: SummaryConfig) -> dict[str, Any]:
    """Generate a structural summary of YAML content.

    Parameters
    ----------
    text
        Raw YAML text to parse and summarize.
    cfg
        Summary configuration limits.

    Returns
    -------
    dict[str, Any]
        Summary with type='yaml' and list of dotted paths.

    Raises
    ------
    ValueError
        If the YAML cannot be parsed.
    """
    try:
        obj = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError("Failed to parse YAML for summary") from e
    paths = _outline_paths(obj, max_depth=cfg.max_depth, max_items=cfg.max_items, max_keys=cfg.max_keys)
    return {"type": "yaml", "paths": paths}


def toml_summary(text: str, *, cfg: SummaryConfig) -> dict[str, Any]:
    """Generate a structural summary of TOML content.

    Parameters
    ----------
    text
        Raw TOML text to parse and summarize.
    cfg
        Summary configuration limits.

    Returns
    -------
    dict[str, Any]
        Summary with type='toml' and list of dotted paths.

    Raises
    ------
    ValueError
        If the TOML cannot be parsed.
    """
    try:
        obj = tomli.loads(text)
    except tomli.TOMLDecodeError as e:
        raise ValueError("Failed to parse TOML for summary") from e
    paths = _outline_paths(obj, max_depth=cfg.max_depth, max_items=cfg.max_items, max_keys=cfg.max_keys)
    return {"type": "toml", "paths": paths}


_MD_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")


def markdown_summary(text: str, *, cfg: SummaryConfig) -> dict[str, Any]:
    """Generate a structural summary of Markdown content.

    Extracts heading levels (1-6) and their text up to the configured limit.

    Parameters
    ----------
    text
        Raw Markdown text to analyze.
    cfg
        Summary configuration limits.

    Returns
    -------
    dict[str, Any]
        Summary with type='markdown' and list of headings with level and text.
    """
    headings: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if not m:
            continue
        level = len(m.group("hashes"))
        headings.append({"level": level, "text": m.group("text")})
        if len(headings) >= cfg.max_headings:
            break
    return {"type": "markdown", "headings": headings}


def summary_for_text(*, suffix: str, text: str, rel_posix: str, cfg: SummaryConfig) -> dict[str, Any]:
    """Generate a summary for text content based on file suffix.

    Routes to the appropriate summary function based on file type.

    Parameters
    ----------
    suffix
        File extension including dot (e.g., '.json', '.yaml').
    text
        Raw file content.
    rel_posix
        Relative path for error messages.
    cfg
        Summary configuration limits.

    Returns
    -------
    dict[str, Any]
        Type-specific summary dictionary.

    Raises
    ------
    ValueError
        If the file type is not supported for summarization.
    """
    suf = suffix.lower()
    if suf == ".json":
        return json_summary(text, cfg=cfg)
    if suf in (".yml", ".yaml"):
        return yaml_summary(text, cfg=cfg)
    if suf == ".toml":
        return toml_summary(text, cfg=cfg)
    if suf in (".md", ".markdown"):
        return markdown_summary(text, cfg=cfg)
    raise ValueError(f"Unsupported summary type for {rel_posix}")


def summary_for_path(path: Path, *, rel_posix: str, cfg: SummaryConfig) -> dict[str, Any]:
    """Generate a summary for a file by reading and analyzing its content.

    Parameters
    ----------
    path
        Absolute path to the file.
    rel_posix
        Relative path for error messages.
    cfg
        Summary configuration limits.

    Returns
    -------
    dict[str, Any]
        Type-specific summary dictionary.

    Raises
    ------
    ValueError
        If the file type is not supported for summarization.
    """
    text = path.read_text(encoding="utf-8")
    return summary_for_text(suffix=path.suffix, text=text, rel_posix=rel_posix, cfg=cfg)


def _outline_paths(obj: Any, *, max_depth: int, max_items: int, max_keys: int) -> list[str]:
    paths: list[str] = []
    queue: deque[tuple[str, Any, int]] = deque([("", obj, 0)])
    key_count = 0
    item_count = 0

    while queue:
        prefix, cur, depth = queue.popleft()
        if depth >= max_depth:
            continue

        if isinstance(cur, dict):
            for k in sorted(cur.keys(), key=lambda x: str(x)):
                if key_count >= max_keys or item_count >= max_items:
                    return paths
                key_count += 1
                item_count += 1
                p = f"{prefix}.{k}" if prefix else str(k)
                paths.append(p)
                queue.append((p, cur[k], depth + 1))
        elif isinstance(cur, list):
            # Only expand the first few items deterministically.
            for i, v in enumerate(cur[: min(len(cur), 10)]):
                if item_count >= max_items:
                    return paths
                item_count += 1
                p = f"{prefix}[{i}]" if prefix else f"[{i}]"
                paths.append(p)
                queue.append((p, v, depth + 1))

    return paths
