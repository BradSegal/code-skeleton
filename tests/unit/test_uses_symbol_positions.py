from __future__ import annotations

from pathlib import Path

import pytest

from anatomize.pack.uses import python_public_symbol_positions

pytestmark = pytest.mark.unit


def test_positions_point_at_symbol_name_for_def_and_class(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(
        (
            "import typing\n\n"
            "def f(x: int) -> int:\n"
            "    return x + 1\n\n"
            "class C:\n"
            "    pass\n"
        ),
        encoding="utf-8",
    )

    pos = python_public_symbol_positions(path, include_private=False)
    # Expect two positions: f and C.
    assert len(pos) == 2
    # def f: line 2, character points at 'f' (0-based line, 0-based char)
    assert (pos[0].line, pos[0].character) == (2, 4)
    # class C: line 5, character points at 'C'
    assert (pos[1].line, pos[1].character) == (5, 6)


def test_positions_include_private_when_requested(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(
        (
            "def _private() -> None:\n"
            "    pass\n"
            "def public() -> None:\n"
            "    pass\n"
        ),
        encoding="utf-8",
    )

    pos_public = python_public_symbol_positions(path, include_private=False)
    assert len(pos_public) == 1
    assert (pos_public[0].line, pos_public[0].character) == (2, 4)

    pos_all = python_public_symbol_positions(path, include_private=True)
    assert len(pos_all) == 2
    assert {(p.line, p.character) for p in pos_all} == {(0, 4), (2, 4)}
