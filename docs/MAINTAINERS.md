# Maintainer guide

The documentation site is a release projection of the same public contracts as
the Python API, CLI, MCP server, and package metadata. It must remain useful to
a first-time reader, deterministic for review, and incapable of silently
publishing private programme material.

## Local setup

Install the documentation environment from the checked lock:

```bash
uv sync --locked --extra docs
```

Generate contract-derived references after changing capabilities, public
exports, or artifact models:

```bash
uv run python scripts/generate_documentation.py
```

Preview with live reload:

```bash
uv run mkdocs serve
```

The preview URL is printed by MkDocs. The generated pages and schemas are
tracked so public-contract changes remain visible in code review. Do not edit
files under `docs/generated/` by hand.

## Required documentation check

Run the exact release projection:

```bash
uv run python scripts/generate_documentation.py --check
uv run mkdocs build --strict --site-dir site
uv run python scripts/verify_documentation_site.py \
  --root . --site-dir site --output site-verification.json
```

Strict mode fails broken links, missing navigation, and MkDocs warnings. The
artifact inspector rejects symlinks, undeclared source or built file types,
private/local path markers, remote scripts, excessive size, missing search and
schema artifacts, and absent accessibility controls. The `site/` directory and
verification report are derived outputs and are not committed.

Also run the general checks in the repository
[contributing guide](https://github.com/BradSegal/anatomize/blob/main/CONTRIBUTING.md).

## Information architecture rules

- Keep the home page task-led: repository review, agent integration, and
  provider construction.
- Put concepts and interpretation in `CONCEPTS.md`; do not repeat them across
  command pages.
- Put exact hand-authored CLI behaviour in `REFERENCE.md` and generate dynamic
  capability, export, and schema inventories from code.
- Describe one canonical lifecycle. A new interface must project the same
  application rather than introduce a parallel workflow.
- Prefer small executable examples and link to a deeper page for nuance.
- State responsibility boundaries and limitations beside the feature they
  constrain.
- Preserve descriptive link text, heading order, keyboard focus, reduced
  motion, readable contrast, and rendering that does not depend on colour.

## GitHub Pages delivery

`.github/workflows/docs.yml` builds the strict site on relevant pull requests
and pushes. It uploads one inspected `site/` artifact. Deployment runs only
from the default `main` branch through GitHub's `github-pages` environment with
`pages: write` and `id-token: write`; the build job has read-only repository
permission and uses no repository secret.

One repository setting is required after the workflow is merged:

1. open **Settings → Pages** in GitHub;
2. set **Build and deployment → Source** to **GitHub Actions**; and
3. run the Documentation workflow or push a documentation change to `main`.

The canonical site will be `https://bradsegal.github.io/anatomize/`. Configure
environment protection in GitHub if deployment requires a human approval. Do
not use a committed `gh-pages` branch or a personal access token.

## Review and release checklist

For every public contract change, review all affected projections:

- Python exports and generated API inventory;
- runtime capabilities and generated capability table;
- Pydantic model and generated JSON Schema;
- CLI help and hand-authored option semantics;
- MCP parity;
- task-led guide and runnable example;
- package metadata and hosted documentation link; and
- wheel, source distribution, and public site privacy boundaries.

A site deployment is not a software release and a software release is not a
site qualification. Both must pass from the same commit before sign-off.
