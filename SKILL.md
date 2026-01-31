# Skill: code packing & extraction (anatomize)

## Purpose
Use `anatomize` to extract a repository (or a slice of it) into **deterministic, token-efficient artifacts** suitable for external review by an AI model or a human.

This skill is about *using the tool*, not modifying it.

## When to use
Use this skill when you need to:
- Bundle a repo into a single artifact (repomix-style) for review.
- Produce a token-efficient index + selective “fill-in” (hybrid mode).
- Slice by dependency closure (`--entry --deps`) or by reverse imports (`--reverse-deps`).
- Constrain output size strictly (`--max-output`, `--split-output`).
- Emit a machine-consumable stream (`--format jsonl`) for pipelines.
- Generate “skeleton” navigation artifacts (optional companion workflow).

## Core principles (how to operate)
- **Determinism:** repeated runs with the same inputs should produce the same output ordering/content.
- **Strictness:** mismatched settings (format vs output extension, hybrid vs non-JSONL) should be treated as hard errors.
- **Token budgeting:** prefer hybrid mode + targeted fill-in; reserve full-bundle for small scopes.
- **Robustness:** choose `--content-encoding` so the artifact cannot be structurally broken by file contents.
- **No partial artifacts:** prefer modes that write atomically/safely; treat failures as “no output”.

## Quick start commands

### 1) Full bundle (repomix-style)
```bash
anatomize pack . --output codebase.md
```

Add stable line numbers (useful for review comments that reference exact lines):
```bash
anatomize pack . --output codebase.md --line-numbers
```

Recommended for robustness (content cannot break Markdown fences):
```bash
anatomize pack . --output codebase.md --content-encoding fence-safe
```

Maximum robustness (base64 in content fields):
```bash
anatomize pack . --output codebase.md --content-encoding base64
```

Token reduction for Python-heavy repos (replaces Python file content with a compressed representation):
```bash
anatomize pack . --output codebase.md --compress
```

### 2) Token-efficient “hybrid” bundle (index + selective content)
Hybrid output is **JSONL** and is optimized for downstream tooling:
```bash
anatomize pack . --mode hybrid --output hybrid.jsonl
```

Fill in a subset with full content (repeatable patterns):
```bash
anatomize pack . --mode hybrid --output hybrid.jsonl \
  --content "src/my_pkg/**" \
  --summary "docs/**" \
  --meta "**/*.lock"
```

### 3) Strict output budgeting
Hard cap output size (bytes or tokens):
```bash
anatomize pack . --output codebase.md --max-output 50_000t
```

Split into multiple artifacts (markdown/plain/jsonl):
```bash
anatomize pack . --output codebase.md --split-output 500kb
```

Hybrid can be made to fit a strict budget (auditable downgrades via `selection_trace`):
```bash
anatomize pack . --mode hybrid --output hybrid.jsonl \
  --max-output 50_000t --fit-to-max-output \
  --content "src/my_pkg/**"
```

## Format selection rules (important)
- If `--format` is omitted, it is inferred from `--output` when the extension is known:
  - `.md|.txt|.json|.xml|.jsonl`
- If both `--format` and `--output` are provided and they conflict, the command **fails**.
- `--mode hybrid` requires JSONL output:
  - If `--format` is omitted, it defaults to JSONL.
  - If `--output` is provided, it must end in `.jsonl`.

## Output composition controls (what’s included)
- `--no-structure`: omit the structure/tree section from the artifact.
- `--no-files`: omit file blocks/records entirely (structure + metadata only).
- `--line-numbers`: prefix lines (bundle modes) and/or include line-oriented summaries (hybrid) when applicable.

## Slicing / extraction strategies

### A) Forward dependency closure (entrypoints → what they import)
Use this to extract a “module slice” with its local import dependencies:
```bash
anatomize pack . --output slice.md \
  --entry src/my_pkg/entry.py --deps
```

Multiple entrypoints are allowed:
```bash
anatomize pack . --output slice.md \
  --entry src/my_pkg/a.py --entry src/my_pkg/b.py --deps
```

### B) Reverse dependency closure (who imports a target)
Use this to capture all local importers of a module/file:
```bash
anatomize pack . --output importers.md \
  --target src/my_pkg/core.py --reverse-deps
```

