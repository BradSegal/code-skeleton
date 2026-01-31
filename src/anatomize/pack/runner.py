"""Orchestrates `anatomize pack`."""

from __future__ import annotations

import base64
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from anatomize.core.exclude import Excluder
from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.compress import compress_python_file
from anatomize.pack.deps import PythonModuleIndex, dependency_closure, reverse_dependency_closure
from anatomize.pack.discovery import discover_paths
from anatomize.pack.formats import (
    ContentEncoding,
    PackFile,
    PackFormat,
    PackPayload,
    default_output_path,
    infer_pack_format_from_output_path,
    render,
    render_file_block,
    render_prefix,
    render_suffix,
)
from anatomize.pack.ignore import build_excluder
from anatomize.pack.jsonl import JsonlFile, iter_jsonl_file_records, iter_jsonl_prefix
from anatomize.pack.limit import LimitKind, OutputLimit
from anatomize.pack.match import GlobMatcher
from anatomize.pack.mode import PackMode
from anatomize.pack.overview import build_pack_overview
from anatomize.pack.representations import (
    FileRepresentation,
    RepresentationPolicy,
    RepresentationRule,
    compile_representation_rules,
)
from anatomize.pack.slicing import SliceBackend
from anatomize.pack.summaries import SummaryConfig, python_summary, summary_for_text
from anatomize.pack.tokens import TokenCounts, count_content_tokens_by_path, count_tokens
from anatomize.pack.tree import render_structure_tree
from anatomize.pack.uses import python_public_symbol_positions


@dataclass(frozen=True)
class PackArtifact:
    path: Path
    bytes: int
    tokens: int


@dataclass(frozen=True)
class PackResult:
    artifacts: list[PackArtifact]
    content_tokens: int
    content_token_counts: TokenCounts


@dataclass(frozen=True)
class _RenderedBlock:
    file: object
    text: str
    bytes: int
    tokens: int


@dataclass(frozen=True)
class _StagedArtifact:
    tmp_path: Path
    final_path: Path
    bytes: int
    tokens: int


HybridProcessResult = tuple[JsonlFile, PackFile, int, dict[str, Any] | None, FileRepresentation]


def _resolve_workers(requested: int, n_items: int) -> int:
    if n_items <= 1:
        return 1
    if requested <= 0:
        return max(1, min(32, os.cpu_count() or 1))
    return max(1, requested)


def _process_one_file(
    discovered: object,
    *,
    root: Path,
    python_roots: list[Path],
    compress: bool,
    line_numbers: bool,
) -> tuple[str, PackFile, str | None]:
    from anatomize.pack.discovery import DiscoveredPath

    if not isinstance(discovered, DiscoveredPath):
        raise ValueError("Internal error: unexpected discovery item")

    abs_path = discovered.absolute_path
    rel_posix = discovered.relative_posix

    if discovered.is_binary:
        return (
            rel_posix,
            PackFile(path=rel_posix, language=None, is_binary=True, content=None, content_tokens=0),
            None,
        )

    try:
        content = _read_text(abs_path)
        if compress and abs_path.suffix == ".py":
            module_name = _python_module_name_for_path(abs_path, root, python_roots=python_roots)
            content = compress_python_file(abs_path, module_name=module_name, relative_posix=rel_posix)
        if line_numbers:
            content = _add_line_numbers(content)
    except Exception as e:
        raise ValueError(f"Failed to process {rel_posix}") from e

    pf = PackFile(
        path=rel_posix,
        language=_language_for_path(abs_path),
        is_binary=False,
        content=content,
        content_tokens=0,
    )
    return rel_posix, pf, content


def _process_one_file_hybrid(
    discovered: object,
    *,
    root: Path,
    python_roots: list[Path],
    token_encoding: str,
    content_encoding: ContentEncoding,
    line_numbers: bool,
    policy: RepresentationPolicy,
    summary_cfg: SummaryConfig,
    include_files: bool,
) -> tuple[JsonlFile, PackFile, int, dict[str, Any] | None, FileRepresentation]:
    from anatomize.pack.discovery import DiscoveredPath

    if not isinstance(discovered, DiscoveredPath):
        raise ValueError("Internal error: unexpected discovery item")

    abs_path = discovered.absolute_path
    rel_posix = discovered.relative_posix
    size_bytes = discovered.size_bytes

    if discovered.is_binary:
        default_rep = FileRepresentation.META
        rep = policy.resolve(rel_posix, is_dir=False, default=default_rep)
        if rep is not FileRepresentation.META:
            raise ValueError(f"Cannot emit {rep.value} for binary file: {rel_posix}")

        jf = JsonlFile(
            path=rel_posix,
            language=None,
            is_binary=True,
            size_bytes=size_bytes,
            content_tokens=0,
            representation=FileRepresentation.META,
            summary=None,
            content_encoding=None,
            content=None,
            content_field_tokens=None,
        )
        pf = PackFile(path=rel_posix, language=None, is_binary=True, content=None, content_tokens=0)
        return jf, pf, 0, None, FileRepresentation.META

    language = _language_for_path(abs_path)
    default_rep = FileRepresentation.SUMMARY if abs_path.suffix == ".py" else FileRepresentation.META
    rep = policy.resolve(rel_posix, is_dir=False, default=default_rep)
    if rep is FileRepresentation.CONTENT and not include_files:
        raise ValueError(f"Content requested but --no-files is set: {rel_posix}")

    try:
        raw_text = _read_text(abs_path)
    except Exception as e:
        raise ValueError(f"Failed to read {rel_posix}") from e

    content_tokens = count_tokens(raw_text, encoding_name=token_encoding)
    summary: dict[str, Any] | None = None
    content: str | None = None
    content_field_tokens: int | None = None

    if rep is FileRepresentation.SUMMARY:
        if abs_path.suffix == ".py":
            module_name = _python_module_name_for_path(abs_path, root, python_roots=python_roots)
            summary = python_summary(abs_path, module_name=module_name, relative_path=rel_posix)
        else:
            summary = summary_for_text(
                suffix=abs_path.suffix,
                text=raw_text,
                rel_posix=rel_posix,
                cfg=summary_cfg,
            )
    elif rep is FileRepresentation.CONTENT:
        content = raw_text
        if line_numbers:
            content = _add_line_numbers(content)
        emitted = (
            base64.b64encode(content.encode("utf-8")).decode("ascii")
            if content_encoding is ContentEncoding.BASE64
            else content
        )
        content_field_tokens = count_tokens(emitted, encoding_name=token_encoding)

    jf = JsonlFile(
        path=rel_posix,
        language=language,
        is_binary=False,
        size_bytes=size_bytes,
        content_tokens=content_tokens,
        representation=rep,
        summary=summary,
        content_encoding=content_encoding if rep is FileRepresentation.CONTENT else None,
        content=content,
        content_field_tokens=content_field_tokens,
    )
    pf = PackFile(
        path=rel_posix,
        language=language,
        is_binary=False,
        content=content if rep is FileRepresentation.CONTENT else None,
        content_tokens=0,
    )
    return jf, pf, content_tokens, summary, rep


