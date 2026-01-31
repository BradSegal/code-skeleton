# Repomix vs anatomize (local comparison)

### Versions
- `repomix`: 1.11.1
- `anatomize`: 0.1.0
- `tiktoken`: 0.12.0

### Method
Generated four “single-file bundle” artifacts for this repository root:
- repomix: full + `--compress`
- anatomize: `pack` full + `--compress`

Token counts were computed by reading the output files and counting with the same encoding:
- `o200k_base` (used for all measurements below)

Repomix was invoked with `--no-git-sort-by-changes` to avoid git-history-dependent ordering.

### Commands
```
# repomix (full)
repomix . --style markdown --output repomix.full.md --token-count-encoding o200k_base --no-git-sort-by-changes

# repomix (compress)
repomix . --style markdown --compress --output repomix.compress.md --token-count-encoding o200k_base --no-git-sort-by-changes

# anatomize (full)
anatomize pack . --format markdown --output anatomize.full.md --token-encoding o200k_base

# anatomize (compress)
anatomize pack . --format markdown --output anatomize.compress.md --token-encoding o200k_base --compress
```

### Results
| Artifact | Bytes | Tokens (`o200k_base`) |
|---|---:|---:|
| `repomix.full.md` | 230,277 | 50,617 |
| `repomix.compress.md` | 124,143 | 29,970 |
| `anatomize.full.md` | 227,186 | 50,578 |
| `anatomize.compress.md` | 75,771 | 18,640 |

### Notes
- The “full” outputs are similar in size and tokens on this repository.
- `anatomize pack --compress` is substantially smaller here because it emits a stricter Python-only structural summary (signatures + constants + imports) rather than a broader “essential code” extraction. This trades context for token savings.
- For slicing specific subsystems, `anatomize pack --entry ... --deps` can reduce output further by selecting only a dependency closure.

---

## Feature parity and ergonomics

### What `anatomize pack` matches (or extends)
- **Filtering**: `--include`, `--ignore`, `--ignore-file`, and `--respect-standard-ignores` provide repomix-style file selection.
- **Determinism**: stable ordering and formatting without git-history-dependent sorting.
- **Hard limits**: `--max-output` and `--split-output` (bytes or tokens, e.g. `500kb`, `20_000t`) provide strict caps.
- **Slicing**:
  - forward dependency closure: `--entry ... --deps`
  - reverse dependency closure (importers): `--target/--module --reverse-deps`
  - reference-based slicing: `--uses --slice-backend pyright`
- **Formats**: markdown/plain/json/xml plus **JSONL** (`--format jsonl`) for stream-friendly tooling.
- **Hybrid mode**: `--mode hybrid` emits summaries + selective fill for token-efficient external review.

### What differs vs repomix
- **Compression semantics**: `anatomize pack --compress` is Python-structural (very token-efficient) and intentionally trades off full bodies; repomix compression aims to keep more “essential code” context across languages.
- **Dependency knowledge**: repomix is primarily a bundler; `anatomize` adds explicit dependency slicing for Python.
- **Hybrid mode**: repomix does not have a first-class “summary everywhere, fill specific files” representation policy; `anatomize` uses this to reduce tokens while keeping navigability.

---

## Dependency tracing vs language-server slicing

### Import-graph slicing (`--deps` / `--reverse-deps`)
- Fast, deterministic, and robust for “module-level” boundaries.
- Fails hard if a required local import cannot be resolved (complete-or-fail).
- Best for bundling a subsystem entrypoint and everything it imports.

### Language-server slicing (`--uses --slice-backend pyright`)
- Higher precision for “what files reference these symbols”, including dynamic references that an import-only graph cannot capture.
- More operational complexity: requires a working Pyright language server and has higher runtime cost.
- Best for answering “where is this API used” and packaging those usage sites for external review.

### Practical ergonomics: “bundle a module and all code that imports it”
Yes:
- **Importers only**: `anatomize pack . --target path/to/mod.py --reverse-deps --output importers.md`
- **Importers plus what they import**: `anatomize pack . --target path/to/mod.py --reverse-deps --deps --output slice.md`
- **Reference-based usage**: `anatomize pack . --target path/to/mod.py --uses --slice-backend pyright --output uses.md`

---

## Token-efficiency vs expressivity

- **Raw extraction** (full contents) maximizes context but costs tokens.
- **Compression** reduces tokens aggressively but may reduce “local reasoning context” (bodies, invariants, detailed logic).
- **Slicing** often provides the best trade-off: include only the relevant closure (imports or symbol references), optionally with compression for further reduction.

For external review flows, a common pattern is:
1) generate a skeleton at `signatures` for navigation,
2) pick an entrypoint/target,
3) `pack` a sliced bundle that fits the token budget via `--split-output`/`--max-output`.
