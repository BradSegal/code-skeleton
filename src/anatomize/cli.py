"""Command-line interface for anatomize.

This module provides the CLI for generating and validating skeleton outputs.

Usage
-----
    anatomize generate ./src --output .skeleton
    anatomize validate .skeleton --source ./src
    anatomize estimate ./src --level modules
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from anatomize.config import SkeletonConfig
from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import ResolutionLevel
from anatomize.formats import OutputFormat, write_skeleton
from anatomize.generators.main import SkeletonGenerator
from anatomize.pack.formats import ContentEncoding, PackFormat
from anatomize.pack.limit import OutputLimit, parse_output_limit
from anatomize.pack.mode import PackMode
from anatomize.pack.slicing import SliceBackend
from anatomize.pack.summaries import SummaryConfig
from anatomize.pack.tree import render_token_tree

app = typer.Typer(
    name="anatomize",
    help="Deterministic, token-efficient codebase packs and skeleton maps for AI review (Python).",
    add_completion=False,
)

logger = logging.getLogger(__name__)

def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        from anatomize.version import __version__

        typer.echo(f"anatomize {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose output."),
    ] = False,
) -> None:
    """Token-efficient codebase packs and skeleton maps."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


def _load_config_from(config_path: Path | None) -> SkeletonConfig | None:
    if config_path is None:
        return SkeletonConfig.find_config()
    return SkeletonConfig.from_file(config_path)


def _load_config_for_root(config_path: Path | None, root: Path) -> SkeletonConfig | None:
    if config_path is not None:
        return SkeletonConfig.from_file(config_path)
    return SkeletonConfig.find_config(start_dir=root)


def _resolve_sources(cli_sources: list[Path], config: SkeletonConfig | None) -> list[Path]:
    if cli_sources:
        return cli_sources
    if config is None:
        raise typer.BadParameter("No sources provided and no .anatomize.yaml found")
    return [Path(s) for s in config.sources]


def _resolve_level(level: str | None, config: SkeletonConfig | None) -> ResolutionLevel:
    if level is not None:
        return ResolutionLevel(level)
    if config is None:
        return ResolutionLevel.MODULES
    return config.level


def _resolve_formats(formats: list[str] | None, config: SkeletonConfig | None) -> list[OutputFormat]:
    if formats is None:
        return config.formats if config is not None else [OutputFormat.YAML]
    resolved: list[OutputFormat] = []
    for fmt in formats:
        resolved.append(OutputFormat(fmt))
    return resolved


def _resolve_symlinks(config: SkeletonConfig | None) -> SymlinkPolicy:
    if config is None:
        return SymlinkPolicy.FORBID
    return config.symlinks


def _resolve_workers(config: SkeletonConfig | None) -> int:
    if config is None:
        return 0
    return config.workers


