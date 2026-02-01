# anatomize

[![CI](https://github.com/BradSegal/anatomize/actions/workflows/ci.yml/badge.svg)](https://github.com/BradSegal/anatomize/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Generate deterministic, token-efficient maps and review bundles for Python repositories.

`anatomize` has two complementary workflows:

1) **Skeletons**: structure-only “code maps” for navigation and architecture understanding.
2) **Packs**: single-file bundles ([repomix](https://repomix.com/)-style) for external review, with filtering and slicing.

If you want the full guide (modes, slicing, config, determinism guarantees), see `docs/GUIDE.md`.

---

## Installation

```bash
pip install anatomize
```

---

## Quick Start (CLI)

### Generate skeletons

```bash
# Scaffold config for the common workflow “src detailed, tests minimal”
anatomize init --preset standard

# Generate all configured outputs from .anatomize.yaml (writes into .anatomy/*)
anatomize generate

# Or run ad-hoc generation for a specific source path
anatomize generate ./src

# Choose resolution level
anatomize generate ./src --level hierarchy --output .anatomy
anatomize generate ./src --level modules --output .anatomy
anatomize generate ./src --level signatures --output .anatomy

# Write multiple formats
anatomize generate ./src --format yaml --format json --format markdown --output .anatomy
```

### Estimate tokens

```bash
anatomize estimate ./src --level modules
```

### Validate (and fix) skeleton output

```bash
# Validate all configured outputs (from .anatomize.yaml)
anatomize validate

# Rewrite configured outputs to match regenerated content (strict, atomic-ish replacement)
anatomize validate --fix

# Or validate a specific directory against explicit sources
anatomize validate .anatomy --source ./src
```

### Pack a repository into an AI-friendly bundle

```bash
# If --format is omitted, it is inferred from --output when the extension is known
anatomize pack . --output codebase.jsonl
anatomize pack . --output codebase.md

# Full bundle
anatomize pack . --format markdown --output codebase.md

# Filter by globs
anatomize pack . --include "src/**" --ignore "**/__pycache__/**" --output src-only.md

# Forward dependency closure (entrypoint + everything it imports)
anatomize pack . --entry src/anatomize/cli.py --deps --output slice.md

# Reverse dependency closure (module + everything that imports it)
anatomize pack . --target src/anatomize/cli.py --reverse-deps --output importers.md

# Reverse + forward (importers plus what they import)
anatomize pack . --target src/anatomize/cli.py --reverse-deps --deps --output importers-and-deps.md

# Token-efficient Python compression (signatures/imports/constants)
anatomize pack . --compress --output compressed.md

# Make markdown robust to embedded ``` fences (default)
anatomize pack . --content-encoding fence-safe --output safe.md

# Maximum robustness (content is base64-encoded UTF-8)
anatomize pack . --content-encoding base64 --output safe.base64.md

# Split output into multiple files (markdown/plain only)
anatomize pack . --split-output 500kb --output codebase.md

# Hard cap output (bytes or tokens)
anatomize pack . --max-output 20_000t --output codebase.md

# Print a per-file content token tree to stdout
anatomize pack . --token-count-tree --output codebase.md

# JSONL (stream-friendly)
anatomize pack . --format jsonl --output codebase.jsonl

# Hybrid mode (skeleton-style summaries + selective fill)
# - defaults to JSONL when --mode hybrid is set
# - Python files default to summary; non-Python defaults to metadata-only
anatomize pack . --mode hybrid --output hybrid.jsonl

# Hybrid: include full content for a slice and fit within a hard token budget
anatomize pack . --mode hybrid --max-output 50_000t --fit-to-max-output \
  --content "src/pkg/**" --output hybrid.slice.jsonl
```

Reference-based usage slicing (requires Pyright language server):

```bash
anatomize pack . --target src/anatomize/cli.py --uses --slice-backend pyright --output uses.md
```

---

## Python API

### Generate skeletons in code

```python
from anatomize import SkeletonGenerator
from anatomize.formats import OutputFormat, write_skeleton

gen = SkeletonGenerator(sources=["./src"])
skeleton = gen.generate(level="modules")

print("Modules:", skeleton.metadata.total_modules)
print("Classes:", skeleton.metadata.total_classes)
print("Functions:", skeleton.metadata.total_functions)
print("Estimated tokens:", skeleton.metadata.token_estimate)

write_skeleton(skeleton, ".anatomy", formats=[OutputFormat.YAML, OutputFormat.JSON])
```

### Key exported objects

- `anatomize.SkeletonGenerator`: orchestrates discovery + extraction.
- `anatomize.formats.write_skeleton`: writes YAML/JSON/Markdown plus schemas and `manifest.json`.
- `anatomize.validation.validate_skeleton_dir`: strict validator with optional `fix`.

---

## Configuration (`.anatomize.yaml`)

The CLI can auto-discover `.anatomize.yaml`. Generation commands use config from the current working directory (or explicit `--config`). `pack` discovers config relative to the chosen `ROOT` when `--config` is not provided.

Minimal config:

```yaml
output: .anatomy

sources:
  - path: src
    output: src
    level: modules
  - path: tests
    output: tests
    level: hierarchy

# Defaults applied to sources that omit fields
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
  output: anatomize-pack.md # if the extension is known, it must match `format`
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

Tip: `anatomize init --preset standard` scaffolds `.anatomize.yaml` with the common pattern “src detailed, tests minimal”.

---

## Output artifacts

### Skeleton output directory

`write_skeleton(...)` and `anatomize generate ... --output DIR` create:
- `hierarchy.yaml|json|md` / `modules.*` / `signatures.*` depending on selected formats and level
- `schemas/*.json` embedded with the package
- `manifest.json` (SHA-256 per output file and metadata for validation)

When `anatomize generate` runs from `.anatomize.yaml`, it writes one skeleton directory per configured source:
- `.anatomy/src/...`
- `.anatomy/tests/...`

### Pack output file(s)

`anatomize pack` writes one or more files depending on splitting:
- `anatomize-pack.md` (or `.txt|.json|.xml`)
- if split: `anatomize-pack.1.md`, `anatomize-pack.2.md`, …

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
python -m mypy -p anatomize
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
