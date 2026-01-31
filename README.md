# code-skeleton

[![CI](https://github.com/BradSegal/code-skeleton/actions/workflows/ci.yml/badge.svg)](https://github.com/BradSegal/code-skeleton/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Generate deterministic, token-efficient maps and review bundles for Python repositories.

`code-skeleton` has two complementary workflows:

1) **Skeletons**: structure-only “code maps” for navigation and architecture understanding.
2) **Packs**: single-file bundles (repomix-style) for external review, with filtering and slicing.

If you want the full guide (modes, slicing, config, determinism guarantees), see `docs/GUIDE.md`.

---

## Installation

```bash
pip install code-skeleton
```

---

## Quick Start (CLI)

### Generate skeletons

```bash
# Generate skeleton output to .skeleton/ (default format: yaml)
code-skeleton generate ./src

# Choose resolution level
code-skeleton generate ./src --level hierarchy
code-skeleton generate ./src --level modules
code-skeleton generate ./src --level signatures

# Write multiple formats
code-skeleton generate ./src --format yaml --format json --format markdown --output .skeleton
```

### Estimate tokens

```bash
code-skeleton estimate ./src --level modules
```

### Validate (and fix) skeleton output

```bash
# Validate existing output directory against sources
code-skeleton validate .skeleton --source ./src

# Rewrite the skeleton output to match regenerated content (strict, atomic-ish replacement)
code-skeleton validate .skeleton --source ./src --fix
```

### Pack a repository into an AI-friendly bundle

```bash
# If --format is omitted, it is inferred from --output when the extension is known
code-skeleton pack . --output codebase.jsonl
code-skeleton pack . --output codebase.md

# Full bundle
code-skeleton pack . --format markdown --output codebase.md

# Filter by globs
code-skeleton pack . --include "src/**" --ignore "**/__pycache__/**" --output src-only.md

# Forward dependency closure (entrypoint + everything it imports)
code-skeleton pack . --entry src/code_skeleton/cli.py --deps --output slice.md

# Reverse dependency closure (module + everything that imports it)
code-skeleton pack . --target src/code_skeleton/cli.py --reverse-deps --output importers.md

# Reverse + forward (importers plus what they import)
code-skeleton pack . --target src/code_skeleton/cli.py --reverse-deps --deps --output importers-and-deps.md

# Token-efficient Python compression (signatures/imports/constants)
code-skeleton pack . --compress --output compressed.md

# Make markdown robust to embedded ``` fences (default)
code-skeleton pack . --content-encoding fence-safe --output safe.md

# Maximum robustness (content is base64-encoded UTF-8)
code-skeleton pack . --content-encoding base64 --output safe.base64.md

# Split output into multiple files (markdown/plain only)
code-skeleton pack . --split-output 500kb --output codebase.md

# Hard cap output (bytes or tokens)
code-skeleton pack . --max-output 20_000t --output codebase.md

# Print a per-file content token tree to stdout
code-skeleton pack . --token-count-tree --output codebase.md

# JSONL (stream-friendly)
code-skeleton pack . --format jsonl --output codebase.jsonl

# Hybrid mode (skeleton-style summaries + selective fill)
# - defaults to JSONL when --mode hybrid is set
# - Python files default to summary; non-Python defaults to metadata-only
code-skeleton pack . --mode hybrid --output hybrid.jsonl

# Hybrid: include full content for a slice and fit within a hard token budget
code-skeleton pack . --mode hybrid --max-output 50_000t --fit-to-max-output \
  --content "src/pkg/**" --output hybrid.slice.jsonl
```

Reference-based usage slicing (requires Pyright language server):

```bash
code-skeleton pack . --target src/code_skeleton/cli.py --uses --slice-backend pyright --output uses.md
```

---

## Python API

### Generate skeletons in code

```python
from code_skeleton import SkeletonGenerator
from code_skeleton.formats import OutputFormat, write_skeleton

gen = SkeletonGenerator(sources=["./src"])
skeleton = gen.generate(level="modules")

