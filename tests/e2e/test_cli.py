from pathlib import Path

import pytest
from typer.testing import CliRunner

from anatomize.cli import app

pytestmark = pytest.mark.e2e


def test_cli_generate_validate_and_fix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    mod = pkg / "mod.py"
    mod.write_text("def f(x: int) -> int:\n    return x + 1\n", encoding="utf-8")

    (tmp_path / ".anatomize.yaml").write_text(
        "sources: [src]\noutput: out\nlevel: modules\nformats: [json]\nexclude: []\nsymlinks: forbid\nworkers: 0\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["generate"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "hierarchy.json").exists()

    ok = runner.invoke(app, ["validate", "out"])
    assert ok.exit_code == 0, ok.output

    # Mutate source -> validate must fail
    mod.write_text("def f2(x: int) -> int:\n    return x + 2\n", encoding="utf-8")
    bad = runner.invoke(app, ["validate", "out"])
    assert bad.exit_code != 0

    fixed = runner.invoke(app, ["validate", "out", "--fix"])
    assert fixed.exit_code == 0, fixed.output

    ok2 = runner.invoke(app, ["validate", "out"])
    assert ok2.exit_code == 0, ok2.output


def test_cli_generate_deterministic_across_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import shutil

    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("def f(x: int) -> int:\n    return x + 1\n", encoding="utf-8")

    out = tmp_path / "out"
    runner = CliRunner()

    def snapshot() -> dict[str, bytes]:
        return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}

    monkeypatch.chdir(tmp_path)
    res1 = runner.invoke(app, ["generate", str(src), "--output", str(out), "--format", "json"])
    assert res1.exit_code == 0, res1.output
    snap1 = snapshot()

    # Delete output and rerun from a different working directory with the same absolute args.
    shutil.rmtree(out)

    subdir = tmp_path / "subdir"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    res2 = runner.invoke(app, ["generate", str(src), "--output", str(out), "--format", "json"])
    assert res2.exit_code == 0, res2.output
    snap2 = snapshot()

    assert snap1 == snap2


def test_cli_invalid_format_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".anatomize.yaml").write_text("sources: [src]\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    res = runner.invoke(app, ["generate", "--format", "nope"])
    assert res.exit_code != 0


def test_cli_requires_sources_when_no_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    res = runner.invoke(app, ["estimate"])
    assert res.exit_code != 0
