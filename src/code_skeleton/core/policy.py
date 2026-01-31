"""Runtime policies for discovery and extraction."""

from __future__ import annotations

from enum import Enum


class SymlinkPolicy(str, Enum):
    """How to treat symlinks during discovery."""

    FORBID = "forbid"
    FILES = "files"
    DIRS = "dirs"
    ALL = "all"

