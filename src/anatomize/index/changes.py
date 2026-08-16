"""Explicit-base file, definition, and prior-consumer impact."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from anatomize.index.git import git_bytes, git_text, nul_paths
from anatomize.index.models import (
    ChangedReport,
    FileChange,
    FileChangeStatus,
    ImpactNode,
    ImpactRelationship,
    ImpactRole,
    RepositoryIndex,
    SymbolChange,
    SymbolChangeStatus,
    SymbolRecord,
)
from anatomize.index.repository import (
    _add_related_files,
    _merge_node,
    _relationships,
    _sorted_nodes,
    build_impact_report,
    build_repository_index,
    repository_path_included,
)


def build_changed_report(
    root: Path,
    index: RepositoryIndex,
    *,
    base: str,
    max_depth: int = 1,
    max_related_per_role: int = 20,
    semantic_references: bool = False,
    pyright_langserver_cmd: list[str] | None = None,
) -> ChangedReport:
    """Build current and prior impact for the working tree relative to a Git base."""
    root = root.resolve()
    base_commit = git_text(root, ["rev-parse", "--verify", f"{base}^{{commit}}"]).strip()
    changes = _working_changes(root, base, base_commit)
    baseline = _baseline_index(root, index, base_commit)
    nodes, omissions, unresolved = _change_impact(
        root,
        index,
        baseline,
        changes,
        max_depth=max_depth,
        max_related_per_role=max_related_per_role,
        semantic_references=semantic_references,
        pyright_langserver_cmd=pyright_langserver_cmd,
    )
    changed_files = sorted(path for item in changes if (path := item.new_path or item.old_path) is not None)
    return ChangedReport(
        root_name=index.root_name,
        source_state=index.source_state,
        base=base,
        base_commit=base_commit,
        changed_files=changed_files,
        changes=changes,
        changed_symbols=_changed_symbols(baseline, index, changes),
        nodes=nodes,
        related_omissions=omissions,
        unresolved=unresolved,
        limitations=index.limitations,
    )


def _working_changes(root: Path, base: str, base_commit: str) -> list[FileChange]:
    changes = _git_changes(root, base)
    known = {item.new_path for item in changes if item.new_path}
    for path in nul_paths(git_text(root, ["ls-files", "--others", "--exclude-standard", "-z"])):
        if path not in known and repository_path_included(Path(path)):
            changes.append(FileChange(status=FileChangeStatus.ADDED, new_path=path))
    return sorted(
        _coalesce_exact_renames(root, base_commit, changes),
        key=lambda item: (item.new_path or item.old_path or "", item.status.value),
    )


def _change_impact(
    root: Path,
    current: RepositoryIndex,
    baseline: RepositoryIndex,
    changes: list[FileChange],
    *,
    max_depth: int,
    max_related_per_role: int,
    semantic_references: bool,
    pyright_langserver_cmd: list[str] | None,
) -> tuple[list[ImpactNode], dict[str, int], list[str]]:
    nodes: dict[str, ImpactNode] = {}
    unresolved: list[str] = []
    current_modules = {module.path for module in current.modules}
    baseline_modules = {module.path for module in baseline.modules}
    terms: set[str] = set()
    for change in changes:
        target = change.new_path or change.old_path
        if target is None:
            continue
        terms.add(Path(target).stem)
        if change.new_path and target in current_modules:
            report = build_impact_report(
                root,
                current,
                target,
                max_depth=max_depth,
                include_related=False,
                semantic_references=semantic_references,
                pyright_langserver_cmd=pyright_langserver_cmd,
            )
            unresolved.extend(report.unresolved)
            for node in report.nodes:
                _merge_node(nodes, node)
        else:
            _merge_node(nodes, _changed_focus(target, change.status))
        if change.old_path in baseline_modules:
            previous = build_impact_report(
                root,
                baseline,
                change.old_path,
                max_depth=max_depth,
                include_related=False,
            )
            for node in previous.nodes:
                _merge_node(nodes, _baseline_relationship(node))
        terms.update(
            symbol.name
            for symbol in (*current.symbols, *baseline.symbols)
            if symbol.path in {change.old_path, change.new_path}
        )
    nodes, omissions = _add_related_files(root, nodes, terms, max_per_role=max_related_per_role)
    return _sorted_nodes(nodes.values()), omissions, sorted(set(unresolved))


def _changed_focus(path: str, status: FileChangeStatus) -> ImpactNode:
    relationship = ImpactRelationship(
        role=ImpactRole.FOCUS,
        distance=0,
        reason=f"{status.value} from explicit Git base",
        source="git_change",
    )
    return ImpactNode(
        path=path,
        role=relationship.role,
        distance=relationship.distance,
        reason=relationship.reason,
        relationships=[relationship],
    )


def _baseline_relationship(node: ImpactNode) -> ImpactNode:
    relationships = [
        ImpactRelationship(
            role=item.role,
            distance=item.distance,
            reason=f"baseline: {item.reason}",
            source=f"baseline_{item.source}",
        )
        for item in _relationships(node)
    ]
    return ImpactNode(
        path=node.path,
        role=node.role,
        distance=node.distance,
        reason=f"baseline: {node.reason}",
        relationships=relationships,
    )


def _git_changes(root: Path, base: str) -> list[FileChange]:
    fields = nul_paths(git_text(root, ["diff", "--name-status", "-z", "-M", base, "--"]))
    changes: list[FileChange] = []
    offset = 0
    while offset < len(fields):
        code = fields[offset]
        offset += 1
        status = code[0]
        if status == "R":
            old_path, new_path = fields[offset : offset + 2]
            offset += 2
            changes.append(
                FileChange(
                    status=FileChangeStatus.RENAMED,
                    old_path=old_path,
                    new_path=new_path,
                    similarity=int(code[1:]) if code[1:].isdigit() else None,
                )
            )
            continue
        path = fields[offset]
        offset += 1
        if status == "A":
            changes.append(FileChange(status=FileChangeStatus.ADDED, new_path=path))
        elif status == "D":
            changes.append(FileChange(status=FileChangeStatus.DELETED, old_path=path))
        else:
            changes.append(FileChange(status=FileChangeStatus.MODIFIED, old_path=path, new_path=path))
    return changes


def _coalesce_exact_renames(root: Path, revision: str, changes: list[FileChange]) -> list[FileChange]:
    deleted = [item for item in changes if item.status is FileChangeStatus.DELETED and item.old_path]
    added = [item for item in changes if item.status is FileChangeStatus.ADDED and item.new_path]
    replacements: list[FileChange] = []
    consumed_old: set[str] = set()
    consumed_new: set[str] = set()
    for old in deleted:
        assert old.old_path is not None
        old_content = git_bytes(root, ["show", f"{revision}:{old.old_path}"])
        for new in added:
            assert new.new_path is not None
            if new.new_path in consumed_new or not (root / new.new_path).is_file():
                continue
            if old_content == (root / new.new_path).read_bytes():
                consumed_old.add(old.old_path)
                consumed_new.add(new.new_path)
                replacements.append(
                    FileChange(
                        status=FileChangeStatus.RENAMED,
                        old_path=old.old_path,
                        new_path=new.new_path,
                        similarity=100,
                    )
                )
                break
    retained = [item for item in changes if item.old_path not in consumed_old and item.new_path not in consumed_new]
    return retained + replacements


def _baseline_index(root: Path, current: RepositoryIndex, revision: str) -> RepositoryIndex:
    entries = nul_paths(git_text(root, ["ls-tree", "-r", "-z", revision]))
    tracked = [
        entry.split("\t", 1)[1] for entry in entries if "\t" in entry and entry.split("\t", 1)[0].split()[1] == "blob"
    ]
    with tempfile.TemporaryDirectory(prefix="anatomize-baseline-") as temporary:
        snapshot = Path(temporary)
        for relative in tracked:
            path = Path(relative)
            if path.suffix not in {"", ".py"}:
                continue
            destination = snapshot / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(git_bytes(root, ["show", f"{revision}:{relative}"]))
        roots = [snapshot if item == "." else snapshot / item for item in current.python_roots]
        for python_root in roots:
            python_root.mkdir(parents=True, exist_ok=True)
        return build_repository_index(snapshot, python_roots=roots)


def _changed_symbols(
    baseline: RepositoryIndex,
    current: RepositoryIndex,
    changes: list[FileChange],
) -> list[SymbolChange]:
    old_paths = {item.old_path for item in changes if item.old_path}
    new_paths = {item.new_path for item in changes if item.new_path}
    old = [item for item in baseline.symbols if item.path in old_paths]
    new = [item for item in current.symbols if item.path in new_paths]
    remaining_old = set(range(len(old)))
    remaining_new = set(range(len(new)))
    result: list[SymbolChange] = []

    _discard_unchanged(old, new, remaining_old, remaining_new)

    def pair(status: SymbolChangeStatus, matcher: Callable[[SymbolRecord, SymbolRecord], bool]) -> None:
        for old_index in sorted(tuple(remaining_old)):
            for new_index in sorted(tuple(remaining_new)):
                if matcher(old[old_index], new[new_index]):
                    result.append(SymbolChange(status=status, old=old[old_index], new=new[new_index]))
                    remaining_old.remove(old_index)
                    remaining_new.remove(new_index)
                    break

    pair(
        SymbolChangeStatus.MODIFIED,
        lambda left, right: left.qualified_name == right.qualified_name and left.digest != right.digest,
    )
    pair(
        SymbolChangeStatus.MOVED,
        lambda left, right: left.path != right.path
        and left.name == right.name
        and left.kind == right.kind
        and left.digest == right.digest,
    )
    pair(SymbolChangeStatus.RENAMED, lambda left, right: left.kind == right.kind and left.digest == right.digest)
    result.extend(SymbolChange(status=SymbolChangeStatus.DELETED, old=old[index]) for index in remaining_old)
    result.extend(SymbolChange(status=SymbolChangeStatus.ADDED, new=new[index]) for index in remaining_new)
    return sorted(result, key=_symbol_change_key)


def _discard_unchanged(
    old: list[SymbolRecord],
    new: list[SymbolRecord],
    remaining_old: set[int],
    remaining_new: set[int],
) -> None:
    for old_index in sorted(tuple(remaining_old)):
        match = next(
            (
                new_index
                for new_index in sorted(remaining_new)
                if old[old_index].qualified_name == new[new_index].qualified_name
                and old[old_index].digest == new[new_index].digest
            ),
            None,
        )
        if match is not None:
            remaining_old.remove(old_index)
            remaining_new.remove(match)


def _symbol_change_key(item: SymbolChange) -> tuple[str, int, str]:
    symbol = item.new or item.old
    return (symbol.path, symbol.line, item.status.value) if symbol else ("", 0, item.status.value)