@app.command()
def generate(
    sources: Annotated[
        list[Path],
        typer.Argument(
            help="Source directory(ies) to analyze. If omitted, uses .anatomize.yaml sources.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = [],
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to .anatomize.yaml (overrides auto-discovery).", exists=True),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output directory for skeleton files.",
        ),
    ] = None,
    level: Annotated[
        str | None,
        typer.Option(
            "--level",
            "-l",
            help="Resolution level: hierarchy, modules, or signatures.",
        ),
    ] = None,
    format: Annotated[
        list[str] | None,
        typer.Option(
            "--format",
            "-f",
            help="Output format(s): yaml, json, markdown. Can be specified multiple times.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Exclude pattern(s). Overrides config excludes.", show_default=False),
    ] = None,
    symlinks: Annotated[
        SymlinkPolicy | None,
        typer.Option("--symlinks", help="Symlink policy: forbid, files, dirs, all."),
    ] = None,
    workers: Annotated[
        int | None,
        typer.Option("--workers", help="Worker count for extraction. 0 means auto.", min=0),
    ] = None,
) -> None:
    """Generate a skeleton for a source directory.

    Examples
    --------
        anatomize generate ./src
        anatomize generate ./src --output .skeleton --level signatures
        anatomize generate ./src -f yaml -f json -f markdown
    """
    try:
        cfg = _load_config_from(config)
        resolved_sources = _resolve_sources(sources, cfg)
        resolution = _resolve_level(level, cfg)
        formats = _resolve_formats(format, cfg)

        out_dir = output if output is not None else Path(cfg.output) if cfg is not None else Path(".skeleton")
        resolved_exclude = exclude if exclude is not None else cfg.exclude if cfg is not None else None
        resolved_symlinks = symlinks if symlinks is not None else _resolve_symlinks(cfg)
        resolved_workers = workers if workers is not None else _resolve_workers(cfg)

        typer.echo(f"Generating skeleton for {', '.join(str(s) for s in resolved_sources)}...")

        # Generate skeleton
        generator = SkeletonGenerator(
            sources=resolved_sources,
            exclude=resolved_exclude,
            symlinks=resolved_symlinks,
            workers=resolved_workers,
        )
        skeleton = generator.generate(level=resolution)

        # Write output
        write_skeleton(skeleton, out_dir, formats=formats)

        # Print summary
        typer.echo("")
        typer.echo("Summary:")
        typer.echo(f"  Packages:  {skeleton.metadata.total_packages}")
        typer.echo(f"  Modules:   {skeleton.metadata.total_modules}")
        typer.echo(f"  Classes:   {skeleton.metadata.total_classes}")
        typer.echo(f"  Functions: {skeleton.metadata.total_functions}")
        typer.echo(f"  Tokens:    ~{skeleton.metadata.token_estimate:,}")
        typer.echo("")
        typer.echo(f"Output written to: {out_dir}")

    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        logger.exception("Failed to generate skeleton")
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def estimate(
    sources: Annotated[
        list[Path],
        typer.Argument(
            help="Source directory(ies) to analyze. If omitted, uses .anatomize.yaml sources.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = [],
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to .anatomize.yaml (overrides auto-discovery).", exists=True),
    ] = None,
    level: Annotated[
        str | None,
        typer.Option(
            "--level",
            "-l",
            help="Resolution level: hierarchy, modules, or signatures.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Exclude pattern(s). Overrides config excludes.", show_default=False),
    ] = None,
    symlinks: Annotated[
        SymlinkPolicy | None,
        typer.Option("--symlinks", help="Symlink policy: forbid, files, dirs, all."),
    ] = None,
    workers: Annotated[
        int | None,
        typer.Option("--workers", help="Worker count for extraction. 0 means auto.", min=0),
    ] = None,
) -> None:
    """Estimate token count for a source directory.

    Examples
    --------
        anatomize estimate ./src
        anatomize estimate ./src --level signatures
    """
    try:
        cfg = _load_config_from(config)
        resolved_sources = _resolve_sources(sources, cfg)
        resolution = _resolve_level(level, cfg)
        resolved_exclude = exclude if exclude is not None else cfg.exclude if cfg is not None else None
        resolved_symlinks = symlinks if symlinks is not None else _resolve_symlinks(cfg)
        resolved_workers = workers if workers is not None else _resolve_workers(cfg)

        typer.echo(
            f"Estimating tokens for {', '.join(str(s) for s in resolved_sources)} at level '{resolution.value}'..."
        )

        generator = SkeletonGenerator(
            sources=resolved_sources,
            exclude=resolved_exclude,
            symlinks=resolved_symlinks,
            workers=resolved_workers,
        )
        skeleton = generator.generate(level=resolution)

        typer.echo("")
        typer.echo("Estimation:")
        typer.echo(f"  Modules:   {skeleton.metadata.total_modules}")
        typer.echo(f"  Classes:   {skeleton.metadata.total_classes}")
        typer.echo(f"  Functions: {skeleton.metadata.total_functions}")
        typer.echo(f"  Tokens:    ~{skeleton.metadata.token_estimate:,}")

    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        logger.exception("Failed to estimate tokens")
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def validate(
    skeleton_dir: Annotated[
        Path,
        typer.Argument(
            help="Skeleton directory to validate.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    sources: Annotated[
        list[Path],
        typer.Option(
            "--source",
            "-s",
            help="Source directory(ies) to validate against. Can be specified multiple times.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = [],
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to .anatomize.yaml (overrides auto-discovery).", exists=True),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Exclude pattern(s). Overrides config excludes.", show_default=False),
    ] = None,
    symlinks: Annotated[
        SymlinkPolicy | None,
        typer.Option("--symlinks", help="Symlink policy: forbid, files, dirs, all."),
    ] = None,
    workers: Annotated[
        int | None,
        typer.Option("--workers", help="Worker count for extraction. 0 means auto.", min=0),
    ] = None,
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Rewrite skeleton output to match regenerated content."),
    ] = False,
) -> None:
    """Validate skeleton against source directories.

    Examples
    --------
        anatomize validate .skeleton --source ./src
        anatomize validate .skeleton -s ./src -s ./tests --fix
    """
    try:
        from anatomize.validation import validate_skeleton_dir

        cfg = _load_config_from(config)
        resolved_sources = _resolve_sources(sources, cfg)
        resolved_exclude = exclude if exclude is not None else cfg.exclude if cfg is not None else None
        resolved_symlinks = symlinks if symlinks is not None else _resolve_symlinks(cfg)
        resolved_workers = workers if workers is not None else _resolve_workers(cfg)

        fixed = validate_skeleton_dir(
            skeleton_dir=skeleton_dir,
            sources=resolved_sources,
            exclude=resolved_exclude,
            symlinks=resolved_symlinks,
            workers=resolved_workers,
            fix=fix,
        )
        typer.echo("Skeleton updated." if fixed else "Validation passed.")

    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("Failed to validate skeleton")
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def pack(
    root: Annotated[
        Path,
        typer.Argument(
            help="Repository root to pack.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = Path("."),
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to .anatomize.yaml (overrides auto-discovery).", exists=True),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output file path (default derived from format)."),
    ] = None,
    format: Annotated[
        str | None,
        typer.Option("--format", "-f", help="Output format: markdown, plain, json, xml, jsonl."),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Pack mode: bundle or hybrid (hybrid requires jsonl)."),
    ] = None,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="Include pattern(s). Repeatable allowlist; when absent, includes all."),
    ] = None,
    ignore: Annotated[
        list[str] | None,
        typer.Option("--ignore", help="Ignore pattern(s). Repeatable; supports gitignore-style `!` negation."),
    ] = None,
    ignore_file: Annotated[
        list[Path] | None,
        typer.Option(
            "--ignore-file",
            help="Ignore file(s) containing patterns (e.g. .repomixignore). Repeatable.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    respect_standard_ignores: Annotated[
        bool | None,
        typer.Option(
            "--respect-standard-ignores/--no-respect-standard-ignores",
            help="Load .repomixignore/.ignore/.gitignore/.git/info/exclude from ROOT when present.",
        ),
    ] = None,
    symlinks: Annotated[
        SymlinkPolicy | None,
        typer.Option("--symlinks", help="Symlink policy: forbid, files, dirs, all."),
    ] = None,
    max_file_bytes: Annotated[
        int | None,
        typer.Option("--max-file-bytes", help="Hard max bytes per file (0 disables).", min=0),
    ] = None,
    workers: Annotated[
        int | None,
        typer.Option("--workers", help="Worker count for pack. 0 means auto.", min=0),
    ] = None,
    token_encoding: Annotated[
        str | None,
        typer.Option("--token-encoding", help="tiktoken encoding name used for token counts."),
    ] = None,
    token_count_tree: Annotated[
        bool,
        typer.Option("--token-count-tree", help="Print a per-file token tree to stdout."),
    ] = False,
    compress: Annotated[
        bool | None,
        typer.Option("--compress", help="Emit compressed structural output for Python files."),
    ] = None,
    content_encoding: Annotated[
        str | None,
        typer.Option("--content-encoding", help="Content encoding: verbatim, fence-safe, or base64."),
    ] = None,
    content: Annotated[
        list[str] | None,
        typer.Option("--content", help="Hybrid: force full content for matching paths. Repeatable."),
    ] = None,
    summary: Annotated[
        list[str] | None,
        typer.Option("--summary", help="Hybrid: force summaries for matching paths. Repeatable."),
    ] = None,
    meta: Annotated[
        list[str] | None,
        typer.Option("--meta", help="Hybrid: force metadata-only for matching paths. Repeatable."),
    ] = None,
    fit_to_max_output: Annotated[
        bool | None,
        typer.Option(
            "--fit-to-max-output/--no-fit-to-max-output",
            help="Hybrid: fit within --max-output by downgrading representations.",
        ),
    ] = None,
    summary_depth: Annotated[
        int | None,
        typer.Option("--summary-depth", help="Hybrid: max depth for structured summaries.", min=1),
    ] = None,
    summary_max_keys: Annotated[
        int | None,
        typer.Option("--summary-max-keys", help="Hybrid: max keys for structured summaries.", min=1),
    ] = None,
    summary_max_items: Annotated[
        int | None,
        typer.Option("--summary-max-items", help="Hybrid: max items for structured summaries.", min=1),
    ] = None,
    summary_max_headings: Annotated[
        int | None,
        typer.Option("--summary-max-headings", help="Hybrid: max Markdown headings.", min=1),
    ] = None,
    line_numbers: Annotated[
        bool | None,
        typer.Option("--line-numbers", help="Prefix each line with its line number."),
    ] = None,
    no_structure: Annotated[
        bool | None,
        typer.Option("--no-structure", help="Omit directory structure section from the output."),
    ] = None,
    no_files: Annotated[
        bool | None,
        typer.Option("--no-files", help="Omit file contents from the output (metadata/structure only)."),
    ] = None,
    max_output: Annotated[
        str | None,
        typer.Option("--max-output", help="Hard max output size (e.g. 20_000t, 500kb)."),
    ] = None,
    split_output: Annotated[
        str | None,
        typer.Option("--split-output", help="Split output into multiple numbered files (e.g. 20_000t, 500kb)."),
    ] = None,
    target: Annotated[
        Path | None,
        typer.Option("--target", help="Target Python file for slicing (resolved relative to ROOT)."),
    ] = None,
    module: Annotated[
        str | None,
        typer.Option("--module", help="Target Python module name for slicing (e.g. package.sub)."),
    ] = None,
    reverse_deps: Annotated[
        bool,
        typer.Option("--reverse-deps", help="Select modules that import the target module (transitively)."),
    ] = False,
    uses: Annotated[
        bool,
        typer.Option("--uses", help="Select files that reference symbols from the target (requires pyright backend)."),
    ] = False,
    slice_backend: Annotated[
        str | None,
        typer.Option("--slice-backend", help="Slicing backend: imports or pyright."),
    ] = None,
    uses_include_private: Annotated[
        bool | None,
        typer.Option("--uses-include-private", help="Include private (underscore) symbols when computing references."),
    ] = None,
    pyright_langserver_cmd: Annotated[
        str | None,
        typer.Option(
            "--pyright-langserver-cmd",
            help="Command to run pyright language server (e.g. 'pyright-langserver --stdio').",
        ),
    ] = None,
    entry: Annotated[
        list[Path],
        typer.Option(
            "--entry",
            help="Entry file(s) for dependency closure. Paths are resolved relative to ROOT if not absolute.",
        ),
    ] = [],
    deps: Annotated[
        bool | None,
        typer.Option(
            "--deps/--no-deps",
            help="If enabled, select the transitive closure of local imports from --entry files. "
            "Defaults to enabled when any --entry is provided.",
        ),
    ] = None,
    python_root: Annotated[
        list[Path] | None,
        typer.Option(
            "--python-root",
            help="Python import root(s) for dependency resolution (default: ROOT/src if present, else ROOT).",
        ),
    ] = None,
) -> None:
    """Pack a repository into a single deterministic artifact.

    Examples
    --------
        anatomize pack . --format markdown --output pack.md
        anatomize pack . --include 'src/**' --ignore '**/__pycache__/**'
        anatomize pack . --entry src/anatomize/cli.py --deps --compress
    """
    try:
        from anatomize.config import PackConfig
        from anatomize.pack.formats import infer_pack_format_from_output_path
        from anatomize.pack.runner import pack as run_pack

        cfg = _load_config_for_root(config, root)
        pack_cfg = cfg.pack if cfg is not None and cfg.pack is not None else PackConfig()

        resolved_mode = pack_cfg.mode if mode is None else PackMode(mode)
        resolved_output: Path | None
        if output is not None:
            resolved_output = output
        elif pack_cfg.output is not None:
            resolved_output = (root / pack_cfg.output).resolve()
        else:
            resolved_output = None

        inferred_from_output = (
            infer_pack_format_from_output_path(resolved_output) if resolved_output is not None else None
        )
        if format is not None:
            resolved_fmt = PackFormat(format)
            if inferred_from_output is not None and inferred_from_output is not resolved_fmt:
                raise ValueError(
                    f"--output extension implies format {inferred_from_output.value} but --format is "
                    f"{resolved_fmt.value}"
                )
        else:
            if resolved_mode is PackMode.HYBRID:
                if inferred_from_output is not None and inferred_from_output is not PackFormat.JSONL:
                    raise ValueError("--mode hybrid requires JSONL output (.jsonl)")
                resolved_fmt = PackFormat.JSONL
            else:
                resolved_fmt = inferred_from_output if inferred_from_output is not None else pack_cfg.format

        resolved_deps = bool(entry) if deps is None else deps
        resolved_backend = pack_cfg.slice_backend if slice_backend is None else SliceBackend(slice_backend)

        resolved_include = pack_cfg.include if include is None else include
        resolved_ignore = pack_cfg.ignore if ignore is None else ignore
        resolved_ignore_files = (
            [Path(p) for p in pack_cfg.ignore_files] if ignore_file is None else ignore_file
        )
        resolved_ignore_files = [((root / p).resolve() if not p.is_absolute() else p) for p in resolved_ignore_files]

        resolved_respect_ignores = (
            pack_cfg.respect_standard_ignores if respect_standard_ignores is None else respect_standard_ignores
        )
        resolved_symlinks = pack_cfg.symlinks if symlinks is None else symlinks
        resolved_max_file_bytes = pack_cfg.max_file_bytes if max_file_bytes is None else max_file_bytes
        resolved_workers = pack_cfg.workers if workers is None else workers
        resolved_token_encoding = pack_cfg.token_encoding if token_encoding is None else token_encoding
        resolved_compress = pack_cfg.compress if compress is None else compress
        resolved_content_encoding = (
            pack_cfg.content_encoding if content_encoding is None else ContentEncoding(content_encoding)
        )
        resolved_fit = pack_cfg.fit_to_max_output if fit_to_max_output is None else fit_to_max_output
        resolved_content_rules = pack_cfg.content if content is None else content
        resolved_summary_rules = pack_cfg.summary if summary is None else summary
        resolved_meta_rules = pack_cfg.meta if meta is None else meta
        scfg = pack_cfg.summary_config
        summary_cfg = SummaryConfig(
            max_depth=scfg.max_depth if summary_depth is None else summary_depth,
            max_keys=scfg.max_keys if summary_max_keys is None else summary_max_keys,
            max_items=scfg.max_items if summary_max_items is None else summary_max_items,
            max_headings=scfg.max_headings if summary_max_headings is None else summary_max_headings,
        )
        resolved_line_numbers = pack_cfg.line_numbers if line_numbers is None else line_numbers
        resolved_no_structure = pack_cfg.no_structure if no_structure is None else no_structure
        resolved_no_files = pack_cfg.no_files if no_files is None else no_files
        if max_output is None:
            max_output = pack_cfg.max_output
        if split_output is None:
            split_output = pack_cfg.split_output
        resolved_max_output: OutputLimit | None = parse_output_limit(max_output) if max_output else None
        resolved_split_output: OutputLimit | None = parse_output_limit(split_output) if split_output else None

        resolved_uses_include_private = (
            pack_cfg.uses_include_private if uses_include_private is None else uses_include_private
        )
        resolved_pyright_cmd = (
            pack_cfg.pyright_langserver_cmd if pyright_langserver_cmd is None else pyright_langserver_cmd
        )
        resolved_python_roots = (
            [Path(p) for p in pack_cfg.python_roots] if python_root is None else python_root
        )

        res = run_pack(
            root=root,
            output=resolved_output,
            fmt=resolved_fmt,
            mode=resolved_mode,
            include=resolved_include,
            ignore=resolved_ignore,
            ignore_files=resolved_ignore_files,
            respect_standard_ignores=resolved_respect_ignores,
            symlinks=resolved_symlinks,
            max_file_bytes=resolved_max_file_bytes,
            workers=resolved_workers,
            token_encoding=resolved_token_encoding,
            compress=resolved_compress,
            content_encoding=resolved_content_encoding,
            line_numbers=resolved_line_numbers,
            include_structure=not resolved_no_structure,
            include_files=not resolved_no_files,
            max_output=resolved_max_output,
            split_output=resolved_split_output,
            representation_content=resolved_content_rules,
            representation_summary=resolved_summary_rules,
            representation_meta=resolved_meta_rules,
            fit_to_max_output=resolved_fit,
            summary_config=summary_cfg,
            target=target,
            target_module=module,
            reverse_deps=reverse_deps,
            uses=uses,
            uses_include_private=resolved_uses_include_private,
            slice_backend=resolved_backend,
            pyright_langserver_cmd=_split_cmd(resolved_pyright_cmd),
            entries=entry,
            deps=resolved_deps,
            python_roots=resolved_python_roots,
        )

        for a in res.artifacts:
            typer.echo(f"Wrote: {a.path}")
        total_artifact_tokens = sum(a.tokens for a in res.artifacts)
        typer.echo(f"Artifact tokens: {total_artifact_tokens:,} ({resolved_token_encoding})")
        typer.echo(f"Content tokens:  {res.content_tokens:,} ({resolved_token_encoding})")

        if token_count_tree:
            typer.echo("")
            typer.echo("Content token tree:")
            for line in render_token_tree(res.content_token_counts.per_file_content_tokens):
                typer.echo(line)

    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        logger.exception("Failed to pack repository")
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


def _split_cmd(value: str) -> list[str]:
    import shlex

    parts = shlex.split(value)
    if not parts:
        raise ValueError("Empty command")
    return parts


if __name__ == "__main__":
    app()
