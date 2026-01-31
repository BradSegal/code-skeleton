from pathlib import Path

import pytest
from pydantic import ValidationError

from anatomize.config import SkeletonConfig
from anatomize.core.types import ResolutionLevel
from anatomize.formats import OutputFormat
from anatomize.pack.formats import PackFormat

pytestmark = pytest.mark.unit


def test_config_strict_unknown_keys(tmp_path: Path) -> None:
    cfg = tmp_path / ".anatomize.yaml"
    cfg.write_text(
        "sources: [src]\noutput: .skeleton\nunknown_key: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        SkeletonConfig.from_file(cfg)


def test_config_find_in_parent_dir(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    cfg = parent / ".anatomize.yaml"
    cfg.write_text(
        (
            "sources: [src]\n"
            "output: out\n"
            "level: signatures\n"
            "formats: [json]\n"
            "exclude: [excluded]\n"
            "symlinks: forbid\n"
            "workers: 0\n"
        ),
        encoding="utf-8",
    )
    found = SkeletonConfig.find_config(start_dir=child)
    assert found is not None
    assert found.sources == ["src"]
    assert found.output == "out"
    assert found.level == ResolutionLevel.SIGNATURES
    assert found.formats == [OutputFormat.JSON]
    assert found.exclude == ["excluded"]
    assert found.symlinks.value == "forbid"
    assert found.workers == 0


def test_config_pack_section_parses_and_is_strict(tmp_path: Path) -> None:
    cfg = tmp_path / ".anatomize.yaml"
    cfg.write_text(
        (
            "sources: [src]\n"
            "pack:\n"
            "  format: json\n"
            "  output: out.json\n"
            "  ignore: ['*.pyc']\n"
        ),
        encoding="utf-8",
    )
    loaded = SkeletonConfig.from_file(cfg)
    assert loaded.pack is not None
    assert loaded.pack.format is PackFormat.JSON
    assert loaded.pack.output == "out.json"
    assert loaded.pack.ignore == ["*.pyc"]

    cfg2 = tmp_path / ".anatomize2.yaml"
    cfg2.write_text("pack:\n  unknown: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        SkeletonConfig.from_file(cfg2)


def test_config_pack_output_extension_must_match_format(tmp_path: Path) -> None:
    cfg = tmp_path / ".anatomize.yaml"
    cfg.write_text(
        (
            "pack:\n"
            "  format: markdown\n"
            "  output: out.jsonl\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        SkeletonConfig.from_file(cfg)
