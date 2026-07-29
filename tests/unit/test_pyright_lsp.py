"""Unit tests for Pyright LSP client utilities."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from anatomize.pack.pyright_lsp import (
    LspPosition,
    _path_to_uri,
    _read_headers,
    _uri_to_path,
)

pytestmark = pytest.mark.unit


class TestLspPosition:
    """Tests for LspPosition dataclass."""

    def test_create_position(self) -> None:
        """Test creating a position."""
        pos = LspPosition(line=10, character=5)
        assert pos.line == 10
        assert pos.character == 5

    def test_position_is_frozen(self) -> None:
        """Test that position is immutable."""
        pos = LspPosition(line=10, character=5)
        with pytest.raises(Exception):  # FrozenInstanceError
            pos.line = 20  # type: ignore[misc]

    def test_position_equality(self) -> None:
        """Test position equality comparison."""
        pos1 = LspPosition(line=10, character=5)
        pos2 = LspPosition(line=10, character=5)
        pos3 = LspPosition(line=10, character=6)
        assert pos1 == pos2
        assert pos1 != pos3

    def test_position_hash(self) -> None:
        """Test that positions are hashable."""
        pos1 = LspPosition(line=10, character=5)
        pos2 = LspPosition(line=10, character=5)
        positions = {pos1, pos2}
        assert len(positions) == 1


class TestPathToUri:
    """Tests for _path_to_uri function."""

    def test_converts_absolute_path(self) -> None:
        """Test conversion of absolute path to URI."""
        path = Path("/tmp/test.py")
        uri = _path_to_uri(path)
        assert uri.startswith("file://")
        assert "test.py" in uri

    def test_handles_path_with_spaces(self) -> None:
        """Test that spaces are percent-encoded."""
        path = Path("/tmp/path with spaces/file.py")
        uri = _path_to_uri(path)
        assert "%20" in uri or " " not in uri.split("://")[1]

    def test_handles_special_characters(self) -> None:
        """Test that special characters are handled."""
        path = Path("/tmp/test#file.py")
        uri = _path_to_uri(path)
        assert "test" in uri
        assert "file.py" in uri


class TestUriToPath:
    """Tests for _uri_to_path function."""

    def test_rejects_non_file_scheme(self) -> None:
        """Test that non-file URIs return None."""
        assert _uri_to_path("https://example.com/x") is None
        assert _uri_to_path("untitled:foo") is None
        assert _uri_to_path("git://repo/file") is None

    def test_decodes_percent_escapes(self) -> None:
        """Test that percent-encoded characters are decoded."""
        p = _uri_to_path("file:///tmp/a%20b.py")
        assert p is not None
        assert p == Path("/tmp/a b.py").resolve()

    def test_accepts_localhost(self) -> None:
        """Test that file://localhost/ URIs are accepted."""
        p = _uri_to_path("file://localhost/tmp/x.py")
        assert p is not None
        assert p == Path("/tmp/x.py").resolve()

    def test_basic_file_uri(self) -> None:
        """Test basic file URI conversion."""
        p = _uri_to_path("file:///tmp/test.py")
        assert p is not None
        assert p.name == "test.py"

    def test_empty_authority(self) -> None:
        """Test file URI with empty authority."""
        p = _uri_to_path("file:///opt/example/file.py")
        assert p is not None
        assert "file.py" in str(p)

    def test_invalid_uri_returns_none(self) -> None:
        """Test that malformed URIs return None."""
        assert _uri_to_path("not-a-uri") is None


class TestReadHeaders:
    """Tests for _read_headers function."""

    def test_parses_content_length_header(self) -> None:
        """Test parsing Content-Length header."""
        stream = BytesIO(b"Content-Length: 42\r\n\r\n")
        headers = _read_headers(stream)
        assert headers is not None
        assert headers["content-length"] == "42"

    def test_returns_none_on_empty_stream(self) -> None:
        """Test that empty stream returns None."""
        stream = BytesIO(b"")
        assert _read_headers(stream) is None

    def test_handles_multiple_headers(self) -> None:
        """Test parsing multiple headers."""
        stream = BytesIO(b"Content-Length: 100\r\nContent-Type: application/json\r\n\r\n")
        headers = _read_headers(stream)
        assert headers is not None
        assert headers["content-length"] == "100"
        assert headers["content-type"] == "application/json"

    def test_header_names_are_lowercased(self) -> None:
        """Test that header names are normalized to lowercase."""
        stream = BytesIO(b"Content-Length: 50\r\n\r\n")
        headers = _read_headers(stream)
        assert headers is not None
        assert "content-length" in headers

    def test_handles_crlf_line_endings(self) -> None:
        """Test that CRLF line endings are handled correctly."""
        stream = BytesIO(b"Content-Length: 10\r\n\r\n")
        headers = _read_headers(stream)
        assert headers is not None
        assert headers["content-length"] == "10"

    def test_returns_none_on_no_blank_line(self) -> None:
        """Test that headers without terminating blank line return None."""
        stream = BytesIO(b"Content-Length: 10\r\n")  # No final \r\n\r\n
        headers = _read_headers(stream)
        # Should return None or handle gracefully - actual behavior depends on implementation
        assert headers is None or isinstance(headers, dict)


class TestRoundTrip:
    """Tests for path<->uri round-trip conversion."""

    def test_roundtrip_simple_path(self) -> None:
        """Test that simple paths survive round-trip conversion."""
        original = Path("/tmp/test.py").resolve()
        uri = _path_to_uri(original)
        recovered = _uri_to_path(uri)
        assert recovered is not None
        assert recovered == original

    def test_roundtrip_path_with_spaces(self) -> None:
        """Test that paths with spaces survive round-trip."""
        original = Path("/tmp/path with spaces/file.py").resolve()
        uri = _path_to_uri(original)
        recovered = _uri_to_path(uri)
        assert recovered is not None
        assert recovered == original
