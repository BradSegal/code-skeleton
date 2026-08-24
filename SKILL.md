---
name: anatomize
description: Use the Anatomize CLI to map an unfamiliar repository, gather focused context for a code change, inspect duplicate code, tests, or documentation, and compare and verify before-and-after states. Use when an agent needs structured repository evidence without reading the whole tree; do not use it to edit code or replace the repository's own tests and analysis tools.
---

# Anatomize

Use Anatomize to find the small part of a repository that matters to the current
task. It creates structured JSON for agents and focused Markdown for people.

Anatomize reads repository structure and supplied tool reports. It does not run
project code, edit files, decide that similar code is redundant, or approve a
change.

## Choose the workflow

| Need | Start with |
| --- | --- |
| Understand an unfamiliar repository | `review start`, then an `orientation` dossier |
| Find what a file, symbol, test, or document affects | An `implementation` or `localisation` dossier for that target |
| Audit the repository | An `audit` dossier |
| Investigate duplicate code, tests, or documentation | `review similarity`, then `review consolidate` |
| Review what changed | Capture before and after sessions, then use `review change` |
| Check that a completed change met its requirements | Record `review intent` before editing and use `review verify` afterwards |

For a small review, the first two commands are usually enough.

## Run the minimal review

First check which operations the installed version supports:

```bash
anatomize review capabilities --format json
```

Capture the current checkout. Keep generated review files outside the source
tree unless the repository has a declared artifact directory:

```bash
anatomize review start /path/to/repository \
  --repository-id repository:project \
  --format json \
  --output /tmp/anatomize-session.json
```

For an unfamiliar repository, request an overview:

```bash
anatomize review dossier /tmp/anatomize-session.json \
  --profile orientation \
  --format markdown \
  --output /tmp/anatomize-orientation.md
```

For a specific change, request focused implementation context:

```bash
anatomize review dossier /tmp/anatomize-session.json src/package/core.py \
  --target-kind file \
  --profile implementation \
  --question 'What depends on this file, and what must be checked if it changes?' \
  --format json \
  --output /tmp/anatomize-implementation.json
```

Use the narrowest useful target. Prefer a file or qualified symbol over a
repository-wide request.

## Read the result before opening more files

Read the output in this order:

1. **Status**: `complete`, `partial`, or `blocked`.
2. **Question and targets**: confirm that the result answers the intended task.
3. **Grouped evidence**: definitions, callers, dependencies, tests,
   documentation, diagnostics, and other relevant items.
4. **Conflicts**: reports that disagree.
5. **Omissions and limitations**: evidence that is missing or cannot be
   established by the available methods.
6. **Actions**: bounded ways to retrieve more context.

`partial` is a usable but incomplete result. Do not silently treat it as
complete. If an expansion action is offered, use its exact `action_id` with the
same session and dossier. Otherwise acquire the missing evidence or report that
the decision remains unresolved.

## Review possible duplication

Find candidates:

```bash
anatomize review similarity /tmp/anatomize-session.json \
  --format json \
  --output /tmp/anatomize-similarity.json
```

For a candidate that could materially change the implementation, build a
consolidation report:

```bash
anatomize review consolidate \
  /tmp/anatomize-implementation.json \
  /tmp/anatomize-similarity.json \
  CANDIDATE_ID \
  --format markdown \
  --output /tmp/anatomize-consolidation.md
```

Do not merge from a similarity score alone. Check behavioural differences,
callers, tests, documentation, contracts, runtime results, conflicts, and
unknowns. Record a `merge`, `keep`, or `postpone` decision with
`review overlay-create` when the rationale needs to be retained.

## Verify an implemented change

For consequential work:

1. create an implementation dossier;
2. record the requirements that must survive with `review intent`;
3. edit the repository and run its native tests, linters, documentation checks,
   security tools, and research validations;
4. capture a new session with the same `--repository-id`;
5. use `review change` to compare the sessions; and
6. use `review verify` with current observations for every requirement.

Use `anatomize review COMMAND --help` or the
[complete quickstart](https://bradsegal.github.io/anatomize/QUICKSTART/) for
the exact intent and verification input formats.

## Return a useful handoff

Report:

- the repository state that was reviewed;
- the focused dossier or consolidation report;
- the decision and rationale, when one was made;
- the native checks that were run;
- the before-and-after change report for implemented work;
- the closure status; and
- any remaining conflicts, omissions, or limitations.

Use JSON between tools. Export Markdown for code review. The agent or person
still owns the edit, interpretation, approval, and release decision.
