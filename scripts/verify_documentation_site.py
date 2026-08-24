#!/usr/bin/env python3
"""Inspect a built documentation site as a bounded public release artifact."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ALLOWED_SOURCE_SUFFIXES = {".css", ".json", ".md"}
FORBIDDEN_SOURCE_PARTS = {".git", "__pycache__", "audit", "repomix", "tickets"}
FORBIDDEN_PUBLIC_TEXT = (
    "/home/",
    "C:\\Users\\",
    "tickets/",
    "repomix/",
)
MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
MAX_SITE_BYTES = 25 * 1024 * 1024
ALLOWED_SITE_SUFFIXES = {
    ".css",
    ".gz",
    ".html",
    ".ico",
    ".js",
    ".json",
    ".map",
    ".png",
    ".svg",
    ".txt",
    ".xml",
}


def _markdown_nav_paths(config: str) -> set[str]:
    return set(re.findall(r":\s+([A-Za-z0-9_./-]+\.md)\s*$", config, flags=re.MULTILINE))


def _source_inventory(docs_dir: Path, config: str) -> list[str]:
    paths: list[str] = []
    for path in sorted(docs_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Documentation source cannot contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(docs_dir)
        if set(relative.parts).intersection(FORBIDDEN_SOURCE_PARTS):
            raise ValueError(f"Private documentation path entered the public source: {relative}")
        if path.suffix not in ALLOWED_SOURCE_SUFFIXES:
            raise ValueError(f"Undeclared documentation source type: {relative}")
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            raise ValueError(f"Documentation source exceeds the per-file bound: {relative}")
        paths.append(relative.as_posix())
    markdown = {path for path in paths if path.endswith(".md")}
    omitted = markdown.difference(_markdown_nav_paths(config))
    if omitted:
        raise ValueError(f"Markdown pages are absent from site navigation: {sorted(omitted)}")
    return paths


def verify(root: Path, site_dir: Path) -> dict[str, object]:
    root = root.resolve()
    site_dir = site_dir.resolve()
    docs_dir = root / "docs"
    config_path = root / "mkdocs.yml"
    config = config_path.read_text(encoding="utf-8")
    if "strict: true" not in config or "https://bradsegal.github.io/anatomize/" not in config:
        raise ValueError("MkDocs must use strict mode and the canonical project URL")
    source_paths = _source_inventory(docs_dir, config)
    required = {
        site_dir / "index.html",
        site_dir / "404.html",
        site_dir / "sitemap.xml",
        site_dir / "search" / "search_index.json",
        site_dir / "generated" / "schemas" / "session.schema.json",
    }
    missing = sorted(str(path.relative_to(site_dir)) for path in required if not path.is_file())
    if missing:
        raise ValueError(f"Built site omits required public artifacts: {missing}")
    total_bytes = 0
    files = 0
    html_pages = 0
    for path in sorted(site_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Built site cannot contain symlinks: {path}")
        if not path.is_file():
            continue
        files += 1
        total_bytes += path.stat().st_size
        if path.suffix.lower() not in ALLOWED_SITE_SUFFIXES:
            raise ValueError(f"Unexpected built-site file type: {path.relative_to(site_dir)}")
        if path.suffix.lower() not in {".html", ".json", ".txt", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PUBLIC_TEXT:
            if forbidden in text:
                raise ValueError(f"Private or local text {forbidden!r} leaked into {path.relative_to(site_dir)}")
        if path.suffix.lower() == ".html":
            html_pages += 1
            if '<html lang="en"' not in text or 'name="viewport"' not in text:
                raise ValueError(f"HTML accessibility metadata missing from {path.relative_to(site_dir)}")
            if re.search(r"<script[^>]+src=[\"']https?://", text):
                raise ValueError(f"Remote script entered the documentation site: {path.relative_to(site_dir)}")
    if total_bytes > MAX_SITE_BYTES:
        raise ValueError(f"Built site is {total_bytes} bytes; limit is {MAX_SITE_BYTES}")
    css = (docs_dir / "stylesheets" / "extra.css").read_text(encoding="utf-8")
    if ":focus-visible" not in css or "prefers-reduced-motion" not in css:
        raise ValueError("Custom presentation must preserve focus and reduced-motion behavior")
    index = (site_dir / "index.html").read_text(encoding="utf-8")
    for phrase in ("Review a repository", "Integrate an agent", "Build a provider"):
        if phrase not in index:
            raise ValueError(f"Site home omits the {phrase!r} entry path")
    return {
        "schema_version": "1.0.0",
        "site_url": "https://bradsegal.github.io/anatomize/",
        "source_files": len(source_paths),
        "built_files": files,
        "html_pages": html_pages,
        "built_bytes": total_bytes,
        "search_index": True,
        "remote_scripts": False,
        "private_text": False,
        "focus_visible": True,
        "reduced_motion": True,
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(arguments.root, arguments.site_dir)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
