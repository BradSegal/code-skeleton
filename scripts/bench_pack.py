from __future__ import annotations

import argparse
import time
from pathlib import Path

from anatomize.core.policy import SymlinkPolicy
from anatomize.pack.formats import ContentEncoding, PackFormat
from anatomize.pack.runner import pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark anatomize pack locally.")
    parser.add_argument("root", nargs="?", default=".", help="Repo root")
    parser.add_argument("--compress", action="store_true")
    parser.add_argument("--format", default="markdown", choices=[f.value for f in PackFormat])
    parser.add_argument("--workers", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out = root / ".bench-pack-output"
    out.mkdir(exist_ok=True)

    start = time.perf_counter()
    res = pack(
        root=root,
        output=out / f"pack.{args.format}",
        fmt=PackFormat(args.format),
        include=[],
        ignore=[],
        ignore_files=[],
        respect_standard_ignores=True,
        symlinks=SymlinkPolicy.FORBID,
        max_file_bytes=0,
        workers=args.workers,
        token_encoding="cl100k_base",
        compress=args.compress,
        content_encoding=ContentEncoding.FENCE_SAFE,
        line_numbers=False,
        include_structure=True,
        include_files=True,
        max_output=None,
        split_output=None,
        entries=[],
        deps=False,
        python_roots=[],
    )
    elapsed = time.perf_counter() - start
    tokens = sum(a.tokens for a in res.artifacts)
    print(
        f"elapsed_s={elapsed:.3f} artifacts={len(res.artifacts)} "
        f"artifact_tokens={tokens} content_tokens={res.content_tokens}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