print("Modules:", skeleton.metadata.total_modules)
print("Classes:", skeleton.metadata.total_classes)
print("Functions:", skeleton.metadata.total_functions)
print("Estimated tokens:", skeleton.token_estimate)

write_skeleton(skeleton, ".skeleton", formats=[OutputFormat.YAML, OutputFormat.JSON])
```

### Key exported objects

- `code_skeleton.SkeletonGenerator`: orchestrates discovery + extraction.
- `code_skeleton.formats.write_skeleton`: writes YAML/JSON/Markdown plus schemas and `manifest.json`.
- `code_skeleton.validation.validate_skeleton_dir`: strict validator with optional `fix`.

---

## Configuration (`.code-skeleton.yaml`)

The CLI can auto-discover `.code-skeleton.yaml`. Generation commands use config from the current working directory (or explicit `--config`). `pack` discovers config relative to the chosen `ROOT` when `--config` is not provided.

Minimal config:

```yaml
sources:
  - src
output: .skeleton
level: modules
formats: [yaml, json, markdown]
exclude:
  - __pycache__/
  - "*.pyc"
symlinks: forbid # forbid|files|dirs|all
workers: 0 # 0 = auto

pack:
  format: markdown # markdown|plain|json|xml|jsonl
  mode: bundle # bundle|hybrid
  output: code-skeleton-pack.md # if the extension is known, it must match `format`
  include: []
  ignore: []
  ignore_files: []
  respect_standard_ignores: true
  symlinks: forbid # forbid|files|dirs|all
  max_file_bytes: 1000000
  workers: 0 # 0 = auto
  token_encoding: cl100k_base
  compress: false
  content_encoding: fence-safe # verbatim|fence-safe|base64 (markdown disallows verbatim)
  line_numbers: false
  no_structure: false
  no_files: false
  max_output: null # e.g. "500kb" or "20_000t"
  split_output: null # e.g. "500kb" or "20_000t"
  fit_to_max_output: false
  # Hybrid representation rules (repeatable patterns). Precedence: meta < summary < content.
  meta: []
  summary: []
  content: []
  summary_config:
    max_depth: 3
    max_keys: 200
    max_items: 200
    max_headings: 200
  python_roots: [] # defaults to ["src"] if present, else ["."]
  slice_backend: imports # imports|pyright
  uses_include_private: false
  pyright_langserver_cmd: "pyright-langserver --stdio"
```

Exclude patterns use gitignore-like semantics and are applied relative to each configured root.

---

## Output artifacts

### Skeleton output directory

`write_skeleton(...)` and `code-skeleton generate ... --output DIR` create:
- `hierarchy.yaml|json|md` / `modules.*` / `signatures.*` depending on selected formats and level
- `schemas/*.json` embedded with the package
- `manifest.json` (SHA-256 per output file and metadata for validation)

### Pack output file(s)

`code-skeleton pack` writes one or more files depending on splitting:
- `code-skeleton-pack.md` (or `.txt|.json|.xml`)
- if split: `code-skeleton-pack.1.md`, `code-skeleton-pack.2.md`, …

Each pack artifact starts with a lightweight, deterministic overview (and, if enabled, a structure tree) before file blocks/records.

Token reporting:
- **Artifact tokens**: exact tokens of the written output file(s).
- **Content tokens**: tokens of file contents only (useful for budgeting and “what’s expensive”).

---

## Determinism and strictness

- Deterministic ordering (paths and symbols sorted).
- No timestamps in outputs.
- Parse failures are hard failures (no partial output).
- Validation is strict; `--fix` replaces output with regenerated content.

---

## Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"

python -m ruff check .
python -m mypy -p code_skeleton
python -m pytest
```

Optional local benchmark:

```bash
.venv/bin/python scripts/bench_pack.py . --compress --workers 0
```

---

## Tests

Tests are indexed via pytest markers in `pyproject.toml` and documented in `tests/README.md`:
- `unit`: fast, isolated tests
- `integration`: filesystem-level tests
- `e2e`: CLI-level tests

---

## License

MIT
