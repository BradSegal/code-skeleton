"""YAML output formatter.

This module provides YAML formatting for skeleton data, optimized
for token efficiency while remaining human readable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from anatomize.core.types import Skeleton
from anatomize.formats.payloads import build_hierarchy_document, build_module_documents

logger = logging.getLogger(__name__)


class YamlFormatter:
    """Format skeleton data as YAML.

    YAML is the recommended format for token efficiency. It produces
    output approximately 15-25% smaller than JSON.

    Example
    -------
    >>> formatter = YamlFormatter()
    >>> formatter.write(skeleton, Path(".skeleton"))
    """

    def __init__(self) -> None:
        """Initialize the YAML formatter."""
        # Configure YAML dumper for clean output
        self._dumper = yaml.SafeDumper
        self._dumper.default_flow_style = False

    def write(self, skeleton: Skeleton, output_dir: Path) -> None:
        """Write skeleton to YAML files.

        Parameters
        ----------
        skeleton
            Skeleton data to write.
        output_dir
            Output directory path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write hierarchy file
        hierarchy_path = output_dir / "hierarchy.yaml"
        self._write_hierarchy(skeleton, hierarchy_path)

        # Write module files if available
        if skeleton.modules:
            modules_dir = output_dir / "modules"
            modules_dir.mkdir(exist_ok=True)
            self._write_modules(skeleton, modules_dir)

        logger.info("Wrote skeleton to %s (YAML)", output_dir)

    def _write_hierarchy(self, skeleton: Skeleton, path: Path) -> None:
        """Write hierarchy YAML file.

        Parameters
        ----------
        skeleton
            Skeleton data.
        path
            Output file path.
        """
        data = build_hierarchy_document(skeleton)

        self._write_yaml(data, path)

    def _write_modules(self, skeleton: Skeleton, output_dir: Path) -> None:
        """Write module YAML files.

        Each package gets its own file with all its modules.

        Parameters
        ----------
        skeleton
            Skeleton data.
        output_dir
            Output directory for module files.
        """
        docs = build_module_documents(skeleton)
        for package_name, data in docs.items():
            # Use dots in filename
            filename = f"{package_name}.yaml"
            path = output_dir / filename

            self._write_yaml(data, path)

    def _write_yaml(self, data: dict[str, Any], path: Path) -> None:
        """Write data to a YAML file.

        Parameters
        ----------
        data
            Data to write.
        path
            Output file path.
        """
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                Dumper=self._dumper,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def format_string(self, skeleton: Skeleton) -> str:
        """Format skeleton as a YAML string.

        Parameters
        ----------
        skeleton
            Skeleton data.

        Returns
        -------
        str
            YAML formatted string.
        """
        data = skeleton.to_dict()
        return str(
            yaml.dump(
            data,
            Dumper=self._dumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            )
        )
