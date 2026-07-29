from __future__ import annotations

from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from anatomize.pack.discovery import _is_binary_file

pytestmark = pytest.mark.unit


def test_binary_sniff_reads_only_prefix(tmp_path: Path) -> None:
    p = tmp_path / "big.txt"
    p.write_text("a" * 50_000, encoding="utf-8")

    mocked_open = mock_open(read_data=b"a" * 50_000)
    with patch.object(Path, "open", mocked_open):
        _is_binary_file(p, sniff_bytes=8)

    mocked_open().read.assert_called_once_with(8)