Combine reverse + forward:
```bash
anatomize pack . --output importers-and-deps.md \
  --target src/my_pkg/core.py --reverse-deps --deps
```

### C) Reference-based “uses” slicing (requires Pyright)
Use this when you need “who references these symbols” rather than “who imports”:
```bash
anatomize pack . --output uses.md \
  --target src/my_pkg/core.py --uses --slice-backend pyright
```

#### Pyright operational knobs
- `--pyright-langserver-cmd "pyright-langserver --stdio"`: override the launched command (useful for envs/paths).
- `--uses-include-private`: include underscore-prefixed symbols in reference selection.

## Target selection inputs
- `--target PATH`: select a Python file (path relative to root unless absolute).
- `--module package.sub`: select a Python module by import name (depends on `--python-root`).
- `--python-root PATH`: declare import roots for module resolution (repeatable).

## Include/ignore hygiene
- Use `--include` for an allowlist; repeatable.
- Use `--ignore` for globs; supports gitignore-style negation `!`.
- Use `--ignore-file` to load patterns from one or more ignore files.
- Use `--respect-standard-ignores` to load `.repomixignore/.ignore/.gitignore/.git/info/exclude` from the chosen root.
- Keep patterns repository-relative (they match against POSIX-style relative paths like `src/pkg/a.py`).

## Token diagnostics
Print a per-file token breakdown (stdout) to identify expensive paths:
```bash
anatomize pack . --output codebase.md --token-count-tree
```

## Output sizing / limits (strict)
`--max-output` and `--split-output` accept:
- tokens: `20000t` or `20_000t`
- bytes: `500kb`, `2mb`, `1gb`, `1000000b`, or bare `123` (bytes)

## Reading JSONL output (what to expect)
JSONL output is line-delimited:
- First record: `{"type":"meta", ...}` (includes `schema_version`, mode, settings)
- Optional `{"type":"structure", ...}`
- Then many `{"type":"file", ...}` records with `representation`:
  - `meta` (metadata only)
  - `summary` (structured summary)
  - `content` (full content, with `content_encoding`)

## Operational knobs (performance and safety)
- `--workers N`: parallelism (0 = auto).
- `--symlinks forbid|files|dirs|all`: whether to traverse symlinks.
- `--max-file-bytes N`: hard-fail if any single file exceeds this size (0 disables).
- `--token-encoding NAME`: `tiktoken` encoding for token counts (default is commonly `cl100k_base`).

## Binary / encoding behavior (important for “completeness”)
- Binary files are detected conservatively (NUL bytes or non-UTF-8) and their content is omitted.
- Text is treated as UTF-8; “non-UTF-8 text” is treated as binary and will not be emitted as content.

## Companion workflow: skeleton navigation artifacts (optional)
If you want a persistent “code map” directory rather than a single bundle:
```bash
anatomize generate ./src --output .skeleton --level modules --format yaml --format json --format markdown
anatomize validate .skeleton --source ./src
anatomize estimate ./src --level modules
```

## Configuration-based usage (repeatable runs)
You can store defaults in `.anatomize.yaml` under `pack:` and then run:
```bash
anatomize pack .
```
Override any setting via CLI flags (subject to strict mismatch rules for format/output).

## Recommended “extraction recipes”
- **External review of a subpackage:** hybrid + `--content "src/pkg/**"` + `--fit-to-max-output`.
- **Architecture understanding:** hybrid default (mostly summaries/meta) + include structure.
- **Bug hunt:** `--target ... --reverse-deps --deps` to capture the local impact surface.
- **Prompt-injection safety:** use `--content-encoding base64` when emitting Markdown/JSON/XML.
- **Machine pipeline ingestion:** JSONL (`--output codebase.jsonl`) + downstream filter on `type`/`representation`.

## Troubleshooting checklist
- Hybrid output must be `.jsonl`; if you see a format/extension error, fix `--output` or pass `--format`.
- If `--uses` fails, ensure `pyright-langserver` is installed and reachable; override with `--pyright-langserver-cmd`.
- If the pack fails on large files, adjust `--max-file-bytes` (or set to `0` to disable) and re-run.
