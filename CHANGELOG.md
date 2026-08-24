# Changelog

All notable changes to this project are documented in this file.

## 2.0.1 - 2026-08-24

### Changed

- Explain the review model in reader language before introducing machine terms,
  and describe imported evidence as saved results from familiar tools.
- Make rendered dossiers and CLI help use task language while retaining stable
  JSON field names for automation.

### Fixed

- Make session-store lock typing portable across Windows and POSIX type
  environments.
- Interpret imported POSIX and Windows absolute paths consistently on every
  operating system, and read Unicode test fixtures explicitly as UTF-8.
- Force the documentation verifier to install the built wheel when its isolated
  environment can see an editable checkout.
- Use an available `setup-uv` action and deploy the documentation site through
  an enabled GitHub Pages environment.

## 2.0.0 - 2026-08-24

### Changed

- Replace the provisional index, skeleton, and pack products with one
  evidence-first `anatomize review` lifecycle shared by Python, CLI, and MCP.
- Converge repository acquisition, provider normalization, exact state,
  dossiers, comparison, consolidation, implementation intent, and closure on
  one canonical evidence graph and one error/path/serialization contract.
- Remove plugin discovery and provider execution. Optional evidence now enters
  only through explicit bounded native artifacts or provider envelopes.
- Remove superseded parsers, graphs, compatibility branches, commands, tests,
  and documentation. There is intentionally no migration layer; rebuild
  artifacts from source with the current release.

### Added

- Deterministic orientation, design, audit, localisation, implementation,
  change-review, and closure dossiers with proof roles, reasons, budgets,
  visible omissions, source-bound expansion, and human-readable rendering.
- Canonical entities for Python, R, notebooks, tests, documentation,
  configuration, workflows, data, artifacts, dependencies, diagnostics, and
  runtime evidence.
- Bounded adapters for captured LSP, SARIF 2.1.0, JUnit, coverage, mutation,
  jscpd, Snakemake, targets, renv, CycloneDX, and RO-Crate artifacts.
- Conservative implementation, test, and documentation candidates; complete
  consolidation-question dossiers; separate consumer decision overlays and
  currency checks.
- Evidence-wide before/after comparison, unique exact move detection,
  implementation obligations, and fresh-evidence closure accounting.
- Cross-provider conflict materialization without last-writer-wins, exact
  identity/coordinate reconciliation, content-free sessions, recoverable
  immutable stores with advisory locking, and a read-only bounded MCP server.
- A task-led, searchable GitHub Pages site with tracked generated capability,
  Python API, and JSON Schema references; strict link and navigation checks;
  public-artifact privacy and accessibility inspection; and least-privilege
  default-branch deployment.

### Assurance

- Add realistic mixed-language lifecycle, agent-workflow, adverse parser,
  Unicode, path, concurrency, recovery, MCP, packaging, clean-install,
  documentation, performance, and held-out comprehension qualification.
- Add software citation, security reporting, consolidated public documentation,
  and a shipped agent skill that uses the same review workflow.

## 1.1.0 - 2026-08-16

- Preserve symbol spans and definition-level changes across explicit Git bases.
- Retain multiple import, reference, test, documentation, and configuration reasons per impact node.
- Preserve baseline consumers for deleted and moved definitions.
- Add machine-readable capability discovery, optional explicit Pyright references, and bounded impact-derived review bundles.

## 1.0.0 - 2026-07-29

The validated `1.0.0rc1` implementation is promoted unchanged as the first
stable release.

## 1.0.0rc1 - 2026-07-29

### Added

- Portable repository indexes with exact Python source identity, definitions,
  public exports, and resolved static local imports.
- `find`, `impact`, and explicit-base `changed` commands for task-bounded agent
  context.
- `check` for configured skeleton, pack, and stored-index drift.
- Role and graph-distance provenance in pack selection reports.
- A valid Codex skill and agent metadata.

### Changed

- Pack requests are validated before discovery.
- File-size limits apply only to selected files.
- Pack outputs, split artifacts, reports, and temporary files are excluded from
  their own source boundary.
- Broken and forbidden symlinks are skipped deterministically.
- Persisted paths are portable and expected CLI errors are concise.
- Representation rules reuse the canonical glob matcher.

## 0.2.1 - 2026-02-01

### Added
- `anatomize pack --prefix standard|minimal` to control pack prefix verbosity (token overhead vs guidance).
- `anatomize pack --explain-selection` to write a deterministic selection report for debugging include/ignore/slice behavior.

### Changed
- Hybrid mode now supports markdown/plain output (JSONL is optional, and required only for `--fit-to-max-output`).
- Pack ignore rules now retain rule provenance (default vs standard ignore files vs CLI) for selection reporting.
- Binary sniffing reads only a small prefix for performance (instead of reading entire files).
- Dependency-closure failures now include an import chain for faster debugging.

### Infrastructure
- CI Pyright job avoids invoking commands that require an LSP transport without `--stdio`.

## 0.2.0 - 2026-02-01

### Added
- Multi-source skeleton workflows via `.anatomize.yaml` (per-source `level`, output subdirectories, and shared defaults).
- `anatomize init --preset standard` to scaffold the common pattern “src detailed, tests minimal”.
- Config-driven `anatomize generate|validate|estimate` (single command operates on all configured outputs).

### Changed
- `.anatomize.yaml` schema now uses `sources: [{path, output, level, ...}]` and writes into a root `output` directory.

## 0.1.0 - 2026-01-31

### Added
- `anatomize generate`: deterministic skeleton maps (hierarchy/modules/signatures) with YAML/JSON/Markdown outputs and embedded schemas.
- `anatomize validate`: strict validation of skeleton outputs with optional `--fix`.
- `anatomize estimate`: token estimation for skeleton outputs.
- `anatomize pack`: deterministic review bundles with include/ignore filtering, dependency slicing, compression, and token diagnostics.
- Pack output formats: Markdown, plain text, JSON, XML, and JSONL (stream-friendly).
- Pack safety/limits: `--content-encoding`, `--max-output`, and `--split-output`.
- Pack slicing: forward dependency closure (`--entry --deps`), reverse import closure (`--reverse-deps`), and optional Pyright-backed `--uses` slicing.
- Pack hybrid mode: JSONL bundles with per-file `meta|summary|content` representations and deterministic `--fit-to-max-output` selection tracing.

### Infrastructure
- CI for linting, typechecking, tests, builds, and optional Pyright e2e verification.
