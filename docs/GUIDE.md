# Guide: anatomize

This guide describes the concepts, tools, and patterns in this repository and how to use them effectively.

---

## What this tool is for

`anatomize` is built for two related jobs:

1) **Skeletons (code maps)**: small, structured representations of a Python codebase (packages/modules/classes/functions/signatures) for fast navigation and architecture review.
2) **Packs (review bundles)**: deterministic single-file (or split) bundles of repository contents for external review, optimized for token budgets.

The intended pattern is:
- use **skeletons** to understand “what exists and where”
- use **packs** to extract a focused slice for deep review (forward deps, reverse deps, or references via Pyright)

---

## Core principles

### Determinism
Outputs are stable across runs for the same repository state:
- deterministic sorting (paths and extracted symbols)
- no timestamps
- stable token counting for the chosen encoding

### Strictness (no soft fallbacks)
If a requested operation cannot be satisfied precisely, the command fails:
- parse errors are hard failures
- dependency closure selection is complete-or-fail
- backend selection is explicit (e.g. `--uses` requires `--slice-backend pyright`)

### Safety
`pack` does not emit binary file content, and enforces max file size (configurable).

---

## Concepts and data model (skeletons)

### Source roots (“container dirs”)
Skeleton generation (`generate`, `estimate`, `validate`) takes one or more **source roots** (directories). A source root is a container; packages/modules are discovered underneath it.

### Resolution levels
Resolution levels control how much structure is extracted:
- `hierarchy`: package tree and module list only
- `modules`: adds module docstring line, top-level classes/functions (names + line numbers)
- `signatures`: adds function signatures/parameters/returns, class methods/attributes/constants, plus import statements

### Schemas and manifests
When writing skeleton output to disk:
- `schemas/*.json` are written into the output directory so JSON artifacts are self-describing.
- `manifest.json` captures a deterministic checksum for every output file to support strict validation.

---

## CLI commands (skeletons)

### `generate`
Generates a skeleton from sources and writes YAML/JSON/Markdown.

Key options:
- `--level`: `hierarchy|modules|signatures`
- `--format`: repeatable (`yaml|json|markdown`)
- `--output`: directory (default `.skeleton`)
- `--exclude`: gitignore-like patterns
- `--symlinks`: `forbid|files|dirs|all`
- `--workers`: extraction worker count (0 = auto)

Examples:
```bash
anatomize generate ./src --output .skeleton --level signatures --format yaml --format json
```

### `estimate`
Computes the same skeleton but prints a token estimate and summary.

Example:
```bash
anatomize estimate ./src --level modules
```

### `validate`
Regenerates expected output in a temp directory and compares to an existing skeleton output directory.

- Without `--fix`, validation fails if output differs.
- With `--fix`, the output directory is replaced with regenerated content.

Example:
```bash
anatomize validate .skeleton --source ./src
anatomize validate .skeleton --source ./src --fix
```

---

## CLI command: `pack` (review bundles)

`pack` produces a deterministic artifact that contains:
- a lightweight, deterministic overview (first artifact only when split)
- a directory structure section (optional)
- a file listing / file blocks (optional)
- stable token metrics

### Modes
`pack` supports two explicit modes:
- `--mode bundle` (default): repomix-style bundling with full file contents (text only; binary omitted).
- `--mode hybrid`: emits a skeleton-like index and selectively “fills in” full contents based on rules.

Hybrid mode is intended for token efficiency and navigability:
- Python files default to `summary` (signatures/imports/line numbers).
- Non-Python files default to `meta` (metadata only) unless explicitly summarized or included as content.
- If `--format` is omitted, it is inferred from `--output` when the extension is known (`.md|.txt|.json|.xml|.jsonl`).
- Hybrid output is **JSONL**. If `--format` is not provided, it defaults to JSONL when `--mode hybrid` is used. Non-`.jsonl` output paths are rejected.

### Output formats
- `--format markdown` (best for human review)
- `--format plain` (best for robust plain-text ingestion)
- `--format json` / `--format xml` (best for machine processing)
- `--format jsonl` (stream-friendly, line-delimited JSON)

