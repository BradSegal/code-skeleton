# Import results from other tools

Anatomize maps source files, relationships, tests, documentation, and project
configuration by itself. It does not run your linter, test runner, coverage
tool, security scanner, dependency scanner, or research workflow. If you have
already run one of those tools, you can add its saved result to the review.

This lets a dossier connect a finding or test result to the relevant code while
keeping important limits visible. For example, an imported JUnit file describes
only the tests selected in that run, and an old coverage file must not be used
as evidence for a newer checkout.

Anatomize calls each source of imported evidence a **provider**. Most users do
not need to construct one: use `--artifact` with a supported result file. The
`ProviderEnvelope` described later on this page is for authors building a new
integration.

## Import a supported result file

Pass each file as `KIND=PATH`. Add `@VERSION` only when the format requires an
explicit version:

```bash
anatomize review start . \
  --repository-id repository:example \
  --artifact sarif@2.1.0=artifacts/lint.sarif \
  --artifact junit=artifacts/junit.xml \
  --test-selection tests/unit \
  --environment-digest sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --format json --output session.json
```

Anatomize checks that each result belongs to the same repository state before
combining its evidence with the repository map.

## Use the advanced integration format

If you are writing an adapter for a result format that Anatomize does not
support directly, normalise it to a `ProviderEnvelope` and pass that file with
`--provider`:

```bash
anatomize review start . \
  --provider artifacts/provider-envelope.json \
  --format json --output session.json
```

This route is intended for integration authors. It carries the tool name and
version, exact checkout, configuration, scope, completeness, limitations, and
normalised evidence in one validated file. Anatomize still does not execute the
external tool.

## Supported result formats

| Kind | Input | Evidence contributed |
| --- | --- | --- |
| `sarif` | SARIF 2.1.0 JSON | Diagnostics, rules, levels, suppressions, locations, and untrusted fix suggestions. |
| `lsp` | Captured Anatomize LSP semantic JSON | Compiler/language-server symbols, occurrences, relationships, and external targets. |
| `junit` | JUnit XML | Selected test outcomes and durations. |
| `coverage` | coverage.py JSON | File and line coverage observations within the declared run. |
| `mutation` | Mutation JSON | Mutant outcomes and locations within the declared run. |
| `jscpd` | jscpd JSON | Similar source regions and aligned evidence. |
| `snakemake` | Snakemake summary JSON | Workflow steps, inputs, outputs, and lineage. |
| `targets` | targets metadata JSON | R targets graph and optional data/resource bindings. |
| `renv` | `renv.lock` JSON | Exact R environment dependencies. |
| `cyclonedx` | CycloneDX JSON | Software components and dependency evidence. |
| `ro-crate` | RO-Crate JSON-LD | Research objects, provenance, and relationships. |

Python and R static test intent, R source, notebooks, executable documents, and
repository facts are collected directly from baseline-inventoried source
without executing it.

An imported result never establishes more than its own selection and method.
A passing JUnit suite does not prove unselected tests passed. Coverage does not
prove correctness. SARIF severity remains the producer's severity. A suggested
fix remains untrusted input.

## What the advanced provider format records

The rest of this page is API-level guidance for integration authors. The public
`anatomize.providers` namespace supplies `ProviderEnvelope`,
`ProviderBatchBuilder`, `ProviderScope`, `ProviderToolIdentity`,
`build_provider_envelope`, parsing/writing helpers, evidence normalisation, and
`run_provider_conformance`.

The envelope uses exact current versions:

| Field | Value |
| --- | --- |
| `artifact_type` | `anatomize.provider` |
| `schema_version` | `1.0.0` |
| `provider_api_version` | `1.0.0` |

It binds provider and underlying tool versions, capabilities, repository and
source states, configuration digest, payload digest, scope, invocation
authority, terminal status, normalised evidence, completeness, omissions, and
limitations. Unknown fields, duplicate IDs, dangling references, source-state
confusion, digest mismatches, and non-portable paths fail validation.

For example, a coverage file from the correct repository but an earlier source
state is not approximately current evidence. Validation rejects the state
mismatch so that a later closure report cannot silently use pre-change coverage
as proof of the changed implementation.

## Build a new integration

A provider producer should:

1. Accept an already-selected repository identity, source state,
   configuration, and bounded scope.
2. Run outside Anatomize under caller-controlled process, network, credential,
   and filesystem policy.
3. Normalize results into public `anatomize.evidence` records.
4. Preserve method, version, location, strength, stance, completeness,
   limitations, and omissions for every claim.
5. Seal the batch in a `ProviderEnvelope` and write canonical JSON atomically.
6. Pass `run_provider_conformance` and adverse parser tests.

Use content-derived stable identifiers. Put producer-native identities in
aliases rather than replacing canonical identity. Use `SourceCoordinateMap` to
convert zero/one-based UTF-8 byte, raw-byte, UTF-16, or Unicode-codepoint
positions. Never relabel provider offsets as canonical coordinates.

Independent structural observations over the same exact entity, edge, or
contract are retained. When one explicitly conflicts and another supports or
qualifies the target, evidence composition creates an unresolved
`ConflictRecord`; it does not pick a winner.

## Decide whether a new adapter belongs

Add a native adapter only when it contributes evidence that changes a named
design, audit, localisation, consolidation, or closure decision. Reuse SARIF,
LSP, JUnit, CycloneDX, RO-Crate, or another supported interchange before adding
a vendor-specific schema. Keep large raw logs and source content outside the
canonical envelope, referenced by digest where necessary.

A new adapter requires deterministic positive and negative fixtures, wrong
state and malformed input cases, byte/depth/count limits, privacy inspection,
clean baseline installation, and documentation of its decision value and
limitations. Anatomize intentionally has no provider execution broker or
plugin discovery layer.

Choose a native adapter when a maintained interchange already carries the
needed evidence. Choose `ProviderEnvelope` when an external tool's result needs
a new normalisation layer. Add another native format only when neither route
can represent evidence that materially changes a supported review decision.
