# Contributing

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[dev]'
```

The checked-in `uv.lock` is the reproducible development resolution. When `uv`
is available, verify it with `uv lock --check`; ordinary consumers install from
package metadata and do not require uv.

## Architecture rules

- Add facts to the canonical evidence path; do not create a second repository
  graph or query engine.
- Keep acquisition, evidence, sessions, dossier selection, decisions, and
  presentation in their owning packages; do not make a lower-level package
  call the review application.
- Reuse shared path, identity, serialization, import, and error contracts.
- Integrate external analysis tools through an existing interchange before adding a
  vendor-specific adapter. Never add implicit tool execution or discovery.
- Preserve provider provenance, conflicts, completeness, omissions, and
  limitations. Candidates must never encode an edit or deletion verdict.
- Current artifact schemas are exact. Change the owning schema and regenerate
  fixtures instead of adding migration or speculative compatibility code.

## Local qualification

Run the complete baseline before requesting review:

```bash
python -m ruff check .
python -m mypy --strict src tests
python -m pytest
python scripts/benchmark_dossiers.py
python scripts/evaluate_agentic_workflows.py
python scripts/verify_documentation.py --root .
python scripts/generate_documentation.py --check
python -m mkdocs build --strict --site-dir site
python scripts/verify_documentation_site.py --root . --site-dir site
python scripts/verify_consumer_install.py --root .
python -m build
python -m twine check --strict dist/*
check-wheel-contents dist/*.whl
```

Install `.[dev,docs]` when running the complete baseline without `uv`. Also run
`uv lock --check` when dependency metadata changes. The executable-documentation
and consumer scripts build in temporary foreign working directories; they do
not rely on an editable checkout. See the
[maintainer guide](docs/MAINTAINERS.md) for local site preview, generated public
contracts, GitHub Pages delivery, and the documentation review checklist.

## Evidence changes

A fact or adapter change needs known-truth positive and adverse fixtures for
its actual boundary: malformed and over-limit input, wrong repository/state,
ambiguous identity, non-ASCII coordinates, unavailable or partial coverage,
and deterministic ordering where relevant. A real producer fixture is required
when a hand-built sample could miss native format behavior.

A dossier change needs a realistic target proving definition, direct consumer,
test, documentation, conflict/unknown handling, budget behavior, and human
rendering. A temporal change needs independently built before/after evidence;
do not hand-construct the expected delta and then merely validate it.

## Pull-request handoff

Keep changes reviewable and delete superseded code, tests, and documentation in
the same change. Report:

1. the outcome and owning boundary;
2. affected public schemas or capability values;
3. exact checks and dogfood workflows run;
4. measured performance or artifact-size effects when relevant; and
5. unresolved limitations or release blockers.

Release qualification additionally inspects wheel and sdist contents, package
metadata, dependency boundaries, citation/version coherence, licensing,
private-path and ticket leakage, source-content defaults, hostile input, MCP
parity, and clean installation on supported Python versions.
