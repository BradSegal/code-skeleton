"""Measure end-to-end review acquisition for an exact repository checkout."""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

from anatomize.review import ReviewApplication, canonical_review_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--repository-id")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"repository root does not exist: {root}")
    if args.max_seconds is not None and args.max_seconds <= 0:
        parser.error("max-seconds must be positive")

    started = time.perf_counter()
    bundle = ReviewApplication().start(root, repository_id=args.repository_id)
    elapsed = time.perf_counter() - started
    raw = canonical_review_bytes(bundle)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    peak_rss_mib = usage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    state = bundle.manifest.source_states[0].source_state
    failures = []
    if args.max_seconds is not None and elapsed > args.max_seconds:
        failures.append("elapsed_seconds")
    if bundle.manifest.status.value not in {"complete", "partial"}:
        failures.append("session_status")
    result = {
        "schema_version": "1.0.0",
        "root": str(root),
        "source_state_id": state.state_id,
        "source_files": state.file_count,
        "session_status": bundle.manifest.status.value,
        "provider_statuses": {
            item.provider_id: item.status.value for item in bundle.manifest.providers
        },
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mib": round(peak_rss_mib, 3),
        "session_bytes": len(raw),
        "max_seconds": args.max_seconds,
        "failures": failures,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, output)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
