"""Deterministic tree renderers for pack."""

from __future__ import annotations

from typing import TypeAlias, Union, cast

StructureTree: TypeAlias = dict[str, Union[None, "StructureTree"]]
TokenTree: TypeAlias = dict[str, Union[int, "TokenTree"]]


def render_structure_tree(paths: list[tuple[str, bool]]) -> list[str]:
    """Render a directory structure tree from (rel_posix, is_dir) items."""
    tree: StructureTree = {}
    for rel_posix, is_dir in sorted(paths, key=lambda x: x[0]):
        parts = [x for x in rel_posix.split("/") if x and x != "."]
        if not parts:
            continue
        node: StructureTree = tree
        for part in parts[:-1]:
            child = node.get(part)
            if isinstance(child, dict):
                node = child
                continue
            new_child: StructureTree = {}
            node[part] = new_child
            node = new_child

        leaf = parts[-1]
        existing = node.get(leaf)
        if is_dir:
            if existing is None:
                node[leaf] = {}
        else:
            if existing is None:
                node[leaf] = None

    lines: list[str] = []

    def walk(node: StructureTree, prefix: str) -> None:
        for name in sorted(node.keys()):
            child = node[name]
            if isinstance(child, dict):
                lines.append(f"{prefix}{name}/")
                walk(child, prefix + "  ")
            else:
                lines.append(f"{prefix}{name}")

    walk(tree, "")
    return lines


def render_token_tree(tokens_by_path: dict[str, int]) -> list[str]:
    tree: TokenTree = {}
    for path in sorted(tokens_by_path.keys()):
        parts = [p for p in path.split("/") if p and p != "."]
        node: TokenTree = tree
        for part in parts[:-1]:
            child = node.get(part)
            if isinstance(child, dict):
                node = child
                continue
            new_child: TokenTree = {}
            node[part] = new_child
            node = new_child
        leaf = parts[-1] if parts else path
        node[leaf] = tokens_by_path[path]

    lines: list[str] = []

    def walk(node: TokenTree, prefix: str) -> None:
        dirs = sorted([k for k, v in node.items() if isinstance(v, dict)])
        files = sorted([k for k, v in node.items() if isinstance(v, int)])
        for name in dirs:
            lines.append(f"{prefix}{name}/")
            child = node[name]
            if isinstance(child, dict):
                walk(child, prefix + "  ")
        for name in files:
            lines.append(f"{prefix}{name} ({cast(int, node[name]):,})")

    walk(tree, "")
    return lines
