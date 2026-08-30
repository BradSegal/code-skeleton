# Public reference

Command help is authoritative for option spelling. `review capabilities` is
authoritative for machine negotiation. Only the namespaces and commands named
here are public contracts.

## Installation and process surface

- Python 3.10 or later.
- Baseline: `pip install anatomize`.
- Optional MCP: `pip install 'anatomize[mcp]'`.
- Executable: `anatomize`.
- Root commands: `review` and `mcp`.
- Version: `anatomize --version`.

There is no repository configuration file, implicit plugin discovery, network
acquisition, or environment-based provider command. Roots, artifacts, included
source, budgets, and output paths are explicit arguments.

## Review commands

| Command | Stable purpose |
| --- | --- |
| `review capabilities` | Advertise operations, profiles, target kinds, schemas, pagination, output, and provider policy. |
| `review state ROOT` | Fingerprint the exact review source without building semantic evidence. |
| `review start ROOT` | Build one exact portable session from baseline facts and explicitly supplied artifacts. |
| `review dossier SESSION [TARGETS...]` | Build a bounded role-labelled answer for one lifecycle profile and question. |
| `review expand SESSION EXCHANGE ACTION` | Apply one source-bound advertised expansion without mutating the base exchange. |
| `review similarity SESSION` | Project conservative implementation, test, and documentation candidates. |
| `review change BEFORE AFTER` | Compare two exact states of one repository. |
| `review consolidate EXCHANGE SIMILARITY CANDIDATE` | Gather the evidence needed to decide whether one candidate should be merged. |
| `review overlay-create SESSION SIMILARITY CANDIDATE` | Record a portable decision, owner, and rationale without changing the evidence. |
| `review overlay-check OVERLAY SESSION SIMILARITY CANDIDATE` | Evaluate decision currency against relevant current evidence. |
| `review intent SESSION EXCHANGE OBLIGATIONS` | Bind explicit implementation obligations to the before-state. |
| `review verify INTENT AFTER OBSERVATIONS` | Account for every obligation against fresh after-state evidence. |
| `review export ARTIFACT` | Validate and render a supported artifact as JSON, plain text, or Markdown. |
| `review check ARTIFACT` | Validate schema, identity, digest, state, and references without mutation. |
| `review recover STORE` | Render the current or immediately prior valid immutable session generation. |

Machine operation names are `capabilities`, `source_state`, `start`, `dossier`, `expand`,
`similarity`, `change`, `consolidation`, `decision_overlay`,
`evaluate_overlay`, `implementation_intent`, `verify`, `check`, `export`, and
`recover`.

Profiles are `orientation`, `design`, `audit`, `localisation`,
`implementation`, `change_review`, and `closure`.

Target kinds are `repository`, `file`, `symbol`, `range`,
`documentation_section`, `test`, `configuration`, `data`, `workflow`,
`artifact`, `diagnostic`, `duplicate_candidate`, `revision`, and
`external_dependency`.

`--format` accepts `text`, `json`, or `markdown` where advertised. `--width`
has a minimum of 40. `--output` writes only after the artifact and rendering
validate.

Orientation and repository-wide `design`, `audit`, `change_review`, and
`closure` requests may omit positional targets; the application binds the
repository entity explicitly. `localisation` and `implementation` require an
exact target.

## Review start inputs

`review start` accepts:

- repeated `--provider PATH` values containing current `ProviderEnvelope`
  JSON;
- repeated `--artifact KIND[@VERSION]=PATH` native artifacts;
- repeated exact `--include-source PATH` values;
- repeated `--test-selection` values and an optional SHA-256
  `--environment-digest` for runtime evidence; and
- optional `--store PATH` for immutable generations and recovery.

Artifact kinds are `sarif`, `lsp`, `junit`, `coverage`, `mutation`, `jscpd`,
`snakemake`, `targets`, `renv`, `cyclonedx`, and `ro-crate`.

## Python API

The stable high-level API is in `anatomize.review`:

