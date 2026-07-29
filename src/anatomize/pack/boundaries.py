"""Pack request and artifact boundary validation."""

from __future__ import annotations

from pathlib import Path

from anatomize.pack.discovery import DiscoveredPath
from anatomize.pack.formats import PackFormat, infer_pack_format_from_output_path
from anatomize.pack.limit import OutputLimit
from anatomize.pack.mode import PackMode
from anatomize.pack.slicing import SliceBackend


def validate_pack_request(
    *,
    root: Path,
    out_path: Path,
    fmt: PackFormat,
    mode: PackMode,
    compress: bool,
    fit_to_max_output: bool,
    max_output: OutputLimit | None,
    entries: list[Path],
    target: Path | None,
    target_module: str | None,
    reverse_deps: bool,
    deps: bool,
    uses: bool,
    slice_backend: SliceBackend,
) -> None:
    """Reject invalid pack requests before repository discovery."""
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Root must be an existing directory: {root}")

    inferred = infer_pack_format_from_output_path(out_path)
    if inferred is not None and inferred is not fmt:
        raise ValueError(f"Output path extension implies format {inferred.value} but fmt is {fmt.value}")

    if mode is PackMode.HYBRID:
        if compress:
            raise ValueError("--compress is not supported in --mode hybrid")
        if fmt not in (PackFormat.JSONL, PackFormat.MARKDOWN, PackFormat.PLAIN):
            raise ValueError("--mode hybrid supports only markdown, plain, or jsonl output")
        if fit_to_max_output:
            if fmt is not PackFormat.JSONL:
                raise ValueError("--fit-to-max-output is only supported with --mode hybrid --format jsonl")
            if max_output is None:
                raise ValueError("--fit-to-max-output requires --max-output")

    if target is not None and target_module is not None:
        raise ValueError("Specify at most one of --target or --module")
    if entries and (target is not None or target_module is not None or reverse_deps):
        raise ValueError("Use either --entry or --target/--module selection, not both")
    if (target is not None or target_module is not None) and not (reverse_deps or deps or uses):
        raise ValueError("When using --target/--module, specify --reverse-deps, --deps, and/or --uses")
    if uses and slice_backend is not SliceBackend.PYRIGHT:
        raise ValueError("--uses requires --slice-backend pyright (no fallback)")

    for entry in entries:
        resolved = (root / entry).resolve() if not entry.is_absolute() else entry.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"--entry must be an existing file: {resolved}")
        if not is_within(resolved, root):
            raise ValueError(f"--entry must be within ROOT ({root}): {resolved}")
        if deps and resolved.suffix != ".py":
            raise ValueError(f"--deps requires Python entry files (*.py): {resolved}")

    if target is not None:
        resolved_target = (root / target).resolve() if not target.is_absolute() else target.resolve()
        if not resolved_target.exists() or not resolved_target.is_file():
            raise ValueError(f"--target must be an existing file: {resolved_target}")
        if not is_within(resolved_target, root):
            raise ValueError(f"--target must be within ROOT ({root}): {resolved_target}")
        if resolved_target.suffix != ".py":
            raise ValueError(f"--target must be a Python file (*.py): {resolved_target}")


def output_ignore_patterns(
    root: Path,
    *,
    output: Path,
    selection_report: Path | None,
) -> list[str]:
    """Return root-anchored patterns for generated pack artifacts."""
    patterns: list[str] = []
    for candidate in (output, selection_report):
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if not is_within(resolved, root):
            continue
        rel = resolved.relative_to(root).as_posix()
        patterns.append(f"/{rel}")
        parent = resolved.parent.relative_to(root).as_posix()
        prefix = "" if parent == "." else f"{parent}/"
        if resolved.suffix:
            patterns.append(f"/{prefix}{resolved.stem}.*{resolved.suffix}")
        patterns.append(f"/{prefix}.{resolved.name}.*.tmp")
    return list(dict.fromkeys(patterns))


def enforce_selected_file_sizes(
    selected: list[DiscoveredPath],
    *,
    max_file_bytes: int,
) -> None:
    """Enforce the file-size contract only on the selected context."""
    if max_file_bytes <= 0:
        return
    oversized = [item for item in selected if not item.is_dir and item.size_bytes > max_file_bytes]
    if not oversized:
        return
    details = "\n".join(f"- {item.relative_posix}: {item.size_bytes} bytes" for item in oversized)
    raise ValueError(f"Selected files exceed max size ({max_file_bytes} bytes):\n{details}")


def is_within(path: Path, root: Path) -> bool:
    """Return whether a resolved path is inside a resolved root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
