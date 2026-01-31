"""Configuration handling for anatomize.

This module provides configuration loading from files and environment variables.
"""

from __future__ import annotations

from pathlib import Path
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


class SkeletonConfig(BaseModel):
    """Configuration for skeleton generation.

    This can be loaded from a .anatomize.yaml file in the project root.

    Attributes
    ----------
    sources
        Source directories to analyze.
    output
        Output directory for skeleton files.
    level
        Default resolution level.
    formats
        Default output formats.
    exclude
        Exclude patterns (gitignore-like) applied relative to each source root.
    symlinks
        Symlink policy: forbid, files, dirs, or all.
    workers
        Worker count for extraction (0 = auto).
    pack
        Defaults for `anatomize pack`.
    """

    sources: list[str] = Field(default_factory=lambda: ["src"])
    output: str = ".skeleton"
    level: ResolutionLevel = ResolutionLevel.MODULES
    formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.YAML])
    exclude: list[str] = Field(
        default_factory=lambda: [
            "__pycache__",
            "*.pyc",
            ".git",
            ".venv",
            ".skeleton",
            ".mypy_cache",
            ".pytest_cache",
        ]
    )
    symlinks: SymlinkPolicy = SymlinkPolicy.FORBID
    workers: int = Field(default=0, ge=0)
    pack: PackConfig | None = None

    model_config = {"extra": "forbid"}

    @classmethod
    def from_file(cls, path: Path) -> SkeletonConfig:
        """Load configuration from a YAML file.

        Parameters
        ----------
        path
            Path to the configuration file.

        Returns
        -------
        SkeletonConfig
            Loaded configuration.
        """
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            data = {}

        return cls.model_validate(data)

    @classmethod
    def find_config(cls, start_dir: Path | None = None) -> SkeletonConfig | None:
        """Find and load configuration file.

        Searches for .anatomize.yaml in the current directory and parent
        directories up to the filesystem root.

        Parameters
        ----------
        start_dir
            Directory to start searching from. Defaults to current directory.

        Returns
        -------
        SkeletonConfig or None
            Loaded configuration or None if not found.
        """
        if start_dir is None:
            start_dir = Path.cwd()

        current = start_dir.resolve()

        while True:
            config_path = current / ".anatomize.yaml"
            if config_path.exists():
                return cls.from_file(config_path)

            # Move to parent
            parent = current.parent
            if parent == current:
                # Reached filesystem root
                break
            current = parent

        return None

    def to_yaml(self) -> str:
        """Serialize configuration to YAML.

        Returns
        -------
        str
            YAML representation of the configuration.
        """
        data: dict[str, Any] = {
            "sources": self.sources,
            "output": self.output,
            "level": self.level.value,
            "formats": [f.value for f in self.formats],
            "exclude": self.exclude,
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
