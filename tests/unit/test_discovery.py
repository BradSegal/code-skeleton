from pathlib import Path

import pytest

from anatomize.core.discovery import ROOT_PACKAGE, discover
from anatomize.core.policy import SymlinkPolicy

pytestmark = pytest.mark.integration


def test_discover_container_layout() -> None:
    root = Path(__file__).parent.parent / "fixtures" / "project_src" / "src"
    result = discover([root], exclude=["excluded"], symlinks=SymlinkPolicy.FORBID)

    assert "pkg.mod" in result.modules
    assert "ns_pkg.sub.mod" in result.modules
    assert "top_level" in result.modules
    assert "excluded.skip" not in result.modules

    assert ROOT_PACKAGE in result.packages
    assert "pkg" in result.packages
    assert "ns_pkg" in result.packages
    assert "ns_pkg.sub" in result.packages

    root_pkg = result.packages[ROOT_PACKAGE]
    assert "pkg" in root_pkg.subpackages
    assert "ns_pkg" in root_pkg.subpackages
    assert "top_level" in root_pkg.modules


def test_discover_collisions_rejected(tmp_path: Path) -> None:
    s1 = tmp_path / "s1"
    s2 = tmp_path / "s2"
    s1.mkdir()
    s2.mkdir()
    (s1 / "a.py").write_text("x = 1\n", encoding="utf-8")
    (s2 / "a.py").write_text("y = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="collision"):
        discover([s1, s2], exclude=[], symlinks=SymlinkPolicy.FORBID)


def test_symlink_policy(tmp_path: Path) -> None:
    root = tmp_path / "src"
    real_dir = root / "real"
    real_dir.mkdir(parents=True)
    (real_dir / "a.py").write_text("x = 1\n", encoding="utf-8")

    link_file = root / "link_file.py"
    link_dir = root / "link_dir"

    try:
        link_file.symlink_to(real_dir / "a.py")
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    forbid = discover([root], exclude=[], symlinks=SymlinkPolicy.FORBID)
    assert "real.a" in forbid.modules
    assert "link_file" not in forbid.modules
    assert "link_dir.a" not in forbid.modules

    files = discover([root], exclude=[], symlinks=SymlinkPolicy.FILES)
    assert "real.a" in files.modules
    assert "link_file" in files.modules
    assert "link_dir.a" not in files.modules

    dirs = discover([root], exclude=[], symlinks=SymlinkPolicy.DIRS)
    assert "real.a" in dirs.modules
    assert "link_file" not in dirs.modules
    assert "link_dir.a" in dirs.modules

    all_ = discover([root], exclude=[], symlinks=SymlinkPolicy.ALL)
    assert "real.a" in all_.modules
    assert "link_file" in all_.modules
    assert "link_dir.a" in all_.modules
