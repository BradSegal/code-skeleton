"""Validation for on-disk skeleton outputs.

Validation is strict and content-based: it regenerates the skeleton with the
same detected resolution and compares the output bytes deterministically.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import ResolutionLevel
from anatomize.formats import OutputFormat, write_skeleton
from anatomize.generators.main import SkeletonGenerator


def validate_skeleton_dir(
    *,
    skeleton_dir: Path,
    sources: list[Path],
    exclude: list[str] | None,
    symlinks: SymlinkPolicy,
    workers: int,
    fix: bool,
) -> bool:
    """Validate a skeleton directory against the given sources.

    If `fix` is True, rewrites the skeleton output to match the regenerated
    deterministic output.
    """
    formats = _detect_formats(skeleton_dir)
    resolution = _detect_resolution(skeleton_dir)

    generator = SkeletonGenerator(sources=sources, exclude=exclude, symlinks=symlinks, workers=workers)
    skeleton = generator.generate(level=resolution)

    tmp_kwargs: dict[str, Any] = {"prefix": "anatomize_validate_"}
    if fix:
        tmp_kwargs["dir"] = skeleton_dir.parent

    with tempfile.TemporaryDirectory(**tmp_kwargs) as tmp:
        tmp_dir = Path(tmp)
        write_skeleton(skeleton, tmp_dir, formats=formats, metadata_base_dir=skeleton_dir)

        expected_manifest = tmp_dir / "manifest.json"
        actual_manifest = skeleton_dir / "manifest.json"
        if expected_manifest.exists() and actual_manifest.exists():
            if expected_manifest.read_bytes() == actual_manifest.read_bytes():
                return False

        mismatches = _compare_dirs(expected=tmp_dir, actual=skeleton_dir)
        if mismatches:
            if fix:
                _atomic_replace_dir(dst=skeleton_dir, src=tmp_dir)
                return True
            details = "\n".join(f"- {m}" for m in mismatches[:50])
            suffix = "" if len(mismatches) <= 50 else f"\n- ... and {len(mismatches) - 50} more"
            raise ValueError(f"Skeleton validation failed:\n{details}{suffix}")
    return False


def _detect_formats(skeleton_dir: Path) -> list[OutputFormat]:
    formats: list[OutputFormat] = []
    if (skeleton_dir / "hierarchy.yaml").exists():
        formats.append(OutputFormat.YAML)
    if (skeleton_dir / "hierarchy.json").exists():
        formats.append(OutputFormat.JSON)
    if (skeleton_dir / "hierarchy.md").exists():
        formats.append(OutputFormat.MARKDOWN)
    if not formats:
        raise ValueError(f"No hierarchy files found in {skeleton_dir}")
    return formats


def _detect_resolution(skeleton_dir: Path) -> ResolutionLevel:
    data = _read_hierarchy_metadata(skeleton_dir)
    try:
        return ResolutionLevel(data["resolution"])
    except Exception as e:
        raise ValueError(f"Invalid or missing resolution in {skeleton_dir}") from e


def _read_hierarchy_metadata(skeleton_dir: Path) -> dict[str, Any]:
    json_path = skeleton_dir / "hierarchy.json"
    yaml_path = skeleton_dir / "hierarchy.yaml"
    if json_path.exists():
        obj = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or "metadata" not in obj or not isinstance(obj["metadata"], dict):
            raise ValueError("Invalid hierarchy.json")
        return obj["metadata"]
    if yaml_path.exists():
        obj = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or "metadata" not in obj or not isinstance(obj["metadata"], dict):
            raise ValueError("Invalid hierarchy.yaml")
        return obj["metadata"]
    raise ValueError(f"Missing hierarchy file in {skeleton_dir}")


def _compare_dirs(*, expected: Path, actual: Path) -> list[str]:
    """Return a list of mismatch descriptions."""
    expected_files = sorted(p.relative_to(expected).as_posix() for p in expected.rglob("*") if p.is_file())
    actual_files = sorted(p.relative_to(actual).as_posix() for p in actual.rglob("*") if p.is_file())

    mismatches: list[str] = []

    expected_set = set(expected_files)
    actual_set = set(actual_files)

    for missing in sorted(expected_set - actual_set):
        mismatches.append(f"missing file: {missing}")
    for extra in sorted(actual_set - expected_set):
        mismatches.append(f"extra file: {extra}")

    for rel in sorted(expected_set & actual_set):
        exp_bytes = (expected / rel).read_bytes()
        act_bytes = (actual / rel).read_bytes()
        if exp_bytes != act_bytes:
            mismatches.append(f"content differs: {rel}")

    return mismatches


def _atomic_replace_dir(*, dst: Path, src: Path) -> None:
    """Atomically replace dst directory with src directory contents."""
    dst = dst.resolve()
    src = src.resolve()
    parent = dst.parent

    backup = parent / f".{dst.name}.bak.{uuid.uuid4().hex}"
    moved_backup = False
    try:
        if dst.exists():
            dst.rename(backup)
            moved_backup = True
        src.rename(dst)
    except Exception:
        # Attempt rollback if we moved the original aside but didn't restore it.
        if moved_backup and not dst.exists() and backup.exists():
            backup.rename(dst)
        raise
    finally:
        if moved_backup and backup.exists():
            shutil.rmtree(backup)