### Content encoding (robustness)
File contents can break Markdown structure (e.g. embedded ``` fences). Control how content is emitted with:
- `--content-encoding fence-safe` (default): picks a fence length that cannot occur in the content.
- `--content-encoding base64`: emits base64-encoded UTF-8 content (max robustness, less readable).

Note: Markdown output intentionally disallows `--content-encoding verbatim`.

### Hybrid representations (fill-in controls)
In hybrid mode, each file record has an explicit representation:
- `meta`: path/language/is_binary/size/content_tokens
- `summary`: structural summary
- `content`: full content

Controls (repeatable):
- `--meta PATTERN`
- `--summary PATTERN`
- `--content PATTERN`

Precedence is deterministic and documented:
- `meta` < `summary` < `content` (content overrides summary overrides meta).

Supported non-Python summaries:
- JSON/YAML/TOML: key-path outline (bounded by `--summary-*` options)
- Markdown: headings outline

### Fit-to-budget (explicit)
Hybrid mode can deterministically fit within a hard cap:
- `--max-output 50_000t --fit-to-max-output`

If enabled, `pack` may downgrade representations (e.g. `summary` → `meta`) to satisfy `--max-output` and emits an auditable `selection_trace`.
If it cannot satisfy the cap, it fails.

### Token metrics
`pack` reports:
- **Artifact tokens**: exact tokens of the written output file(s).
- **Content tokens**: tokens for file contents only (useful for budgeting per-file).

Encoding is controlled by:
- `--token-encoding` (default: `cl100k_base`)

### Filtering (include/ignore)
Filtering is gitignore-like and deterministic:
- `--include PATTERN` repeatable allowlist (when specified, only matching files are eligible)
- `--ignore PATTERN` repeatable ignore rules (supports `!` negation)
- `--ignore-file PATH` to load ignore patterns from files
- `--respect-standard-ignores/--no-respect-standard-ignores` to read `.repomixignore/.ignore/.gitignore/.git/info/exclude` under `ROOT`

#### Pattern semantics (summary)
Patterns are evaluated in order; the last matching rule wins:
- blank lines and `#` comments are ignored
- `!pattern` negates a previous match (re-include)
- trailing `/` marks directory-only rules (excludes the directory and everything under it)
- leading `/` anchors to the root being scanned
- patterns containing `/` match against paths; patterns without `/` match against basenames
- `*` and `**` wildcards are supported

### Symlinks
Symlink behavior is explicit via `--symlinks`:
- `forbid`: skip all symlinks
- `files`: allow file symlinks only
- `dirs`: allow directory symlinks only
- `all`: allow all symlinks

### Slicing modes (how you select files)

#### 1) Forward dependency closure (`--entry ... --deps`)
Selects the transitive closure of **local Python imports** from entry files.

Example:
```bash
anatomize pack . --entry src/app.py --deps --output app-slice.md
```

Strictness: if your ignore/include filters exclude any file required by the closure, `pack` fails with an actionable error.

#### 2) Reverse dependency closure (importers): `--target/--module --reverse-deps`
Selects a Python module and **all local Python modules that import it**, transitively.

Example:
```bash
anatomize pack . --target src/pkg/core.py --reverse-deps --output importers.md
```

Combine with forward deps to include “importers + everything they import”:
```bash
anatomize pack . --target src/pkg/core.py --reverse-deps --deps --output slice.md
```

#### 3) Reference-based usage slicing (Pyright): `--uses --slice-backend pyright`
This selects files that reference symbols in the target module, using Pyright’s language server.

Example:
```bash
anatomize pack . --target src/pkg/core.py --uses --slice-backend pyright --output uses.md
```

Notes:
- This is intentionally strict: if pyright cannot run, `pack` fails. There is no fallback to import-only slicing when `--uses` is requested.
- Configure the server command via `--pyright-langserver-cmd`.

### Splitting and limits (scalability)
To keep bundles bounded and CI-friendly:
- `--max-output` is a hard cap (fail before writing final output).
- `--split-output` splits into multiple numbered artifacts (markdown/plain/jsonl only).

Examples:
```bash
anatomize pack . --split-output 500kb --output codebase.md
anatomize pack . --max-output 20_000t --output codebase.md
```

### Performance (`--workers`)
`pack` can read/compress files in parallel:
- `--workers 0` chooses an automatic worker count

---

## Python API details

### Skeleton generation
- `anatomize.generators.main.SkeletonGenerator`: orchestrates discovery and extraction.
- `anatomize.core.discovery.discover`: finds modules/packages under roots.
- `anatomize.core.extractor.SymbolExtractor`: tree-sitter extraction (strict parsing).
- `anatomize.formats.write_skeleton`: writes outputs + schemas + manifest.
- `anatomize.validation.validate_skeleton_dir`: strict validation + optional fix.

### Pack API
- `anatomize.pack.runner.pack`: high-level pack API used by the CLI.
- `anatomize.pack.deps`:
  - forward closure: `dependency_closure(...)`
  - reverse closure: `reverse_dependency_closure(...)`
- `anatomize.pack.formats`: output rendering (markdown/plain/json/xml) + markdown safety.
- `anatomize.pack.pyright_lsp`: minimal LSP client (stdio) for Pyright usage slicing.

---

## Pattern glossary

### Code Maps (skeletons)
Use skeletons as “index files”:
- fast navigation
- architecture-level reasoning
- symbol-level location and signatures without full bodies

### Review Bundles (packs)
Use packs to prepare a bounded context for:
- PR review
- external audit
- “explain this subsystem” prompts
- LLM evaluation with a fixed token budget

Recommended workflow:
1) generate skeletons at `modules` or `signatures`
2) identify entrypoints or targets
3) pack with slicing + compression + splitting to fit the budget

---

## Testing patterns

Tests are organized into a small pyramid and marked for discoverability:
- `unit`: isolated logic tests
- `integration`: filesystem-level behaviors
- `e2e`: CLI-level workflows

See `tests/README.md` for exact marker commands.

---

## Troubleshooting

### “Selection requires files that were not included by filtering”
You requested a slicing mode (`--deps`, `--reverse-deps`, or `--uses`) but your ignore/include rules excluded required files. Fix by:
- widening `--include`
- removing an `--ignore` rule
- using `!negation` patterns to re-include a specific path

### “Pyright language server not found”
You requested `--uses --slice-backend pyright` but the server command could not be started. Fix by:
- installing pyright and `pyright-langserver`
- or setting `--pyright-langserver-cmd` to the correct command

### Markdown output looks broken
Use `--content-encoding fence-safe` or switch to `--format plain`.
