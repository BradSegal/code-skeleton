"""Configuration handling for anatomize.

This module provides configuration loading from files and environment variables.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, Field

from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import ResolutionLevel
from anatomize.formats import OutputFormat
from anatomize.pack.formats import ContentEncoding, PackFormat, infer_pack_format_from_output_path
from anatomize.pack.mode import PackMode
from anatomize.pack.slicing import SliceBackend
from anatomize.pack.summaries import SummaryConfig


class PackConfig(BaseModel):
    """Configuration for `anatomize pack`."""

    format: PackFormat = PackFormat.MARKDOWN
    mode: PackMode = PackMode.BUNDLE
    output: str | None = None
    include: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    ignore_files: list[str] = Field(default_factory=list)
    respect_standard_ignores: bool = True
    symlinks: SymlinkPolicy = SymlinkPolicy.FORBID
    max_file_bytes: int = Field(default=1_000_000, ge=0)
    workers: int = Field(default=0, ge=0)
    token_encoding: str = "cl100k_base"
    compress: bool = False
    content_encoding: ContentEncoding = ContentEncoding.FENCE_SAFE
    line_numbers: bool = False
    no_structure: bool = False
    no_files: bool = False
    max_output: str | None = None
    split_output: str | None = None
    fit_to_max_output: bool = False
    content: list[str] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    meta: list[str] = Field(default_factory=list)
    summary_config: SummaryConfig = Field(default_factory=SummaryConfig)
    python_roots: list[str] = Field(default_factory=list)
    slice_backend: SliceBackend = SliceBackend.IMPORTS
    uses_include_private: bool = False
    pyright_langserver_cmd: str = "pyright-langserver --stdio"

    model_config = {"extra": "forbid"}

    def model_post_init(self, __context: Any) -> None:
        if self.output is None:
            return
        inferred = infer_pack_format_from_output_path(Path(self.output))
        if inferred is None:
            return
        if inferred is not self.format:
            raise ValueError(
                f"pack.output extension implies format {inferred.value} but pack.format is {self.format.value}"
            )


DEFAULT_EXCLUDE: list[str] = [
    "__pycache__",
    "*.pyc",
    ".git",
    ".venv",
    ".anatomy",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
]


class SkeletonSourceConfig(BaseModel):
    """One configured skeleton output."""

    path: str
    output: str | None = None
    level: ResolutionLevel | None = None
    formats: list[OutputFormat] | None = None
    exclude: list[str] | None = None
    symlinks: SymlinkPolicy | None = None
    workers: int | None = Field(default=None, ge=0)

    model_config = {"extra": "forbid"}

    def model_post_init(self, __context: Any) -> None:
        if not self.path.strip():
            raise ValueError("sources[].path must be non-empty")
        if self.output is None:
            return
        rel = self.output.replace("\\", "/").strip("/")
        if not rel:
            raise ValueError("sources[].output must be a non-empty relative path")
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"sources[].output must be a safe relative path (no '..'): {self.output}")
        self.output = p.as_posix()


class AnatomizeConfig(BaseModel):
    """Project configuration loaded from `.anatomize.yaml`.

    This config can define multiple skeleton outputs (per-source resolution),
    plus defaults for `anatomize pack`.
    """

    output: str = ".anatomy"
    sources: list[SkeletonSourceConfig] = Field(default_factory=list)

    # Defaults applied to sources that omit fields.
    level: ResolutionLevel = ResolutionLevel.MODULES
    formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.YAML])
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    symlinks: SymlinkPolicy = SymlinkPolicy.FORBID
    workers: int = Field(default=0, ge=0)

    pack: PackConfig | None = None

    model_config = {"extra": "forbid"}

    @classmethod
    def from_file(cls, path: Path) -> AnatomizeConfig:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            data = {}
        return cls.model_validate(data)

    @classmethod
    def find_config_path(cls, start_dir: Path | None = None) -> Path | None:
        if start_dir is None:
            start_dir = Path.cwd()
        current = start_dir.resolve()
        while True:
            candidate = current / ".anatomize.yaml"
            if candidate.exists():
                return candidate
            parent = current.parent
            if parent == current:
                return None
            current = parent

    @classmethod
    def find_config(cls, start_dir: Path | None = None) -> AnatomizeConfig | None:
        p = cls.find_config_path(start_dir=start_dir)
        if p is None:
            return None
        return cls.from_file(p)

    def to_yaml(self) -> str:
        data: dict[str, Any] = {
            "output": self.output,
            "sources": [
                {
                    "path": s.path,
                    **({} if s.output is None else {"output": s.output}),
                    **({} if s.level is None else {"level": s.level.value}),
                    **({} if s.formats is None else {"formats": [f.value for f in s.formats]}),
                    **({} if s.exclude is None else {"exclude": list(s.exclude)}),
                    **({} if s.symlinks is None else {"symlinks": s.symlinks.value}),
                    **({} if s.workers is None else {"workers": s.workers}),
                }
                for s in self.sources
            ],
            "level": self.level.value,
            "formats": [f.value for f in self.formats],
            "exclude": list(self.exclude),
            "symlinks": self.symlinks.value,
            "workers": self.workers,
        }
        if self.pack is not None:
            data["pack"] = {
                "format": self.pack.format.value,
                "mode": self.pack.mode.value,
                "output": self.pack.output,
                "include": self.pack.include,
                "ignore": self.pack.ignore,
                "ignore_files": self.pack.ignore_files,
                "respect_standard_ignores": self.pack.respect_standard_ignores,
                "symlinks": self.pack.symlinks.value,
                "max_file_bytes": self.pack.max_file_bytes,
                "workers": self.pack.workers,
                "token_encoding": self.pack.token_encoding,
                "compress": self.pack.compress,
                "content_encoding": self.pack.content_encoding.value,
                "line_numbers": self.pack.line_numbers,
                "no_structure": self.pack.no_structure,
                "no_files": self.pack.no_files,
                "max_output": self.pack.max_output,
                "split_output": self.pack.split_output,
                "fit_to_max_output": self.pack.fit_to_max_output,
                "content": self.pack.content,
                "summary": self.pack.summary,
                "meta": self.pack.meta,
                "summary_config": self.pack.summary_config.model_dump(mode="json"),
                "python_roots": self.pack.python_roots,
                "slice_backend": self.pack.slice_backend.value,
                "uses_include_private": self.pack.uses_include_private,
                "pyright_langserver_cmd": self.pack.pyright_langserver_cmd,
            }
        return str(yaml.dump(data, default_flow_style=False, sort_keys=False))
