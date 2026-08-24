# Review a repository with focused evidence

Anatomize helps agents and people inspect an unfamiliar repository, localise a
change, review duplication, and verify the result. It produces compact Markdown
for review and stable, versioned JSON for automation.

<div class="anatomize-hero" markdown>

```bash
python -m pip install anatomize
anatomize review start . --format json --output review/session.json
anatomize review dossier review/session.json \
  --profile orientation --format markdown --output review/orientation.md
```

[Run the complete quickstart](QUICKSTART.md){ .md-button .md-button--primary }
[Browse common workflows](WORKFLOWS.md){ .md-button }

</div>

## Choose what you need to do

<div class="anatomize-paths" markdown>

<div class="anatomize-path" markdown>

### Review a repository

Capture the current checkout, find its entry points, and create a focused
dossier for a file, symbol, test, document section, workflow, or diagnostic.

[Start the review](QUICKSTART.md)

</div>

<div class="anatomize-path" markdown>

### Integrate an agent

Use versioned JSON through the CLI, the high-level Python API, or the optional
read-only MCP server. Expand partial results without repeating the review.

[Integrate automation](AUTOMATION.md)

</div>

<div class="anatomize-path" markdown>

### Import results from another tool

Add saved SARIF, JUnit, coverage, mutation, duplication, workflow, dependency,
or research results. Authors of new integrations can use the advanced provider
format.

[Import tool results](PROVIDERS.md)

</div>

</div>

## Complete a review

1. Run `review start` once for the checkout.
2. Use `review dossier` with the profile and target that match the task.
3. Inspect the status, conflicts, omissions, and limitations before editing.
4. Use `review similarity` and `review consolidate` when assessing duplicate
   code, tests, or documentation.
5. Record implementation obligations with `review intent`.
6. Capture the changed checkout and run `review change` and `review verify`.
7. Attach the focused Markdown dossier and closure report to the code review.

The [lifecycle guide](WORKFLOWS.md) provides the commands for each step. The
[artifact gallery](ARTIFACTS.md) explains which outputs are intended for people
and which are intended for machines.

## Read a result correctly

- `complete` means the required evidence was available within the requested
  scope.
- `partial` identifies useful evidence and the material omissions that remain.
- `blocked` means the current session cannot answer the question defensibly.
- A similarity candidate is something to review, not an instruction to merge.
- Closure requires current evidence for every declared obligation.

Use [interpret review results](CONCEPTS.md) for the complete reading order and
[troubleshooting](TROUBLESHOOTING.md) for stale, incompatible, partial, or
unavailable evidence.

## Keep the review boundary explicit

Anatomize does not execute repository code or approve changes. Run the
repository's own tests, linters, documentation checks, security tools, and
research validations, then import their saved results where they help the review.
Source text is excluded from sessions unless explicitly requested.

For exact commands, schemas, and public Python names, use the
[public reference](REFERENCE.md).
