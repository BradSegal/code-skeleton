import pytest

from anatomize.core.exclude import Excluder

pytestmark = pytest.mark.unit


def test_exclude_directory_rule_excludes_contents() -> None:
    ex = Excluder(["build/"])
    assert ex.is_excluded("build", is_dir=True) is True
    assert ex.is_excluded("build/x.py", is_dir=False) is True
    assert ex.is_excluded("src/build/x.py", is_dir=False) is True


def test_exclude_negation_rule() -> None:
    ex = Excluder(["*.py", "!keep.py"])
    assert ex.is_excluded("a.py", is_dir=False) is True
    assert ex.is_excluded("keep.py", is_dir=False) is False


def test_exclude_anchored_rule() -> None:
    ex = Excluder(["/dist/"])
    assert ex.is_excluded("dist", is_dir=True) is True
    assert ex.is_excluded("dist/a.py", is_dir=False) is True
    assert ex.is_excluded("src/dist/a.py", is_dir=False) is False


def test_exclude_escaped_comment_and_negation_prefixes() -> None:
    ex = Excluder([r"\#keep", r"\!literal", "# comment", ""])
    assert ex.is_excluded("#keep", is_dir=False) is True
    assert ex.is_excluded("!literal", is_dir=False) is True


def test_exclude_trailing_spaces_ignored_unless_escaped() -> None:
    ex = Excluder(["foo   "])
    assert ex.is_excluded("foo", is_dir=False) is True

    ex2 = Excluder([r"bar\ "])
    assert ex2.is_excluded("bar", is_dir=False) is False
    assert ex2.is_excluded("bar ", is_dir=False) is True


def test_exclude_rejects_unsupported_backslash_escapes() -> None:
    with pytest.raises(ValueError, match="Unsupported backslash escape"):
        Excluder([r"src\\**"])
