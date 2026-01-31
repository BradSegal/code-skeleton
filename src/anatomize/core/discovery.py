"""Source tree discovery for Python modules and package hierarchy.

This module defines the strict, deterministic rules for mapping a container
directory (e.g. `./src`) to import-style module names and a package tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from anatomize.core.exclude import Excluder
from anatomize.core.policy import SymlinkPolicy
from anatomize.core.types import PackageInfo

ROOT_PACKAGE = "__root__"


@dataclass(frozen=True)
class DiscoveredModule:
    """A discovered Python module."""

    name: str
    absolute_path: Path
    relative_path: str
    source: int


@dataclass(frozen=True)
class DiscoveryResult:
    """Discovery output for one or more source roots."""

    sources: list[Path]
    packages: dict[str, PackageInfo]
    modules: dict[str, DiscoveredModule]


def discover(
    sources: list[Path],
    *,
    exclude: list[str],
    symlinks: SymlinkPolicy,
) -> DiscoveryResult:
    """Discover Python modules under one or more container directories.

    Parameters
    ----------
    sources
        List of source roots to scan.
    exclude
        Glob patterns to exclude (applies to both files and directories).
    symlinks
        Symlink handling policy.
    """
    normalized_sources: list[Path] = [p.resolve() for p in sources]
    for p in normalized_sources:
        if not p.exists():
            raise ValueError(f"Source directory does not exist: {p}")
        if not p.is_dir():
            raise ValueError(f"Source path is not a directory: {p}")

    all_modules: dict[str, DiscoveredModule] = {}
    package_dirs: set[str] = set()

    for source_index, root in enumerate(normalized_sources):
        for rel_path, abs_path in _walk_python_files(
            root,
            exclude=exclude,
            symlinks=symlinks,
        ):
            module_name, package_name, simple_module = _module_name_from_relative_path(rel_path)

            # Track package directories (namespace packages supported)
            if package_name != ROOT_PACKAGE:
                package_dirs.add(package_name)
                # Ensure all parents exist in the package set for hierarchy output
                parent = package_name
                while "." in parent:
                    parent = parent.rsplit(".", 1)[0]
                    package_dirs.add(parent)

            # Collect modules for extraction (exclude __init__.py)
            if simple_module is None:
                continue

            if module_name in all_modules:
                existing = all_modules[module_name]
                raise ValueError(
                    "Module name collision across sources: "
                    f"{module_name} -> {existing.absolute_path} and {abs_path}"
                )

            all_modules[module_name] = DiscoveredModule(
                name=module_name,
                absolute_path=abs_path,
                relative_path=rel_path,
                source=source_index,
            )

            if package_name == ROOT_PACKAGE:
                package_dirs.add(ROOT_PACKAGE)
            else:
                package_dirs.add(package_name)

    packages = _build_package_tree(package_dirs, all_modules)
    return DiscoveryResult(sources=normalized_sources, packages=packages, modules=all_modules)


def _walk_python_files(
    root: Path,
    *,
    exclude: list[str],
    symlinks: SymlinkPolicy,
) -> list[tuple[str, Path]]:
    """Walk python files under root and return (relative_posix_path, absolute_path)."""
    results: list[tuple[str, Path]] = []
    excluder = Excluder(exclude)
    follow_dirs = symlinks in (SymlinkPolicy.DIRS, SymlinkPolicy.ALL)
    allow_file_symlinks = symlinks in (SymlinkPolicy.FILES, SymlinkPolicy.ALL)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_dirs):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        # Prune excluded directories
        excluder.filter_dirnames(rel_dir, dirnames)

        # Enforce symlink directory policy (even if excluded patterns would keep it)
        if symlinks == SymlinkPolicy.FORBID:
            dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]
        elif symlinks == SymlinkPolicy.FILES:
            dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]

        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel_file = f"{rel_dir}/{name}" if rel_dir else name
            abs_file = current / name

            if excluder.is_excluded(rel_file, is_dir=False):
                continue
            if abs_file.is_symlink() and not allow_file_symlinks:
                continue
            results.append((rel_file, abs_file))

    results.sort(key=lambda t: t[0])
    return results


def _module_name_from_relative_path(rel_posix: str) -> tuple[str, str, str | None]:
    """Return (module_name, package_name, simple_module_name_or_None_if_init)."""
    parts = rel_posix.split("/")
    filename = parts[-1]
    stem = filename[:-3]  # strip .py

    if stem == "__init__":
        if len(parts) == 1:
            return ROOT_PACKAGE, ROOT_PACKAGE, None
        package_name = ".".join(parts[:-1])
        return package_name, package_name, None

    module_name = ".".join(parts[:-1] + [stem]) if len(parts) > 1 else stem
    package_name = ".".join(parts[:-1]) if len(parts) > 1 else ROOT_PACKAGE
    return module_name, package_name, stem


def _build_package_tree(
    package_dirs: set[str],
    modules: dict[str, DiscoveredModule],
) -> dict[str, PackageInfo]:
    """Build PackageInfo mapping from discovered package dirs and modules."""
    if package_dirs or modules:
        package_dirs.add(ROOT_PACKAGE)

    # Collect direct module lists per package
    modules_by_package: dict[str, set[str]] = {}
    for module_name in modules:
        if "." in module_name:
            package, simple = module_name.rsplit(".", 1)
        else:
            package, simple = ROOT_PACKAGE, module_name
        modules_by_package.setdefault(package, set()).add(simple)
        package_dirs.add(package)

    # Build subpackage relationships
    subpackages_by_package: dict[str, set[str]] = {p: set() for p in package_dirs}
    for pkg in package_dirs:
        if pkg == ROOT_PACKAGE:
            continue
        if "." in pkg:
            parent, child = pkg.rsplit(".", 1)
        else:
            parent, child = ROOT_PACKAGE, pkg
        subpackages_by_package.setdefault(parent, set()).add(child)

    packages: dict[str, PackageInfo] = {}
    for pkg in sorted(package_dirs):
        packages[pkg] = PackageInfo(
            name=pkg,
            subpackages=sorted(subpackages_by_package.get(pkg, set())),
            modules=sorted(modules_by_package.get(pkg, set())),
        )

    return packages
