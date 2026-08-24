# Lifecycle workflows

Choose the workflow from the decision that must be made, not from the amount of
repository context available. Every profile uses the same captured state. The
shortest useful path is `start -> dossier`; a change continues through intent,
an independently captured after-state, comparison, and closure.

## Orient and design

Start once for an exact checkout, then project multiple questions without
walking the repository again:

```bash
anatomize review start . \
  --repository-id repository:example \
  --format json --output review/before.json

anatomize review dossier review/before.json \
  --profile orientation \
  --format markdown --output review/orientation.md

anatomize review dossier review/before.json \
  --profile design \
  --question 'Where should repository cache ownership live?' \
  --format markdown --output review/design.md
```

The orientation dossier maps the public surfaces and topology. The design
dossier changes evidence priority toward boundaries, contracts, consumers,
alternatives, and unknowns. Neither creates a decision on the user's behalf.

## Audit without losing scope

Use a repository-wide audit to expose diagnostics, dependencies, conflicts,
assurance evidence, and omissions:

```bash
anatomize review dossier review/before.json \
  --profile audit \
  --format json --output review/audit.json
```

Read the dossier status before its items:

- `complete` means every required evidence category was available within the
  requested scope.
- `partial` means useful evidence exists but a budget, provider, or scope left
  material omissions.
- `blocked` means the question cannot be answered defensibly from the current
  session.

Apply only advertised expansions. An action is bound to the exact session,
query, profile, and base exchange, so a caller cannot accidentally continue a
different review.

## Localise an implementation

Target the narrowest stable entity that expresses the intended change:

```bash
anatomize review dossier review/before.json src/anatomize/review/application.py \
  --target-kind file \
  --profile implementation \
  --question 'How can this boundary be simplified without changing its public contract?' \
  --format markdown --output review/implementation.md
```

The result groups the target definition, inbound and outbound relationships,
consumers, tests, documentation, contracts, diagnostics, conflicts, and gaps
by purpose. This is the context to inspect before editing, not a source dump.

## Deduplicate and consolidate

Candidate discovery and the merge decision are intentionally separate:

```bash
anatomize review similarity review/before.json \
  --format json --output review/similarity.json

anatomize review consolidate \
  review/implementation.json \
  review/similarity.json \
  CANDIDATE_ID \
  --format markdown --output review/consolidation.md
```

The similarity artifact can contain implementation, test, and documentation
candidates. If two path-normalisation helpers match, a consolidation dossier
tests that resemblance against their behaviour, differences, consumers, tests,
documentation, runtime evidence, contracts, history, conflicts, and unknowns.
The reviewer then records `merge`, `keep`, or `postpone` with an owner and
rationale. The JSON decision record is called an overlay because it adds
judgement without changing the observed evidence. Imported jscpd regions can
improve discovery, but the decision still depends on behaviour, callers,
tests, documentation, contracts, history, conflicts, and remaining unknowns.

## Implement against explicit obligations

Bind what must remain true before changing source:

```bash
anatomize review intent \
  review/before.json \
  review/implementation.json \
  obligations.json \
  --format json --output review/intent.json
```

Obligations may cover public contracts, consumers, tests, documentation,
workflows, decisions, ownership, and declared unknowns. Run the repository's
native formatter, linter, tests, documentation builder, security checks, and
research validations after editing. Their bounded artifacts can then enter the
same review session as current implementation evidence.

## Compare and close

Capture the changed checkout independently with the same repository identity:

```bash
anatomize review start . \
  --repository-id repository:example \
  --artifact junit=artifacts/junit.xml \
  --format json --output review/after.json

anatomize review change review/before.json review/after.json \
  --format markdown --output review/change.md

anatomize review verify review/intent.json review/after.json observations.json \
  --format markdown --output review/closure.md
```

Closure requires a fresh observation for every obligation, bound to the exact
after-state and evidence references. The result closes only when required
evidence is present, providers are complete, and declared unknowns are resolved.
Otherwise it says precisely whether work is incomplete, discrepant, or stale.

## Human review hand-off

Keep canonical JSON for exact machine traceability. Export Markdown for the
smallest set that materially helps a code reviewer:

1. the targeted implementation or consolidation dossier;
2. the decision record (overlay) and rationale;
3. the evidence-wide change dossier;
4. relevant native test or analysis evidence; and
5. the closure report.

Do not attach every generated artifact by default. The hand-off is sufficient
when a reviewer can recover the decision, the evidence that supports it, the
obligations imposed on the change, and any limitation that could alter
approval—without reconstructing the repository review from scratch.
