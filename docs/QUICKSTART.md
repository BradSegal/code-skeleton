# Quickstart: one complete agent review loop

This workflow starts with an ordinary checkout and finishes with evidence that
a human can review alongside a change. The example asks whether two helpers in
`src/package/core.py` can be consolidated without changing public behaviour.
Replace that path and question with the decision in your repository. Only the
baseline installation is required.

## 1. Negotiate and capture

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install anatomize

anatomize review capabilities --format json > review-capabilities.json
mkdir -p .anatomy/review
anatomize review start . \
  --repository-id repository:example \
  --format json \
  --output .anatomy/review/before.json \
  --store .anatomy/review/store
anatomize review check .anatomy/review/before.json --format json
```

An automation should check `application_api_version`, operations, profiles,
target kinds, and schema versions before relying on them. The session captures
one exact state and excludes source text by default.

## 2. Orient before reading broadly

```bash
anatomize review dossier .anatomy/review/before.json \
  --profile orientation \
  --format markdown \
  --output .anatomy/review/orientation.md
```

Read the status and evidence groups first. They identify public entry points,
ownership, dependencies, tests, documentation, workflows, configuration, data,
and any imported tool results without dumping the repository.

## 3. Localise the decision

```bash
anatomize review dossier .anatomy/review/before.json src/package/core.py \
  --profile implementation \
  --target-kind file \
  --question 'Can these helpers be consolidated without changing public behaviour?' \
  --format json \
  --output .anatomy/review/implementation.json
```

Use the narrowest defensible target: a `file`, `symbol`, `test`,
`documentation_section`, `configuration`, `workflow`, or other advertised
kind. Inspect `groups`, `conflicts`, `omissions`, and `limitations` before
opening source. A `partial` dossier is an honest bounded answer, not a failure
to hide.

When an exchange advertises an expansion, pass its exact `action_id` back with
the unchanged session and exchange:

```bash
anatomize review expand \
  .anatomy/review/before.json \
  .anatomy/review/implementation.json \
  ACTION_ID \
  --format json \
  --output .anatomy/review/expanded.json
```

An expansion is source-bound and non-mutating. It can enlarge a budget, follow
a relationship, or continue a page without silently widening the original
question.

## 4. Audit and consolidate deliberately

The targeted dossier explains the local evidence, but the consolidation
decision also depends on repository-wide diagnostics, conflicts, boundaries,
dependencies, and gaps. Project the duplicate candidates independently:

```bash
anatomize review dossier .anatomy/review/before.json \
  --profile audit \
  --format markdown \
  --output .anatomy/review/audit.md

anatomize review similarity .anatomy/review/before.json \
  --format json \
  --output .anatomy/review/similarity.json
```

If a candidate exists, collect the evidence needed to decide whether it should
be merged:

```bash
anatomize review consolidate \
  .anatomy/review/implementation.json \
  .anatomy/review/similarity.json \
  CANDIDATE_ID \
  --format markdown \
  --output .anatomy/review/consolidation.md
```

Inspect the consolidation dossier before choosing a disposition. A helper that
looks redundant may preserve different input handling or serve a distinct
caller. Record your decision separately from the observed similarity:

```bash
anatomize review overlay-create \
  .anatomy/review/before.json \
  .anatomy/review/similarity.json \
  CANDIDATE_ID \
  --disposition keep \
  --rationale 'The implementations preserve distinct public behaviour.' \
  --owner example-review \
  --format json \
  --output .anatomy/review/decision.json
```

`overlay-check` later determines whether the relevant evidence changed. It
does not rewrite an old decision to appear current.

## 5. Bind the implementation contract

Create `obligations.json`. The CLI assigns content-derived obligation IDs:

```json
{
  "obligations": [
    {
      "kind": "contract",
      "subject_ref": "src/package/core.py",
      "expectation": "The public result remains unchanged.",
      "required_evidence_kinds": ["test_runtime"]
    },
    {
      "kind": "documentation",
      "subject_ref": "README.md",
      "expectation": "The public example names the surviving API.",
      "required_evidence_kinds": ["documentation"]
    }
  ],
  "declared_unknowns": []
}
```

```bash
anatomize review intent \
  .anatomy/review/before.json \
  .anatomy/review/implementation.json \
  obligations.json \
  --format json \
  --output .anatomy/review/intent.json
```

Now edit. Run the repository's native tests, linters, documentation checks,
security tools, and research validations. Anatomize can import their saved
results; it does not execute or impersonate them.

## 6. Capture and compare the after-state

```bash
anatomize review start . \
  --repository-id repository:example \
  --artifact junit=artifacts/junit.xml \
  --format json \
  --output .anatomy/review/after.json

anatomize review change \
  .anatomy/review/before.json \
  .anatomy/review/after.json \
  --format markdown \
  --output .anatomy/review/change.md
```

The change dossier compares every canonical evidence family. It reports
additions, removals, modifications, and unique exact file moves without
inventing uncertain lineage.

## 7. Verify every obligation

Create `observations.json` from the IDs in `intent.json`. Each observation must
name the exact after-state ID and evidence references present in the after
session:

```json
{
  "observations": [
    {
      "obligation_id": "COPY_FROM_INTENT",
      "source_state_id": "COPY_FROM_AFTER_SESSION",
      "status": "satisfied",
      "evidence_refs": ["COPY_AN_AFTER_EVIDENCE_REFERENCE"],
      "observed": "The declared after-state check passed.",
      "limitations": []
    }
  ]
}
```

```bash
anatomize review verify \
  .anatomy/review/intent.json \
  .anatomy/review/after.json \
  observations.json \
  --format markdown \
  --output .anatomy/review/closure.md
```

Closure is `closed` only when every obligation is satisfied with the required
fresh evidence kinds, all providers are complete, and no declared unknown
remains. Otherwise the report is `incomplete`, `discrepancy`, or `stale` and
identifies the work still needed.

## 8. Hand off to a person

```bash
anatomize review export .anatomy/review/implementation.json \
  --format markdown --output implementation-review.md
anatomize review export .anatomy/review/change.json \
  --format markdown --output change-review.md
anatomize review export .anatomy/review/closure.json \
  --format markdown --output closure-review.md
```

Attach the focused dossier, decision rationale, change dossier, native test
evidence, and closure report to the code review when they materially explain
the change. The machine artifact remains available for exact traceability; the
Markdown projection is optimized for human review.

The loop is complete when the reviewer can reconstruct why the candidate was
merged or retained, what the implementation was required to preserve, what
changed, and which current evidence supports closure. Use the
[conceptual model](CONCEPTS.md) to interpret any unfamiliar artifact and
[troubleshooting](TROUBLESHOOTING.md) when the evidence cannot yet close.
