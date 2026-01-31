"""Selection modes for `pack`."""

from __future__ import annotations

from enum import Enum


class SliceBackend(str, Enum):
    IMPORTS = "imports"
    PYRIGHT = "pyright"

