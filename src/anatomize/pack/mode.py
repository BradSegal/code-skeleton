"""Pack mode selection."""

from __future__ import annotations

from enum import Enum


class PackMode(str, Enum):
    BUNDLE = "bundle"
    HYBRID = "hybrid"

