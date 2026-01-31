"""Main skeleton generator.

This module provides the primary interface for generating skeletons at
various resolution levels, using strict container-directory scanning.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import local
from typing import Any, cast

import tiktoken

from anatomize.core.discovery import discover
from anatomize.core.extractor import SymbolExtractor
from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import (
    ModuleInfo,
    ResolutionLevel,
    Skeleton,
    SkeletonMetadata,
)
from anatomize.version import __version__

logger = logging.getLogger(__name__)


class SkeletonGenerator:
    """Main skeleton generator.

    This class orchestrates discovery and extraction at various resolution
    levels. It accepts one or more *container* directories (e.g. `./src`) and
    discovers Python modules underneath them (including namespace packages).
    """

    def __init__(
        self,
        sources: Sequence[str | Path],
        *,
        exclude: list[str] | None = None,
        symlinks: SymlinkPolicy = SymlinkPolicy.FORBID,
        workers: int = 0,
    ) -> None:
        """Initialize the skeleton generator.

        Parameters
        ----------
        sources
            One or more source roots (container directories) to analyze.
        exclude
            Exclude patterns (gitignore-like) applied relative to each source root.
        symlinks
            Symlink handling policy.
        workers
            Number of workers for parallel extraction. 0 means auto.
        """
        if not sources:
            raise ValueError("At least one source directory is required")

        self._sources = [Path(p).resolve() for p in sources]
        self._exclude = exclude or [
            "__pycache__",
            "*.pyc",
            ".git",
            ".venv",
            ".skeleton",
            ".mypy_cache",
            ".pytest_cache",
        ]
        self._symlinks = symlinks
        self._workers = workers

        for p in self._sources:
            if not p.exists():
                raise ValueError(f"Source directory does not exist: {p}")
            if not p.is_dir():
                raise ValueError(f"Source path is not a directory: {p}")

    def generate(
        self,
        level: str | ResolutionLevel = ResolutionLevel.MODULES,
    ) -> Skeleton:
        """Generate skeleton at the specified resolution level.

        Parameters
        ----------
        level
            Resolution level ('hierarchy', 'modules', or 'signatures').

        Returns
        -------
        Skeleton
            Generated skeleton data.
        """
        # Normalize level
        if isinstance(level, str):
            level = ResolutionLevel(level)

        logger.info("Generating skeleton at level %s for %s", level.value, ", ".join(str(p) for p in self._sources))

        discovery = discover(self._sources, exclude=self._exclude, symlinks=self._symlinks)
        packages = discovery.packages

        modules: dict[str, ModuleInfo] = {}
        if level in (ResolutionLevel.MODULES, ResolutionLevel.SIGNATURES):
            items = [(name, discovery.modules[name]) for name in sorted(discovery.modules.keys())]
            workers = self._resolve_workers()
            if workers == 1 or len(items) <= 1:
                extractor = SymbolExtractor(resolution=level)
                for module_name, discovered in items:
                    modules[module_name] = extractor.extract_module(
                        discovered.absolute_path,
                        module_name,
                        relative_path=discovered.relative_path,
                        source=discovered.source,
                    )
            else:
                tls = local()

                def get_extractor() -> SymbolExtractor:
                    extractor = getattr(tls, "extractor", None)
                    if extractor is None or getattr(tls, "level", None) != level:
                        tls.extractor = SymbolExtractor(resolution=level)
                        tls.level = level
                        return cast(SymbolExtractor, tls.extractor)
                    return cast(SymbolExtractor, extractor)

                def run_one(module_name: str, discovered: Any) -> tuple[str, ModuleInfo]:
                    try:
                        extractor = get_extractor()
                        info = extractor.extract_module(
                            discovered.absolute_path,
                            module_name,
                            relative_path=discovered.relative_path,
                            source=discovered.source,
                        )
                        return module_name, info
                    except Exception as e:
                        raise ValueError(f"Failed to extract {module_name} from {discovered.absolute_path}") from e

                errors: list[str] = []
                results: dict[str, ModuleInfo] = {}
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = [ex.submit(run_one, name, discovered) for name, discovered in items]
                    for fut in futures:
                        try:
                            mod_name, info = fut.result()
                            results[mod_name] = info
                        except Exception as e:
                            errors.append(str(e))

                if errors:
                    errors.sort()
                    raise ValueError("Module extraction failed:\n" + "\n".join(f"- {m}" for m in errors))

                modules = results

        # Calculate statistics
        total_packages = len(packages)
        total_modules = sum(len(p.modules) for p in packages.values())
        total_classes = sum(len(m.classes) for m in modules.values())
        total_functions = sum(len(m.functions) for m in modules.values())
        total_functions += sum(
            len(c.methods) for m in modules.values() for c in m.classes
        )

        token_estimate = 0

        # Build metadata
        metadata = SkeletonMetadata(
            generator_version=__version__,
            sources=[p.as_posix() for p in discovery.sources],
            resolution=level,
            total_packages=total_packages,
            total_modules=total_modules,
            total_classes=total_classes,
            total_functions=total_functions,
            token_estimate=token_estimate,
        )

        skeleton = Skeleton(
            metadata=metadata,
            packages=packages,
            modules=modules,
        )

        estimate = self._estimate_tokens(skeleton)
        return Skeleton(
            metadata=skeleton.metadata.model_copy(update={"token_estimate": estimate}),
            packages=skeleton.packages,
            modules=skeleton.modules,
        )

    def _estimate_tokens(self, skeleton: Skeleton) -> int:
        """Estimate token count from a canonical deterministic representation."""
        encoding = tiktoken.get_encoding("cl100k_base")
        canonical = json.dumps(
            skeleton.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return len(encoding.encode(canonical))

    def _resolve_workers(self) -> int:
        if self._workers <= 0:
            return max(1, min(32, os.cpu_count() or 1))
        return max(1, self._workers)

    def estimate(self, level: str | ResolutionLevel = ResolutionLevel.MODULES) -> int:
        """Estimate token count without generating full skeleton.

        Parameters
        ----------
        level
            Resolution level.

        Returns
        -------
        int
            Estimated token count.
        """
        skeleton = self.generate(level)
        return skeleton.token_estimate
