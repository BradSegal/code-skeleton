# Interpret review results

Anatomize outputs are designed to be read in a fixed order. Start with whether
the question was answered, then inspect the selected evidence and anything that
could change the decision.

## Start with the status

Every dossier reports one of three states:

- `complete`: all required proof roles were available within the requested
  boundary;
- `partial`: the result is useful, but a budget, scope, or provider left a
  material omission; or
- `blocked`: the current session cannot support a defensible answer.

A successful command can still produce a `partial` result. Do not treat exit
code 0 or a non-empty dossier as proof that the evidence is complete.

## Confirm the question and target

A **review session** represents one captured repository state. A **dossier** is
a focused answer over that session. Check the question, profile, and targets
before using its contents: an implementation dossier for one file does not
claim repository-wide coverage.

Profiles change what the dossier prioritises:

| Profile | Use it when you need to |
| --- | --- |
| `orientation` | Find entry points, ownership, tests, documentation, workflows, configuration, and data |
| `design` | Review boundaries, contracts, alternatives, and consumers |
| `audit` | Inspect diagnostics, conflicts, dependencies, omissions, and assurance evidence |
| `localisation` | Find an exact target and its inbound and outbound relationships |
| `implementation` | Gather the consumers, tests, documentation, contracts, diagnostics, and unknowns needed before editing |
| `change_review` | Explain the effects of a before-and-after comparison |
| `closure` | Account for declared obligations and current evidence |

Use the narrowest target that expresses the task. If the result is too broad,
target a file, symbol, test, documentation section, workflow, data resource, or
diagnostic rather than increasing every budget.

## Read the evidence groups

Proof-role groups explain why an item was selected. Definitions and contracts
describe the target; consumers and relationships show what may be affected;
tests, documentation, diagnostics, and runtime observations show where the
change can be checked.

Each item retains its source location and selection reasons. Open source only
where the dossier identifies a decision-relevant definition, difference, or
gap rather than reading every referenced file.

## Inspect conflicts, omissions, and limitations

- A **conflict** preserves incompatible observations. Review both sources; no
  provider wins automatically.
- An **omission** records material evidence that was unavailable because of
  scope, budget, privacy, policy, or provider availability.
- A **limitation** states what a method cannot establish even when it completed
  successfully.
- **Completeness** applies only to the named provider, scope, state, and
  evidence family.

If the dossier advertises an expansion action, use its exact `action_id` with
the unchanged session and exchange. The expanded result widens that question
without altering the original dossier.

## Review duplication as a candidate

`review similarity` finds conservative implementation, test, and documentation
candidates. Similarity alone does not establish that two items have the same
behaviour or purpose.

Use `review consolidate` to gather the candidate's differences, consumers,
tests, documentation, runtime evidence, contracts, history, conflicts, and
unknowns. Record the resulting `merge`, `keep`, or `postpone` decision with
`review overlay-create`. The overlay keeps the owner and rationale separate
from the observed similarity and can later be checked for staleness.

## Verify a change against explicit obligations

Before editing, `review intent` records what must remain true. After editing:

1. run the repository's native checks;
2. capture a new session with the same repository identity;
3. compare it with `review change`; and
4. supply current observations to `review verify`.

Closure is `closed` only when every obligation has the required current
evidence and no declared unknown remains. Otherwise the report identifies an
`incomplete`, `discrepancy`, or `stale` result and the work required to recover.

## Choose the right output

Use canonical JSON between tools and releases. Use Markdown for code review,
design records, and other human hand-offs. Use plain text for terminals and
logs, but do not parse it as an interchange format.

You are ready to act when you can state the exact question, the evidence that
supports the decision, the evidence that is absent or conflicting, and the
current observations required for closure.
