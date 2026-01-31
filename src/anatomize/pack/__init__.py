"""Token-efficient repository pack/bundle output.

The `pack` feature is inspired by tools like repomix: it produces a single,
deterministic artifact containing the repository structure plus either full
file contents or a compressed (tree-sitter-based) structural representation.
"""

from __future__ import annotations

from anatomize.pack.runner import PackResult, pack

__all__ = ["PackResult", "pack"]
