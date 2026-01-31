"""Canonical payload builders for on-disk skeleton documents.

These builders define the single source of truth for JSON/YAML structural
output. Formatters should only be responsible for serialization.
"""

from __future__ import annotations

from typing import Any

from anatomize.core.types import ResolutionLevel, Skeleton


def build_hierarchy_document(skeleton: Skeleton) -> dict[str, Any]:
    data: dict[str, Any] = {
        "metadata": {
            "generator_version": skeleton.metadata.generator_version,
            "sources": list(skeleton.metadata.sources),
            "resolution": skeleton.metadata.resolution.value,
            "total_packages": skeleton.metadata.total_packages,
            "total_modules": skeleton.metadata.total_modules,
            "total_classes": skeleton.metadata.total_classes,
            "total_functions": skeleton.metadata.total_functions,
            "token_estimate": skeleton.metadata.token_estimate,
        },
        "packages": {},
    }

    for name in sorted(skeleton.packages.keys()):
        pkg = skeleton.packages[name]
        (data["packages"])[name] = {
            "subpackages": list(pkg.subpackages),
            "modules": list(pkg.modules),
        }

    return data


def build_module_documents(skeleton: Skeleton) -> dict[str, dict[str, Any]]:
    """Return mapping of package_name -> document."""
    packages_modules: dict[str, dict[str, Any]] = {}
    resolution = skeleton.metadata.resolution

    for module_name in sorted(skeleton.modules.keys()):
        module_info = skeleton.modules[module_name]

        if "." in module_name:
            package_name, simple_name = module_name.rsplit(".", 1)
        else:
            package_name, simple_name = "__root__", module_name

        if package_name not in packages_modules:
            packages_modules[package_name] = {}

        module_data: dict[str, Any] = {"path": module_info.path, "source": module_info.source}
        if module_info.doc:
            module_data["doc"] = module_info.doc
        if resolution == ResolutionLevel.SIGNATURES:
            if module_info.imports:
                module_data["imports"] = list(module_info.imports)
            if module_info.constants:
                module_data["constants"] = [_dump_model(c) for c in module_info.constants]

        if module_info.classes:
            module_data["classes"] = [_build_class(cls, resolution) for cls in _sorted_classes(module_info.classes)]

        if module_info.functions:
            module_data["functions"] = [
                _build_function(fn, resolution) for fn in _sorted_functions(module_info.functions)
            ]

        packages_modules[package_name][simple_name] = module_data

    documents: dict[str, dict[str, Any]] = {}
    for package_name in sorted(packages_modules.keys()):
        documents[package_name] = {"package": package_name, "modules": packages_modules[package_name]}
    return documents


def _sorted_classes(classes: list[Any]) -> list[Any]:
    return sorted(classes, key=lambda c: (c.line, c.name))


def _sorted_functions(funcs: list[Any]) -> list[Any]:
    return sorted(funcs, key=lambda f: (f.line, f.name))


def _build_class(cls: Any, resolution: ResolutionLevel) -> dict[str, Any]:
    data: dict[str, Any] = {"name": cls.name, "line": cls.line}
    if cls.doc:
        data["doc"] = cls.doc
    if cls.bases:
        data["bases"] = list(cls.bases)
    if cls.decorators:
        data["decorators"] = list(cls.decorators)
    if cls.is_dataclass:
        data["dataclass"] = True
    if cls.is_pydantic:
        data["pydantic"] = True

    if cls.methods:
        data["methods"] = [_build_function(m, resolution) for m in _sorted_functions(cls.methods)]

    if resolution == ResolutionLevel.SIGNATURES and cls.attributes:
        data["attributes"] = [_dump_model(a) for a in cls.attributes]

    return data


def _build_function(fn: Any, resolution: ResolutionLevel) -> dict[str, Any]:
    data: dict[str, Any] = {"name": fn.name, "line": fn.line}
    if fn.doc:
        data["doc"] = fn.doc
    if fn.decorators:
        data["decorators"] = list(fn.decorators)
    if fn.is_async:
        data["async"] = True

    if resolution == ResolutionLevel.SIGNATURES:
        if fn.signature:
            data["signature"] = fn.signature
        if fn.returns:
            data["returns"] = fn.returns
        if fn.parameters:
            data["parameters"] = [_dump_model(p) for p in fn.parameters]

    return data


def _dump_model(model: Any) -> dict[str, Any]:
    return {k: v for k, v in model.model_dump(exclude_none=True).items()}
