# anatomize

[![CI](https://github.com/BradSegal/anatomize/actions/workflows/ci.yml/badge.svg)](https://github.com/BradSegal/anatomize/actions/workflows/ci.yml)
[![Documentation](https://github.com/BradSegal/anatomize/actions/workflows/docs.yml/badge.svg)](https://bradsegal.github.io/anatomize/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Finding code is usually easier than deciding whether it can be changed safely.
A duplicate-looking function may preserve another contract, and a passing test
may describe only one selected run. Anatomize turns an exact repository state
into the compact evidence needed to make and review that decision:

```text
repository -> review session -> task dossier -> decision -> change -> fresh evidence -> closure
```

Observation and judgement remain separate. Anatomize records candidates,
omissions, and conflicts; an agent or person owns the resulting decision.
Source content is excluded unless the caller explicitly includes it.

## Install

Anatomize requires Python 3.10 or later.

```bash
python -m pip install anatomize
```

The optional local MCP server is the only extra:

```bash
python -m pip install 'anatomize[mcp]'
```

## Review a repository

From the repository to inspect:

```bash
mkdir -p .anatomy/review

anatomize review start . \
  --repository-id repository:example \
  --format json \
  --output .anatomy/review/session.json

anatomize review dossier .anatomy/review/session.json \
  --profile orientation \
  --format markdown \
  --output .anatomy/review/orientation.md

anatomize review dossier .anatomy/review/session.json src/package/core.py \
  --profile implementation \
  --target-kind file \
  --question 'What must remain true while simplifying this module?' \
  --format markdown \
  --output .anatomy/review/implementation.md
```

The session is content-addressed and portable. The orientation dossier gives a
small map of entry points, ownership, tests, documentation, configuration, and
data. The implementation dossier prioritises the selected target's definition,
consumers, tests, contracts, diagnostics, documentation, conflicts, and known
gaps. When a byte or item limit excludes material evidence, the dossier says so
and advertises a source-bound expansion.

Inspect conservative consolidation candidates separately:

```bash
anatomize review similarity .anatomy/review/session.json \
  --format json \
  --output .anatomy/review/similarity.json
```

A candidate is never an instruction to delete or merge code. Use `review
consolidate` to gather the behavioural, consumer, test, documentation, runtime,
contract, history, conflict, and unknown evidence needed for that decision.

## Complete the lifecycle

Before editing, `review intent` binds explicit obligations to the exact
before-state and implementation dossier. After editing, capture a new session
with the same repository identity, then use `review change` and `review verify`:

```bash
anatomize review start . \
  --repository-id repository:example \
  --format json \
  --output .anatomy/review/after.json

anatomize review change \
  .anatomy/review/session.json \
  .anatomy/review/after.json \
  --format markdown \
  --output .anatomy/review/change.md
```

Closure requires a fresh observation for every declared obligation. Anatomize
accounts for the evidence but never fabricates a test, audit, or human approval.

## Import results from tools you already use

`review start` always maps the repository itself. It can also include saved
results from tools you ran separately—for example, linter findings, test
results, coverage, or a dependency inventory:

```bash
anatomize review start . \
  --repository-id repository:example \
  --artifact sarif=artifacts/lint.sarif \
  --artifact junit=artifacts/junit.xml \
  --artifact coverage=artifacts/coverage.json \
  --artifact cyclonedx=artifacts/sbom.json \
  --format json \
  --output .anatomy/review/session.json
```

Supported file kinds are `sarif`, `lsp`, `junit`, `coverage`, `mutation`,
`jscpd`, `snakemake`, `targets`, `renv`, `cyclonedx`, and `ro-crate`. Use
`KIND@VERSION=PATH` when a native format needs an explicit version. A caller can
also pass a `ProviderEnvelope`: Anatomize's advanced, normalised format for
results from another tool. Anatomize does not run any of these tools, run
repository code, discover plugins, or use the network during review.

## Integrate an agent

Negotiate the machine surface rather than inferring it from the package
version:

```bash
anatomize review capabilities --format json
```

Use canonical JSON over the CLI for an independently released consumer. For a
local MCP host:

```bash
anatomize mcp /path/to/repository
```

The MCP projection is read-only and delegates to the same review application
as the CLI. It adds no second analysis path.

## Guarantees and limits

- Repository paths are normalised, relative, and contained after symlink
  resolution.
- Inputs use bounded, strict schemas; XML parsing rejects DTDs and entities.
- Artifacts bind repository identity, exact source state, producer evidence,
  configuration, and limitations.
- Independent evidence is merged without last-writer-wins; explicit
  contradictions between evidence sources become conflict records.
- Sessions omit source text by default. `--include-source` is exact and
  opt-in.
- Outputs are deterministic and human-readable; Markdown and plain text do not
  require ANSI or colour.
- The library does not execute code, prove semantic equivalence, approve a
  release, scan for secrets, or replace compilers, test runners, linters, or
  security scanners.

## Documentation

- [Documentation site](https://bradsegal.github.io/anatomize/): task-led paths
  for reviewers, agent integrators, and authors of external-tool integrations.
- [Quickstart](https://bradsegal.github.io/anatomize/QUICKSTART/): the complete
  design-to-closure workflow.
- [Lifecycle workflows](https://bradsegal.github.io/anatomize/WORKFLOWS/):
  design, audit, consolidation, implementation, and human hand-off.
- [Interpret review results](https://bradsegal.github.io/anatomize/CONCEPTS/): status, evidence groups,
  omissions, conflicts, consolidation decisions, and closure.
- [Import tool results](https://bradsegal.github.io/anatomize/PROVIDERS/): add
  linter, test, coverage, dependency, and research-workflow output.
- [Public reference](https://bradsegal.github.io/anatomize/REFERENCE/): CLI, Python namespaces, schemas, and
  supported formats.
- [Troubleshooting](https://bradsegal.github.io/anatomize/TROUBLESHOOTING/): recovery and visible failure
  states.

Development and release qualification are defined in
[CONTRIBUTING.md](CONTRIBUTING.md). Security and privacy boundaries are in
[SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
