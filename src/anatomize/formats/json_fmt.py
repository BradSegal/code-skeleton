"""JSON output formatter.

This module provides JSON formatting for skeleton data, optimized
for schema validation and programmatic access.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from anatomize.core.types import Skeleton
from anatomize.formats.payloads import build_hierarchy_document, build_module_documents

logger = logging.getLogger(__name__)


class JsonFormatter:
    """Format skeleton data as JSON.

    JSON is recommended for programmatic access and schema validation.
    Each file includes a $schema reference for validation.

    Example
    -------
    >>> formatter = JsonFormatter()
    >>> formatter.write(skeleton, Path(".skeleton"))
    """

    def __init__(self, indent: int = 2) -> None:
        """Initialize the JSON formatter.

        Parameters
        ----------
        indent
            Number of spaces for indentation.
        """
        self._indent = indent

    def write(self, skeleton: Skeleton, output_dir: Path) -> None:
        """Write skeleton to JSON files.

        Parameters
        ----------
        skeleton
            Skeleton data to write.
        output_dir
            Output directory path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write hierarchy file
        hierarchy_path = output_dir / "hierarchy.json"
        self._write_hierarchy(skeleton, hierarchy_path)

        # Write module files if available
        if skeleton.modules:
            modules_dir = output_dir / "modules"
            modules_dir.mkdir(exist_ok=True)
            self._write_modules(skeleton, modules_dir)

        logger.info("Wrote skeleton to %s (JSON)", output_dir)

    def _write_hierarchy(self, skeleton: Skeleton, path: Path) -> None:
        """Write hierarchy JSON file.

        Parameters
        ----------
        skeleton
            Skeleton data.
        path
            Output file path.
        """
        data = build_hierarchy_document(skeleton)
        data["$schema"] = "./schemas/hierarchy.schema.json"

        self._write_json(data, path)

    def _write_modules(self, skeleton: Skeleton, output_dir: Path) -> None:
        """Write module JSON files.

        Parameters
        ----------
        skeleton
            Skeleton data.
        output_dir
            Output directory for module files.
        """
        docs = build_module_documents(skeleton)
        for package_name, data in docs.items():
            filename = f"{package_name}.json"
            path = output_dir / filename

            data["$schema"] = "../schemas/module.schema.json"

            self._write_json(data, path)

    def _write_json(self, data: dict[str, Any], path: Path) -> None:
        """Write data to a JSON file.

        Parameters
        ----------
        data
            Data to write.
        path
            Output file path.
        """
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=self._indent, ensure_ascii=False)
            f.write("\n")

    def format_string(self, skeleton: Skeleton) -> str:
        """Format skeleton as a JSON string.

        Parameters
        ----------
        skeleton
            Skeleton data.

        Returns
        -------
        str
            JSON formatted string.
        """
        data = skeleton.to_dict()
        return json.dumps(data, indent=self._indent, ensure_ascii=False)