```python
from pathlib import Path

from anatomize.dossiers import DossierProfile
from anatomize.review import ReviewApplication, ReviewOutputFormat, render_review

application = ReviewApplication()
session = application.start(Path("."), repository_id="repository:example")
dossier = application.dossier(session, profile=DossierProfile.ORIENTATION)
print(render_review(dossier, format=ReviewOutputFormat.TEXT))
```

Use the models' enum values and typed helper builders for non-trivial calls;
the CLI is simpler for an independently released agent. The package root
`anatomize` exports only `__version__`.

Lower-level public ownership is:

| Namespace | Ownership |
| --- | --- |
| `anatomize.index` | Deterministic baseline `RepositoryIndex` and `build_repository_index`. |
| `anatomize.evidence` | Canonical entities, edges, contracts, observations, candidates, conflicts, completeness, omissions, aliases, lineage, merge, parse, and write. |
| `anatomize.identity` | Portable identity keys, coordinate conversion, and cross-provider claim reconciliation. |
| `anatomize.providers` | Advanced contract for normalising saved results from another analysis tool. |
| `anatomize.sessions` | Portable session manifests/bundles and recoverable immutable stores. |
| `anatomize.dossiers` | Targets, profiles, roles, requests, budgets, deterministic selection, slicing, cursors, and expansion. |
| `anatomize.temporal` | Exact state manifests, evidence-wide comparisons, deltas, validation, and exact lineage. |
| `anatomize.lifecycle` | Similarity, consolidation, decision overlays, test evidence, implementation intent, change, and closure. |
| `anatomize.semantic` | Captured LSP semantic artifact model and normalisation. |
| `anatomize.diagnostics` | Bounded SARIF 2.1.0 model and normalisation. |
| `anatomize.research` | R, notebook, execution, workflow, environment, data, dependency, and provenance evidence. |
| `anatomize.review` | The stateless application shared by CLI and MCP, artifact gateway, rendering, and capability negotiation. |

Names absent from a namespace's `__all__` are implementation details.

## Current schemas

The [generated capability table](generated/capabilities.md) is derived from
`review capabilities`, and the [generated schema catalogue](generated/schemas.md)
contains downloadable JSON Schemas for the current operation artifacts. This
keeps exact capability names and versions reviewable without maintaining a
second hand-written inventory.

Provider envelopes, repository comparisons, dossier requests and cursors,
captured LSP semantics, test intent and runtime, R evidence, notebook evidence
and execution, research graphs, and review artifacts also use exact current
schemas. SARIF input is `2.1.0`.

There is no migration layer. Old, incomplete, malformed, and unknown future
artifacts fail with regeneration guidance. Never hand-edit an identity or
schema version.

## Errors and writes

All public domain failures derive from `AnatomizeError`, expose `code`,
`remediation`, and `exit_code`, and are also `ValueError` instances. CLI
failures print a concise code, message, and remedy without a traceback.

The ordinary success exit is 0; invalid invocation, input, schema, identity,
state, or artifact uses 2. A valid incomplete or blocked lifecycle result may
use 3, and a stale consumer decision may use 4. Consumers should classify the
structured error or exit code, not parse prose.

Review writes are limited to requested `--output` artifacts and `review start
--store`. No operation edits repository source. Store generations are immutable
and the current pointer is replaced atomically under an advisory lock.

## MCP

`anatomize mcp [ROOT]` serves the same review application. Default `stdio` is
appropriate for a local host. `streamable-http` binds only to loopback and is
not a hosted-service security boundary. Server operators select provider
envelopes and included source paths explicitly. Inputs, outputs, and operation
duration are bounded. See command help for the exact transport limits.

## Output, privacy, and accessibility

Use canonical JSON for automation, Markdown for durable code review, and plain
text for terminals. Human rendering uses labels as well as order, needs no
colour or ANSI, and preserves conflicts, omissions, limitations, and recovery
actions.

Sessions omit source text by default. `--include-source` is exact and opt-in.
Artifact encodings protect structure, not trust: repository text and provider
content remain untrusted and must never be executed as instructions.
