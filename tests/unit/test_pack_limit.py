from __future__ import annotations

import pytest

from anatomize.pack.limit import LimitKind, parse_output_limit

pytestmark = pytest.mark.unit


def test_parse_output_limit_prefers_longest_suffix() -> None:
    lim = parse_output_limit("1mb")
    assert lim.kind is LimitKind.BYTES
    assert lim.value == 1024 * 1024