def pack(
    root: Path,
    *,
    output: Path | None,
    fmt: PackFormat,
    mode: PackMode = PackMode.BUNDLE,
    include: list[str],
    ignore: list[str],
    ignore_files: list[Path],
    respect_standard_ignores: bool,
    symlinks: SymlinkPolicy,
    max_file_bytes: int,
    workers: int = 0,
    token_encoding: str,
    compress: bool,
    content_encoding: ContentEncoding = ContentEncoding.FENCE_SAFE,
    line_numbers: bool = False,
    include_structure: bool = True,
    include_files: bool = True,
    max_output: OutputLimit | None = None,
    split_output: OutputLimit | None = None,
    representation_content: list[str] | None = None,
    representation_summary: list[str] | None = None,
    representation_meta: list[str] | None = None,
    fit_to_max_output: bool = False,
    summary_config: SummaryConfig | None = None,
    target: Path | None = None,
    target_module: str | None = None,
    reverse_deps: bool = False,
    uses: bool = False,
    uses_include_private: bool = False,
    slice_backend: SliceBackend = SliceBackend.IMPORTS,
    pyright_langserver_cmd: list[str] | None = None,
    entries: list[Path],
    deps: bool,
    python_roots: list[Path],
) -> PackResult:
    root = root.resolve()

    if mode is PackMode.HYBRID:
        if compress:
            raise ValueError("--compress is not supported in --mode hybrid")
        if fmt is not PackFormat.JSONL:
            raise ValueError("--mode hybrid requires --format jsonl")

    excluder = build_excluder(
        root,
        ignore=ignore,
        ignore_files=ignore_files,
        respect_standard_ignores=respect_standard_ignores,
    )

    discovered = discover_paths(
        root,
        excluder=excluder,
        include_patterns=include if include else None,
        symlinks=symlinks,
        max_file_bytes=max_file_bytes,
    )

    file_paths = [d for d in discovered if not d.is_dir]
    discovered_files_set = {d.absolute_path for d in file_paths}

    selected_files: set[Path] | None = None
    resolved_python_roots = _resolve_python_roots(root, python_roots or _default_python_roots(root))
    if entries and (target is not None or target_module is not None or reverse_deps):
        raise ValueError("Use either --entry or --target/--module selection, not both")

    if (target is not None or target_module is not None) and not reverse_deps and not deps and not uses:
        # Selecting a target without a selection mode is ambiguous.
        raise ValueError("When using --target/--module, specify --reverse-deps, --deps, and/or --uses")

    if entries:
        resolved_entries = [(root / p).resolve() if not p.is_absolute() else p.resolve() for p in entries]
        for e in resolved_entries:
            if not e.exists() or not e.is_file():
                raise ValueError(f"--entry must be an existing file: {e}")
            try:
                e.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"--entry must be within ROOT ({root}): {e}") from exc
            if deps and e.suffix != ".py":
                raise ValueError(f"--deps requires Python entry files (*.py): {e}")
        if not deps:
            selected_files = set(resolved_entries)
        else:
            index = PythonModuleIndex(resolved_python_roots, symlinks=symlinks)
            closure = dependency_closure(resolved_entries, index=index)
            selected_files = set(closure)
    elif target is not None or target_module is not None:
        index = PythonModuleIndex(resolved_python_roots, symlinks=symlinks)
        if target is not None and target_module is not None:
            raise ValueError("Specify at most one of --target or --module")
        if target is not None:
            abs_target = (root / target).resolve() if not target.is_absolute() else target.resolve()
            if not abs_target.exists() or not abs_target.is_file():
                raise ValueError(f"--target must be an existing file: {abs_target}")
            if abs_target.suffix != ".py":
                raise ValueError(f"--target must be a Python file (*.py): {abs_target}")
            target_mod = index.module_for_path(abs_target).module
        else:
            target_mod = target_module or ""

        selected: set[Path] = set()
        if reverse_deps:
            selected.update(reverse_dependency_closure(target_mod, index=index))
        if uses:
            if slice_backend is not SliceBackend.PYRIGHT:
                raise ValueError("--uses requires --slice-backend pyright (no fallback)")
            if pyright_langserver_cmd is None:
                pyright_langserver_cmd = ["pyright-langserver", "--stdio"]
            resolved = index.resolve_module(target_mod)
            if resolved is None:
                raise ValueError(f"Unknown target module: {target_mod}")
            positions = python_public_symbol_positions(resolved.path, include_private=uses_include_private)
            if positions:
                from anatomize.pack.pyright_lsp import pyright_referenced_files

                refs = pyright_referenced_files(
                    root=root,
                    target_file=resolved.path,
                    positions=positions,
                    langserver_cmd=pyright_langserver_cmd,
                    python_roots=resolved_python_roots,
                    workspace_files=[m.path for m in index.modules()],
                )
                selected.update(refs)
            selected.add(resolved.path)
        if deps:
            # Forward closure from the currently selected set (or from the target itself if reverse not requested).
            if selected:
                start = sorted(selected)
            else:
                resolved = index.resolve_module(target_mod)
                if resolved is None:
                    raise ValueError(f"Unknown target module: {target_mod}")
                start = [resolved.path]
            selected.update(dependency_closure(start, index=index))
        selected_files = selected

    if selected_files is not None:
        _ensure_required_files_included(
            root,
            required=selected_files,
            discovered_files=discovered_files_set,
            include_patterns=include,
            excluder=excluder,
            symlinks=symlinks,
        )

    selected_file_paths = [d for d in file_paths if selected_files is None or d.absolute_path in selected_files]
    size_by_rel: dict[str, int] = {d.relative_posix: d.size_bytes for d in selected_file_paths}
    is_binary_by_rel: dict[str, bool] = {d.relative_posix: d.is_binary for d in selected_file_paths}

    payload_by_path: dict[str, str] = {}
    files: list[PackFile] = []
    jsonl_files: list[JsonlFile] = []
    hybrid_per_file_tokens: dict[str, int] = {}

    if mode is PackMode.HYBRID and not include_files and representation_content:
        raise ValueError("--no-files cannot be combined with --content rules in --mode hybrid")

    if mode is not PackMode.HYBRID and not include_files:
        for f in selected_file_paths:
            files.append(
                PackFile(
                    path=f.relative_posix,
                    language=_language_for_path(f.absolute_path),
                    is_binary=f.is_binary,
                    content=None,
                    content_tokens=0,
                )
            )
    else:
        workers_resolved = _resolve_workers(workers, len(selected_file_paths))
        if mode is PackMode.HYBRID:
            summary_cfg = summary_config or SummaryConfig()
            rep_rules: list[RepresentationRule] = []
            rep_rules.extend(compile_representation_rules(representation_meta or [], FileRepresentation.META))
            rep_rules.extend(compile_representation_rules(representation_summary or [], FileRepresentation.SUMMARY))
            rep_rules.extend(compile_representation_rules(representation_content or [], FileRepresentation.CONTENT))
            policy = RepresentationPolicy(rules=rep_rules)

            if workers_resolved == 1:
                for f in selected_file_paths:
                    jf, pf, file_tokens, _summary, _rep = _process_one_file_hybrid(
                        f,
                        root=root,
                        python_roots=resolved_python_roots,
                        token_encoding=token_encoding,
                        content_encoding=content_encoding,
                        line_numbers=line_numbers,
                        policy=policy,
                        summary_cfg=summary_cfg,
                        include_files=include_files,
                    )
                    files.append(pf)
                    jsonl_files.append(jf)
                    hybrid_per_file_tokens[jf.path] = file_tokens
            else:
                errors: list[str] = []
                results_hybrid: dict[str, HybridProcessResult] = {}
                with ThreadPoolExecutor(max_workers=workers_resolved) as executor_hybrid:
                    futures_hybrid = [
                        executor_hybrid.submit(
                            _process_one_file_hybrid,
                            f,
                            root=root,
                            python_roots=resolved_python_roots,
                            token_encoding=token_encoding,
                            content_encoding=content_encoding,
                            line_numbers=line_numbers,
                            policy=policy,
                            summary_cfg=summary_cfg,
                            include_files=include_files,
                        )
                        for f in selected_file_paths
                    ]
                    for fut_hybrid in futures_hybrid:
                        try:
                            jf, pf, file_tokens, summary, rep = fut_hybrid.result()
                            results_hybrid[jf.path] = (jf, pf, file_tokens, summary, rep)
                        except Exception as e:
                            errors.append(str(e))

                if errors:
                    errors.sort()
                    raise ValueError("Failed to pack files:\n" + "\n".join(f"- {m}" for m in errors))

                for rel in sorted(results_hybrid.keys()):
                    jf, pf, file_tokens, _summary, _rep = results_hybrid[rel]
                    files.append(pf)
                    jsonl_files.append(jf)
                    hybrid_per_file_tokens[jf.path] = file_tokens
        else:
            if workers_resolved == 1:
                for f in selected_file_paths:
                    rel, pf, content = _process_one_file(
                        f,
                        root=root,
                        python_roots=resolved_python_roots,
                        compress=compress,
                        line_numbers=line_numbers,
                    )
                    files.append(pf)
                    if content is not None:
                        payload_by_path[rel] = content
            else:
                errors_bundle: list[str] = []
                results: dict[str, tuple[PackFile, str | None]] = {}
                with ThreadPoolExecutor(max_workers=workers_resolved) as executor_bundle:
                    futures_bundle = [
                        executor_bundle.submit(
                            _process_one_file,
                            f,
                            root=root,
                            python_roots=resolved_python_roots,
                            compress=compress,
                            line_numbers=line_numbers,
                        )
                        for f in selected_file_paths
                    ]
                    for fut_bundle in futures_bundle:
                        try:
                            rel, pf, content = fut_bundle.result()
                            results[rel] = (pf, content)
                        except Exception as e:
                            errors_bundle.append(str(e))

                if errors_bundle:
                    errors_bundle.sort()
                    raise ValueError("Failed to pack files:\n" + "\n".join(f"- {m}" for m in errors_bundle))

                for rel in sorted(results.keys()):
                    pf, content = results[rel]
                    files.append(pf)
                    if content is not None:
                        payload_by_path[rel] = content

    token_counts = (
        TokenCounts(
            per_file_content_tokens=hybrid_per_file_tokens,
            content_total_tokens=sum(hybrid_per_file_tokens.values()),
        )
        if mode is PackMode.HYBRID
        else count_content_tokens_by_path(payload_by_path, encoding_name=token_encoding)
    )
    files_by_path = {f.path: f for f in files}
    files = [
        PackFile(
            path=p,
            language=files_by_path[p].language,
            is_binary=files_by_path[p].is_binary,
            content=files_by_path[p].content,
            content_tokens=token_counts.per_file_content_tokens.get(p, 0),
        )
        for p in sorted(files_by_path.keys())
    ]

    selected_rel_paths = [d.relative_posix for d in selected_file_paths]
    structure_nodes: list[tuple[str, bool]] = []
    dirs: set[str] = set()
    for rel in selected_rel_paths:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    for dir_rel in sorted(dirs):
        structure_nodes.append((dir_rel, True))
    for file_rel in sorted(selected_rel_paths):
        structure_nodes.append((file_rel, False))
    structure_paths = render_structure_tree(structure_nodes)
    overview = build_pack_overview(
        root=root,
        selected_rel_paths=selected_rel_paths,
        size_by_rel=size_by_rel,
        is_binary_by_rel=is_binary_by_rel,
        content_total_tokens=token_counts.content_total_tokens,
    )
    payload = PackPayload(
        root_name=root.name,
        structure_paths=structure_paths,
        overview=overview,
        files=files,
        content_total_tokens=token_counts.content_total_tokens,
        encoding_name=token_encoding,
        compressed=compress,
        content_encoding=content_encoding,
        line_numbers=line_numbers,
        include_structure=include_structure,
        include_files=include_files,
    )

    out_path = output if output is not None else default_output_path(fmt)
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inferred_from_output = infer_pack_format_from_output_path(out_path)
    if inferred_from_output is not None and inferred_from_output is not fmt:
        raise ValueError(
            f"Output path extension implies format {inferred_from_output.value} but fmt is {fmt.value}"
        )

    if fmt is PackFormat.JSONL:
        artifacts = _write_jsonl(
            payload,
            root_dir=root,
            base_output=out_path,
            token_encoding=token_encoding,
            split_output=split_output,
            max_output=max_output,
            mode=mode,
            files=jsonl_files if mode is PackMode.HYBRID else None,
            size_by_rel=size_by_rel,
            representation_rules={
                "content": representation_content or [],
                "summary": representation_summary or [],
                "meta": representation_meta or [],
            }
            if mode is PackMode.HYBRID
            else None,
            summary_config=(
                (summary_config or SummaryConfig()).model_dump(mode="json")
                if mode is PackMode.HYBRID
                else None
            ),
            fit_to_max_output=fit_to_max_output if mode is PackMode.HYBRID else False,
        )
        return PackResult(
            artifacts=artifacts,
            content_tokens=token_counts.content_total_tokens,
            content_token_counts=token_counts,
        )

    if split_output is None and fmt in (PackFormat.MARKDOWN, PackFormat.PLAIN):
        staged = _stage_markdown_or_plain(
            payload,
            dst=out_path,
            fmt=fmt,
            token_encoding=token_encoding,
        )
        _enforce_max_output([staged], max_output=max_output)
        _commit_staged([staged])
        return PackResult(
            artifacts=[PackArtifact(path=out_path, bytes=staged.bytes, tokens=staged.tokens)],
            content_tokens=token_counts.content_total_tokens,
            content_token_counts=token_counts,
        )

    writes: list[tuple[Path, str]] = []
    if split_output is not None:
        if fmt not in (PackFormat.MARKDOWN, PackFormat.PLAIN):
            raise ValueError("--split-output is only supported for markdown/plain/jsonl formats")
        artifacts, writes = _build_split_output(
            payload,
            fmt=fmt,
            base_output=out_path,
            token_encoding=token_encoding,
            split_output=split_output,
        )
    else:
        artifact_text = render(payload, fmt=fmt)
        artifacts = [
            PackArtifact(
                path=out_path,
                bytes=len(artifact_text.encode("utf-8")),
                tokens=count_tokens(artifact_text, encoding_name=token_encoding),
            )
        ]
        writes = [(out_path, artifact_text)]

    total_bytes = sum(a.bytes for a in artifacts)
    total_tokens = sum(a.tokens for a in artifacts)
    if max_output is not None:
        if max_output.kind is LimitKind.BYTES and total_bytes > max_output.value:
            raise ValueError(f"Output exceeds --max-output ({max_output.value} bytes): {total_bytes}")
        if max_output.kind is LimitKind.TOKENS and total_tokens > max_output.value:
            raise ValueError(f"Output exceeds --max-output ({max_output.value} tokens): {total_tokens}")

    _atomic_write_many(writes)

    return PackResult(
        artifacts=artifacts,
        content_tokens=token_counts.content_total_tokens,
        content_token_counts=token_counts,
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Non-UTF-8 text file cannot be packed: {path}") from e
    except OSError as e:
        raise ValueError(f"Failed to read file: {path}") from e


def _language_for_path(path: Path) -> str | None:
    suf = path.suffix.lower()
    if suf == ".py":
        return "python"
    if suf in (".md", ".markdown"):
        return "markdown"
    if suf == ".json":
        return "json"
    if suf in (".yml", ".yaml"):
        return "yaml"
    if suf in (".toml",):
        return "toml"
    if suf in (".sh",):
        return "bash"
    return None


def _default_python_roots(root: Path) -> list[Path]:
    # Common src-layout default.
    src = root / "src"
    if src.exists() and src.is_dir():
        return [src]
    return [root]


def _resolve_python_roots(root: Path, python_roots: list[Path]) -> list[Path]:
    resolved: list[Path] = []
    for p in python_roots:
        resolved.append((root / p).resolve() if not p.is_absolute() else p.resolve())
    return resolved


def _python_module_name_for_path(path: Path, root: Path, *, python_roots: list[Path]) -> str:
    abs_path = path.resolve()
    for pr in python_roots:
        pr = pr.resolve()
        try:
            rel = abs_path.relative_to(pr)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        if not rel_posix.endswith(".py"):
            break
        mod = rel_posix[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        return mod
    return abs_path.stem


def _add_line_numbers(text: str) -> str:
    lines = text.splitlines(keepends=False)
    width = max(1, len(str(len(lines) if lines else 1)))
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        out.append(f"{i:>{width}}: {line}")
    # Preserve trailing newline behavior: always end with newline if input ended with one.
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix


def _build_split_output(
    payload: PackPayload,
    *,
    fmt: PackFormat,
    base_output: Path,
    token_encoding: str,
    split_output: OutputLimit,
) -> tuple[list[PackArtifact], list[tuple[Path, str]]]:
    if not payload.include_files:
        # Nothing to split.
        artifact_text = render(payload, fmt=fmt)
        single_artifacts = [
            PackArtifact(
                path=base_output,
                bytes=len(artifact_text.encode("utf-8")),
                tokens=count_tokens(artifact_text, encoding_name=token_encoding),
            )
        ]
        return single_artifacts, [(base_output, artifact_text)]

    # Pre-render file blocks once; plan parts without repeatedly re-rendering documents.
    blocks = _render_file_blocks(payload, fmt=fmt, token_encoding=token_encoding)

    limit = split_output.value
    prefix_first = render_prefix(payload, fmt=fmt, include_structure=True, include_overview=True)
    prefix_later = render_prefix(payload, fmt=fmt, include_structure=False, include_overview=False)
    suffix = render_suffix(payload, fmt=fmt)

    if split_output.kind is LimitKind.BYTES:
        prefix_first_size = len(prefix_first.encode("utf-8"))
        prefix_later_size = len(prefix_later.encode("utf-8"))
        suffix_size = len(suffix.encode("utf-8"))
        block_sizes = [b.bytes for b in blocks]
    else:
        prefix_first_size = count_tokens(prefix_first, encoding_name=token_encoding)
        prefix_later_size = count_tokens(prefix_later, encoding_name=token_encoding)
        suffix_size = count_tokens(suffix, encoding_name=token_encoding)
        block_sizes = [b.tokens for b in blocks]

    # Greedy plan based on component sizes; verify strict token counts after rendering each part.
    parts: list[list[_RenderedBlock]] = []
    current: list[_RenderedBlock] = []
    current_size = 0

    def current_prefix_size() -> int:
        return prefix_first_size if not parts else prefix_later_size

    for b, b_size in zip(blocks, block_sizes, strict=True):
        if not current:
            # First item in part: ensure even the minimal doc fits.
            if split_output.kind is LimitKind.BYTES:
                minimal = current_prefix_size() + b_size + suffix_size
                if minimal > limit:
                    raise ValueError("A single file block exceeds the split limit; increase --split-output")
            else:
                prefix = prefix_first if not parts else prefix_later
                tok = count_tokens(prefix + b.text + suffix, encoding_name=token_encoding)
                if tok > limit:
                    raise ValueError(
                        "A single file block exceeds the split token limit; increase --split-output"
                    )
            current.append(b)
            current_size = b_size
            continue

        candidate = current_prefix_size() + current_size + b_size + suffix_size
        if candidate <= limit:
            current.append(b)
            current_size += b_size
            continue

        parts.append(current)
        current = [b]
        current_size = b_size
        if split_output.kind is LimitKind.BYTES:
            minimal = prefix_later_size + current_size + suffix_size
            if minimal > limit:
                raise ValueError("A single file block exceeds the split limit; increase --split-output")
        else:
            tok = count_tokens(prefix_later + b.text + suffix, encoding_name=token_encoding)
            if tok > limit:
                raise ValueError("A single file block exceeds the split token limit; increase --split-output")

    if current:
        parts.append(current)

    artifacts: list[PackArtifact] = []
    writes: list[tuple[Path, str]] = []
    for i, part_blocks in enumerate(parts, start=1):
        out = _with_part_suffix(base_output, i)
        include_structure = payload.include_structure and i == 1
        part_prefix = prefix_first if i == 1 else prefix_later
        if not include_structure and i == 1:
            # If structure is disabled, rebuild the prefix without it.
            part_prefix = render_prefix(payload, fmt=fmt, include_structure=False, include_overview=True)

        text = part_prefix + "".join(b.text for b in part_blocks) + suffix

        # Strict verification for token-based splitting (BPE boundary effects) and to produce exact metrics.
        if split_output.kind is LimitKind.TOKENS:
            tok = count_tokens(text, encoding_name=token_encoding)
            while tok > limit and len(part_blocks) > 1:
                # Move the last block to the next part; bounded backtracking.
                last = part_blocks.pop()
                # Ensure a next part exists to receive it.
                if i == len(parts):
                    parts.append([last])
                else:
                    parts[i].insert(0, last)
                text = part_prefix + "".join(b.text for b in part_blocks) + suffix
                tok = count_tokens(text, encoding_name=token_encoding)
            if tok > limit:
                raise ValueError("A single file block exceeds the split token limit; increase --split-output")

        artifacts.append(
            PackArtifact(
                path=out,
                bytes=len(text.encode("utf-8")),
                tokens=count_tokens(text, encoding_name=token_encoding),
            )
        )
        writes.append((out, text))

    return artifacts, writes


def _render_file_blocks(payload: PackPayload, *, fmt: PackFormat, token_encoding: str) -> list[_RenderedBlock]:
    blocks: list[_RenderedBlock] = []
    for f in payload.files:
        text = render_file_block(payload, fmt=fmt, file=f)
        blocks.append(
            _RenderedBlock(
                file=f,
                text=text,
                bytes=len(text.encode("utf-8")),
                tokens=count_tokens(text, encoding_name=token_encoding),
            )
        )
    return blocks


def _atomic_write_many(writes: list[tuple[Path, str]]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for dst, text in writes:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(dst.parent),
                prefix=f".{dst.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            staged.append((tmp_path, dst))

        for tmp_path, dst in staged:
            os.replace(tmp_path, dst)
    finally:
        for tmp_path, _ in staged:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass


def _write_jsonl(
    payload: PackPayload,
    *,
    root_dir: Path,
    base_output: Path,
    token_encoding: str,
    split_output: OutputLimit | None,
    max_output: OutputLimit | None,
    mode: PackMode,
    files: list[JsonlFile] | None,
    size_by_rel: dict[str, int],
    representation_rules: dict[str, list[str]] | None,
    summary_config: dict[str, Any] | None,
    fit_to_max_output: bool,
) -> list[PackArtifact]:
    if files is None:
        files = _jsonl_files_from_payload(payload, token_encoding=token_encoding, size_by_rel=size_by_rel)

    max_output_str = _limit_to_str(max_output)
    split_output_str = _limit_to_str(split_output)

    if fit_to_max_output:
        if mode is not PackMode.HYBRID:
            raise ValueError("--fit-to-max-output is only supported in --mode hybrid")
        if max_output is None:
            raise ValueError("--fit-to-max-output requires --max-output")

    selection_trace: list[dict[str, Any]] = []

    def stage_current(*, files_current: list[JsonlFile]) -> list[_StagedArtifact]:
        if split_output is None:
            return [
                _stage_jsonl_part(
                    payload,
                    dst=base_output,
                    token_encoding=token_encoding,
                    files=files_current,
                    include_structure=True,
                    include_overview=True,
                    mode=mode,
                    max_output=max_output_str,
                    split_output=split_output_str,
                    fit_to_max_output=fit_to_max_output,
                    representation_rules=representation_rules,
                    summary_config=summary_config,
                    selection_trace=selection_trace if selection_trace else None,
                )
            ]

        blocks = _render_jsonl_file_blocks(payload, token_encoding=token_encoding, files=files_current)
        parts: list[list[_RenderedBlock]] = []
        current: list[_RenderedBlock] = []
        current_size = 0

        prefix_first = list(
            iter_jsonl_prefix(
                payload,
                include_structure=True,
                include_overview=True,
                mode=mode,
                max_output=max_output_str,
                split_output=split_output_str,
                fit_to_max_output=fit_to_max_output,
                representation_rules=representation_rules,
                summary_config=summary_config,
                selection_trace=selection_trace if selection_trace else None,
            )
        )
        prefix_later = list(
            iter_jsonl_prefix(
                payload,
                include_structure=False,
                include_overview=False,
                mode=mode,
                max_output=max_output_str,
                split_output=split_output_str,
                fit_to_max_output=fit_to_max_output,
                representation_rules=representation_rules,
                summary_config=summary_config,
                selection_trace=selection_trace if selection_trace else None,
            )
        )
        prefix_first_text = "".join(prefix_first)
        prefix_later_text = "".join(prefix_later)
        prefix_first_size = (
            len(prefix_first_text.encode("utf-8"))
            if split_output.kind is LimitKind.BYTES
            else count_tokens(prefix_first_text, encoding_name=token_encoding)
        )
        prefix_later_size = (
            len(prefix_later_text.encode("utf-8"))
            if split_output.kind is LimitKind.BYTES
            else count_tokens(prefix_later_text, encoding_name=token_encoding)
        )

        limit = split_output.value

        def current_prefix_size() -> int:
            return prefix_first_size if not parts else prefix_later_size

        for b in blocks:
            b_size = b.bytes if split_output.kind is LimitKind.BYTES else b.tokens
            if not current:
                if current_prefix_size() + b_size > limit:
                    raise ValueError("A single file record exceeds the split limit; increase --split-output")
                current.append(b)
                current_size = b_size
                continue

            if current_prefix_size() + current_size + b_size <= limit:
                current.append(b)
                current_size += b_size
                continue

            parts.append(current)
            current = [b]
            current_size = b_size

        if current:
            parts.append(current)

        if split_output.kind is LimitKind.TOKENS:
            # Strict verification for BPE boundary effects.
            i = 0
            while i < len(parts):
                part = parts[i]
                prefix_text = prefix_first_text if i == 0 else prefix_later_text
                text = prefix_text + "".join(b.text for b in part)
                tok = count_tokens(text, encoding_name=token_encoding)
                while tok > limit and len(part) > 1:
                    last = part.pop()
                    if i + 1 >= len(parts):
                        parts.append([last])
                    else:
                        parts[i + 1].insert(0, last)
                    text = prefix_text + "".join(b.text for b in part)
                    tok = count_tokens(text, encoding_name=token_encoding)
                if tok > limit:
                    raise ValueError("A single file record exceeds the split token limit; increase --split-output")
                i += 1

        staged_parts: list[_StagedArtifact] = []
        for i, part in enumerate(parts, start=1):
            dst = _with_part_suffix(base_output, i)
            include_structure = i == 1
            part_files: list[JsonlFile] = [cast(JsonlFile, b.file) for b in part]
            staged_parts.append(
                _stage_jsonl_part(
                    payload,
                    dst=dst,
                    token_encoding=token_encoding,
                    files=part_files,
                    include_structure=include_structure,
                    include_overview=i == 1,
                    mode=mode,
                    max_output=max_output_str,
                    split_output=split_output_str,
                    fit_to_max_output=fit_to_max_output,
                    representation_rules=representation_rules,
                    summary_config=summary_config,
                    selection_trace=selection_trace if selection_trace else None,
                )
            )
        return staged_parts

    def discard(staged: list[_StagedArtifact]) -> None:
        for s in staged:
            try:
                if s.tmp_path.exists():
                    s.tmp_path.unlink()
            except OSError:
                pass

    def totals(staged: list[_StagedArtifact]) -> tuple[int, int]:
        return sum(s.bytes for s in staged), sum(s.tokens for s in staged)

    def within(staged: list[_StagedArtifact]) -> bool:
        if max_output is None:
            return True
        total_bytes, total_tokens = totals(staged)
        if max_output.kind is LimitKind.BYTES:
            return total_bytes <= max_output.value
        return total_tokens <= max_output.value

    # Fit loop: stage, verify, and downgrade deterministically if requested.
    current_files = files
    attempts = 0
    while True:
        attempts += 1
        staged = stage_current(files_current=current_files)
        if within(staged):
            _commit_staged(staged)
            return [PackArtifact(path=s.final_path, bytes=s.bytes, tokens=s.tokens) for s in staged]

        discard(staged)
        if not fit_to_max_output:
            total_bytes, total_tokens = totals(staged)
            if max_output is not None:
                if max_output.kind is LimitKind.BYTES:
                    raise ValueError(f"Output exceeds --max-output ({max_output.value} bytes): {total_bytes}")
                raise ValueError(f"Output exceeds --max-output ({max_output.value} tokens): {total_tokens}")
            raise ValueError("Output exceeds --max-output")

        if attempts > 10:
            raise ValueError("Failed to fit output to --max-output after multiple attempts")

        if representation_rules is None:
            raise ValueError("Internal error: fit requested without representation policy")

        assert max_output is not None
        content_policy = RepresentationPolicy(
            rules=compile_representation_rules(representation_rules.get("content", []), FileRepresentation.CONTENT)
        )

        max_kind = max_output.kind
        total_bytes, total_tokens = totals(staged)
        over = (
            (total_bytes - max_output.value)
            if max_kind is LimitKind.BYTES
            else (total_tokens - max_output.value)
        )

        # Identify downgrade candidates and compute deterministic savings.
        candidates: list[tuple[int, str]] = []

        def record_cost(f: JsonlFile) -> int:
            text = "".join(iter_jsonl_file_records(payload, files=[f]))
            if max_kind is LimitKind.BYTES:
                return len(text.encode("utf-8"))
            return count_tokens(text, encoding_name=token_encoding)

        for f in current_files:
            if f.representation is FileRepresentation.SUMMARY:
                meta_f = replace(
                    f,
                    representation=FileRepresentation.META,
                    summary=None,
                    content_encoding=None,
                    content=None,
                    content_field_tokens=None,
                )
                savings = record_cost(f) - record_cost(meta_f)
                if savings > 0:
                    candidates.append((savings, f.path))

        if not candidates:
            raise ValueError("Output exceeds --max-output and cannot be fit by downgrading representations")

        candidates.sort(key=lambda t: (-t[0], t[1]))
        savings_by_path = {p: s for s, p in candidates}
        savings_total = sum(s for s, _ in candidates)
        if savings_total < over:
            raise ValueError("Output exceeds --max-output and cannot be fit by downgrading representations")

        # Apply the smallest set of downgrades that should satisfy the budget.
        updated: list[JsonlFile] = []
        to_downgrade: set[str] = set()
        acc = 0
        for s, p in candidates:
            to_downgrade.add(p)
            acc += s
            if acc >= over:
                break

        for f in current_files:
            if f.path in to_downgrade and f.representation is FileRepresentation.SUMMARY:
                selection_trace.append(
                    {
                        "path": f.path,
                        "from": "summary",
                        "to": "meta",
                        "savings": savings_by_path[f.path],
                        "unit": "bytes" if max_kind is LimitKind.BYTES else "tokens",
                    }
                )
                updated.append(
                    replace(
                        f,
                        representation=FileRepresentation.META,
                        summary=None,
                        content_encoding=None,
                        content=None,
                        content_field_tokens=None,
                    )
                )
            elif f.path in to_downgrade and f.representation is FileRepresentation.CONTENT:
                # CONTENT is considered pinned in hybrid unless it does not match the explicit content policy.
                pinned = (
                    content_policy.resolve(f.path, is_dir=False, default=FileRepresentation.META)
                    is FileRepresentation.CONTENT
                )
                if pinned:
                    updated.append(f)
                else:
                    # Best-effort downgrade content -> summary for supported types; otherwise meta.
                    abs_path = (root_dir / f.path).resolve()
                    if abs_path.suffix == ".py":
                        module_name = _python_module_name_for_path(
                            abs_path,
                            root_dir,
                            python_roots=_default_python_roots(root_dir),
                        )
                        summ = python_summary(abs_path, module_name=module_name, relative_path=f.path)
                        updated.append(
                            replace(
                                f,
                                representation=FileRepresentation.SUMMARY,
                                summary=summ,
                                content_encoding=None,
                                content=None,
                                content_field_tokens=None,
                            )
                        )
                    else:
                        if summary_config is None:
                            updated.append(
                                replace(
                                    f,
                                    representation=FileRepresentation.META,
                                    content_encoding=None,
                                    content=None,
                                    content_field_tokens=None,
                                )
                            )
                        else:
                            cfg = SummaryConfig.model_validate(summary_config)
                            summ = summary_for_text(
                                suffix=abs_path.suffix,
                                text=abs_path.read_text(encoding="utf-8"),
                                rel_posix=f.path,
                                cfg=cfg,
                            )
                            updated.append(
                                replace(
                                    f,
                                    representation=FileRepresentation.SUMMARY,
                                    summary=summ,
                                    content_encoding=None,
                                    content=None,
                                    content_field_tokens=None,
                                )
                            )
            else:
                updated.append(f)

        current_files = updated


def _render_jsonl_file_blocks(
    payload: PackPayload, *, token_encoding: str, files: list[JsonlFile]
) -> list[_RenderedBlock]:
    blocks: list[_RenderedBlock] = []
    for f in files:
        text = "".join(iter_jsonl_file_records(payload, files=[f]))
        blocks.append(
            _RenderedBlock(
                file=f,
                text=text,
                bytes=len(text.encode("utf-8")),
                tokens=count_tokens(text, encoding_name=token_encoding),
            )
        )
    return blocks


def _stage_jsonl_part(
    payload: PackPayload,
    *,
    dst: Path,
    token_encoding: str,
    files: list[JsonlFile],
    include_structure: bool,
    include_overview: bool,
    mode: PackMode,
    max_output: str | None,
    split_output: str | None,
    fit_to_max_output: bool,
    representation_rules: dict[str, list[str]] | None,
    summary_config: dict[str, Any] | None,
    selection_trace: list[dict[str, Any]] | None,
) -> _StagedArtifact:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(dst.parent),
        prefix=f".{dst.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        tmp_path = Path(f.name)
        for chunk in iter_jsonl_prefix(
            payload,
            include_structure=include_structure,
            include_overview=include_overview,
            mode=mode,
            max_output=max_output,
            split_output=split_output,
            fit_to_max_output=fit_to_max_output,
            representation_rules=representation_rules,
            summary_config=summary_config,
            selection_trace=selection_trace,
        ):
            f.write(chunk)
        for chunk in iter_jsonl_file_records(payload, files=files):
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())

    bytes_ = tmp_path.stat().st_size
    tokens = count_tokens(tmp_path.read_text(encoding="utf-8"), encoding_name=token_encoding)
    return _StagedArtifact(tmp_path=tmp_path, final_path=dst, bytes=bytes_, tokens=tokens)


def _jsonl_files_from_payload(
    payload: PackPayload, *, token_encoding: str, size_by_rel: dict[str, int]
) -> list[JsonlFile]:
    out: list[JsonlFile] = []
    for pf in payload.files:
        rep = (
            FileRepresentation.CONTENT
            if payload.include_files and not pf.is_binary
            else FileRepresentation.META
        )
        content: str | None = pf.content if rep is FileRepresentation.CONTENT else None
        content_field_tokens: int | None = None
        if content is not None:
            emitted = (
                base64.b64encode(content.encode("utf-8")).decode("ascii")
                if payload.content_encoding is ContentEncoding.BASE64
                else content
            )
            content_field_tokens = count_tokens(emitted, encoding_name=token_encoding)
        out.append(
            JsonlFile(
                path=pf.path,
                language=pf.language,
                is_binary=pf.is_binary,
                size_bytes=size_by_rel.get(pf.path, 0),
                content_tokens=pf.content_tokens,
                representation=rep,
                summary=None,
                content_encoding=payload.content_encoding if content is not None else None,
                content=content,
                content_field_tokens=content_field_tokens,
            )
        )
    return out


def _limit_to_str(limit: OutputLimit | None) -> str | None:
    if limit is None:
        return None
    if limit.kind is LimitKind.TOKENS:
        return f"{limit.value}t"
    return f"{limit.value}b"


def _enforce_max_output(staged: list[_StagedArtifact], *, max_output: OutputLimit | None) -> None:
    if max_output is None:
        return
    total_bytes = sum(s.bytes for s in staged)
    total_tokens = sum(s.tokens for s in staged)
    if max_output.kind is LimitKind.BYTES and total_bytes > max_output.value:
        raise ValueError(f"Output exceeds --max-output ({max_output.value} bytes): {total_bytes}")
    if max_output.kind is LimitKind.TOKENS and total_tokens > max_output.value:
        raise ValueError(f"Output exceeds --max-output ({max_output.value} tokens): {total_tokens}")


def _commit_staged(staged: list[_StagedArtifact]) -> None:
    try:
        for s in staged:
            os.replace(s.tmp_path, s.final_path)
    finally:
        for s in staged:
            try:
                if s.tmp_path.exists():
                    s.tmp_path.unlink()
            except OSError:
                pass


def _stage_markdown_or_plain(
    payload: PackPayload,
    *,
    dst: Path,
    fmt: PackFormat,
    token_encoding: str,
) -> _StagedArtifact:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(dst.parent),
        prefix=f".{dst.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        tmp_path = Path(f.name)
        f.write(render_prefix(payload, fmt=fmt, include_structure=True))
        if payload.include_files:
            for pf in payload.files:
                f.write(render_file_block(payload, fmt=fmt, file=pf))
        f.write(render_suffix(payload, fmt=fmt))
        f.flush()
        os.fsync(f.fileno())

    bytes_ = tmp_path.stat().st_size
    tokens = count_tokens(tmp_path.read_text(encoding="utf-8"), encoding_name=token_encoding)
    return _StagedArtifact(tmp_path=tmp_path, final_path=dst, bytes=bytes_, tokens=tokens)


def _with_part_suffix(path: Path, part: int) -> Path:
    # anatomize-pack.md -> anatomize-pack.1.md
    if path.suffix:
        return path.with_name(f"{path.stem}.{part}{path.suffix}")
    return path.with_name(f"{path.name}.{part}")


def _ensure_required_files_included(
    root: Path,
    *,
    required: set[Path],
    discovered_files: set[Path],
    include_patterns: list[str],
    excluder: Excluder,
    symlinks: SymlinkPolicy,
) -> None:
    missing = sorted([p for p in required if p not in discovered_files])
    if not missing:
        return

    include_matcher = GlobMatcher(include_patterns) if include_patterns else None

    reasons: list[str] = []
    for abs_path in missing:
        rel = abs_path.relative_to(root).as_posix() if _is_within(abs_path, root) else abs_path.as_posix()
        reason = "excluded"
        try:
            is_symlink = abs_path.is_symlink()
        except OSError:
            is_symlink = False

        if is_symlink:
            if abs_path.is_dir() and symlinks not in (SymlinkPolicy.DIRS, SymlinkPolicy.ALL):
                reason = "symlink dir forbidden by --symlinks"
            elif abs_path.is_file() and symlinks not in (SymlinkPolicy.FILES, SymlinkPolicy.ALL):
                reason = "symlink file forbidden by --symlinks"

        if include_matcher is not None and reason == "excluded":
            if not include_matcher.matches_any(rel, is_dir=False):
                reason = "does not match --include patterns"

        if reason == "excluded":
            # Best-effort: check ignore rules.
            if excluder.is_excluded(rel, is_dir=False):
                reason = "excluded by ignore rules"

        reasons.append(f"- {rel}: {reason}")

    raise ValueError(
        "Selection requires files that were not included by filtering:\n"
        + "\n".join(reasons)
        + "\n\nAdjust --include/--ignore (or use `!negation` rules) so required files are eligible."
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
