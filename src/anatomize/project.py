"""Project-wide validation for configured Anatomize artifacts."""

from __future__ import annotations

import tempfile
from enum import Enum
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from anatomize.config import AnatomizeConfig, PackConfig, SkeletonSourceConfig
from anatomize.index import load_repository_index
from anatomize.pack.boundaries import output_ignore_patterns
from anatomize.pack.limit import parse_output_limit
from anatomize.pack.runner import pack
from anatomize.validation import validate_skeleton_dir


class CheckStatus(str, Enum):
    """Independent project-check outcomes."""

    VERIFIED = "verified"
    INVALID = "invalid"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class CheckRecord(BaseModel):
    """One independently interpretable project check."""

    check_id: str
    status: CheckStatus
    message: str

    model_config = {"frozen": True}


class ProjectCheckReport(BaseModel):
    """Result of checking configured Anatomize artifacts."""

    schema_version: str = "1.0.0"
    root_name: str
    checks: list[CheckRecord]

    model_config = {"frozen": True}

    @property
    def successful(self) -> bool:
        """Return whether no check is invalid or unresolved."""
        return all(check.status in (CheckStatus.VERIFIED, CheckStatus.NOT_APPLICABLE) for check in self.checks)


def check_project(
    root: Path,
    *,
    config_path: Path | None = None,
) -> ProjectCheckReport:
    """Validate every configured skeleton, pack, and stored index."""
    root = root.resolve()
    selected_config = config_path or AnatomizeConfig.find_config_path(root)
    if selected_config is None:
        return ProjectCheckReport(
            root_name=root.name,
            checks=[
                CheckRecord(
                    check_id="configuration",
                    status=CheckStatus.UNRESOLVED,
                    message="No .anatomize.yaml was found.",
                )
            ],
        )
    config = AnatomizeConfig.from_file(selected_config)
    checks: list[CheckRecord] = [
        CheckRecord(
            check_id="configuration",
            status=CheckStatus.VERIFIED,
            message=f"Loaded {selected_config.name}.",
        )
    ]
    checks.extend(_check_skeletons(root, config))
    checks.append(_check_pack(root, config.pack))
    checks.append(_check_index(root, config))
    return ProjectCheckReport(root_name=root.name, checks=checks)


def _check_skeletons(root: Path, config: AnatomizeConfig) -> list[CheckRecord]:
    if not config.sources:
        return [
            CheckRecord(
                check_id="skeletons",
                status=CheckStatus.NOT_APPLICABLE,
                message="No skeleton sources are configured.",
            )
        ]
    out_root = _resolve_under(root, config.output)
    checks: list[CheckRecord] = []
    for index, source in enumerate(config.sources):
        name = _source_output_name(source, index=index)
        source_path = _resolve_under(root, source.path)
        skeleton_path = out_root / name
        try:
            if not skeleton_path.is_dir():
                raise ValueError(f"Missing skeleton output: {skeleton_path}")
            validate_skeleton_dir(
                skeleton_dir=skeleton_path,
                sources=[source_path],
                exclude=source.exclude if source.exclude is not None else config.exclude,
                symlinks=source.symlinks if source.symlinks is not None else config.symlinks,
                workers=source.workers if source.workers is not None else config.workers,
                fix=False,
                metadata_base_dir=root,
            )
        except ValueError as exc:
            checks.append(
                CheckRecord(
                    check_id=f"skeleton:{name}",
                    status=CheckStatus.INVALID,
                    message=str(exc),
                )
            )
        else:
            checks.append(
                CheckRecord(
                    check_id=f"skeleton:{name}",
                    status=CheckStatus.VERIFIED,
                    message="Stored skeleton matches current source.",
                )
            )
    return checks


def _check_pack(root: Path, config: PackConfig | None) -> CheckRecord:
    if config is None or config.output is None:
        return CheckRecord(
            check_id="pack",
            status=CheckStatus.NOT_APPLICABLE,
            message="No stored pack output is configured.",
        )
    actual = _resolve_under(root, config.output)
    if not actual.exists() and config.split_output is None:
        return CheckRecord(
            check_id="pack",
            status=CheckStatus.INVALID,
            message=f"Missing configured pack output: {actual}",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="anatomize-check-") as temporary:
            candidate = Path(temporary) / actual.name
            ignore = [
                *config.ignore,
                *output_ignore_patterns(
                    root,
                    output=actual,
                    selection_report=None,
                ),
            ]
            result = pack(
                root=root,
                output=candidate,
                fmt=config.format,
                mode=config.mode,
                include=config.include,
                ignore=ignore,
                ignore_files=[_resolve_under(root, item) for item in config.ignore_files],
                respect_standard_ignores=config.respect_standard_ignores,
                symlinks=config.symlinks,
                max_file_bytes=config.max_file_bytes,
                workers=config.workers,
                token_encoding=config.token_encoding,
                compress=config.compress,
                content_encoding=config.content_encoding,
                prefix_style=config.prefix,
                line_numbers=config.line_numbers,
                include_structure=not config.no_structure,
                include_files=not config.no_files,
                max_output=(parse_output_limit(config.max_output) if config.max_output else None),
                split_output=(parse_output_limit(config.split_output) if config.split_output else None),
                representation_content=config.content,
                representation_summary=config.summary,
                representation_meta=config.meta,
                fit_to_max_output=config.fit_to_max_output,
                summary_config=config.summary_config,
                entries=[],
                deps=False,
                python_roots=[_resolve_under(root, item) for item in config.python_roots],
            )
            for artifact in result.artifacts:
                stored = actual.parent / artifact.path.name
                if not stored.is_file():
                    raise ValueError(f"Missing configured pack artifact: {stored}")
                if stored.read_bytes() != artifact.path.read_bytes():
                    raise ValueError(f"Configured pack artifact is stale: {stored.relative_to(root)}")
    except ValueError as exc:
        return CheckRecord(
            check_id="pack",
            status=CheckStatus.INVALID,
            message=str(exc),
        )
    return CheckRecord(
        check_id="pack",
        status=CheckStatus.VERIFIED,
        message="Stored pack matches a clean regenerated candidate.",
    )


def _check_index(root: Path, config: AnatomizeConfig) -> CheckRecord:
    index_path = _resolve_under(root, config.output) / "index.json"
    if not index_path.is_file():
        return CheckRecord(
            check_id="index",
            status=CheckStatus.NOT_APPLICABLE,
            message="No stored repository index was found.",
        )
    try:
        load_repository_index(root, index_path, require_current=True)
    except ValueError as exc:
        return CheckRecord(
            check_id="index",
            status=CheckStatus.INVALID,
            message=str(exc),
        )
    return CheckRecord(
        check_id="index",
        status=CheckStatus.VERIFIED,
        message="Stored repository index matches current Python source.",
    )


def _source_output_name(source: SkeletonSourceConfig, *, index: int) -> str:
    if source.output is not None:
        return source.output
    leaf = PurePosixPath(source.path.replace("\\", "/").strip("/")).name
    return leaf or f"source-{index}"


def _resolve_under(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()
