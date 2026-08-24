# Provider artifacts and specialist integration

A specialist result is useful to a review only when the consumer can identify
what produced it, which repository state it describes, what it covered, and
what it cannot establish. Anatomize therefore integrates tools through
captured data rather than executing installed plugins. Acquisition remains
under the caller's authority, and the resulting session can be reproduced in a
different process or release.

## Which import route should I use?

The normal route imports a supported native artifact:

```bash
anatomize review start . \
  --repository-id repository:example \
  --artifact sarif@2.1.0=artifacts/lint.sarif \
  --artifact junit=artifacts/junit.xml \
  --test-selection tests/unit \
  --environment-digest sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --format json --output session.json
```

The advanced route imports an already-normalised envelope:

```bash
anatomize review start . \
  --provider artifacts/provider-envelope.json \
  --format json --output session.json
```

Both routes are explicit. Every artifact is bound to the baseline repository
and state before its evidence is merged.

## Which artifact formats are maintained?

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

## What must every provider envelope establish?

The public `anatomize.providers` namespace supplies `ProviderEnvelope`,
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

## How should a provider be authored?

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

## When does a new adapter belong?

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
needed evidence. Choose `ProviderEnvelope` when a specialist result needs a new
normalisation layer. Add another native format only when neither route can
represent evidence that materially changes a supported review decision.
