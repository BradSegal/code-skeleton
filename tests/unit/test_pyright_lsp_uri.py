from pathlib import Path

import pytest

from anatomize.pack.pyright_lsp import _uri_to_path

pytestmark = pytest.mark.unit


def test_uri_to_path_rejects_non_file() -> None:
    assert _uri_to_path("https://example.com/x") is None
    assert _uri_to_path("untitled:foo") is None


def test_uri_to_path_decodes_percent_escapes() -> None:
    p = _uri_to_path("file:///tmp/a%20b.py")
    assert p is not None
    assert p == Path("/tmp/a b.py").resolve()


def test_uri_to_path_accepts_localhost() -> None:
    p = _uri_to_path("file://localhost/tmp/x.py")
    assert p is not None
    assert p == Path("/tmp/x.py").resolve()
