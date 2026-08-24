# Troubleshooting and recovery

Expected failures print a stable code, concise message, and remediation. If an
unexpected Python traceback is needed, reproduce through the Python API in a
controlled environment; CLI output intentionally avoids local-path-heavy
tracebacks.

## An artifact is old or from an unknown future version

Only exact schemas advertised by this release are accepted:

```bash
anatomize review capabilities --format json
```

There is no migration layer. Regenerate a session from source or rerun the tool
that created the rejected result file. Do not edit `schema_version`, IDs, or
digests.

## An artifact is stale

A stale artifact may be valid JSON but bound to another source state,
repository, provider input, or query. Capture a fresh session and rebuild the
derived dossier, similarity projection, or overlay evaluation. Keep an old
decision as history, but never present it as current.

## A session store is interrupted or corrupt

Validate a portable artifact without mutation:

```bash
anatomize review check artifact.json --format json
```

Recover the current or immediately prior valid immutable generation:

```bash
anatomize review recover .anatomy/review/store \
  --format json \
  --output recovered-session.json
```

If no generation validates, remove or archive only that derived store and run
`review start` again. The checkout and portable artifacts are authoritative;
the store is disposable acceleration.

## Results from another tool are unavailable

Anatomize can still map the repository without optional tools. Run the linter,
test runner, coverage tool, scanner, or workflow tool separately, save its
result in a supported format, and import that file explicitly:

```bash
anatomize review start . \
  --artifact sarif=artifacts/lint.sarif \
  --format json --output session.json
```

If the tool cannot run, retain an unavailable or partial status. Do not turn
absence into a complete empty result. In JSON, the tool and its imported result
are represented as a `provider`.

## A native artifact is rejected

Confirm the kind, native version, source state, byte size, and format. Parsers
bound bytes, depth, string length, value count, XML entities, coordinates, and
cross-references. Reacquire a valid complete artifact; do not truncate it while
claiming complete coverage.

## A target is unresolved or ambiguous

Inspect target kinds and use a narrower repository-relative locator:

```bash
anatomize review capabilities --format json
anatomize review dossier session.json src/package/core.py \
  --target-kind file --profile localisation
```

Prefer a path-qualified symbol over an unqualified name. An unresolved target
remains visible instead of falling back to whole-repository evidence.

## A dossier is partial or blocked

Read its required omissions, limitations, conflicts, unsatisfied stop
conditions, and advertised actions. Apply an exact expansion or adjust an
explicit budget:

```bash
anatomize review expand session.json exchange.json ACTION_ID \
  --max-items 64 --max-bytes 131072 \
  --format json --output expanded.json
```

If a required evidence category is genuinely unavailable, acquire that
evidence or record that the decision cannot yet close. Raising every budget is
not a substitute for a precise target.

## An expansion is rejected

Use an `action_id` from the exact base exchange and the exact session used to
create it. Actions are self-validating and cannot cross source states, queries,
profiles, or manifests.

## Providers disagree

Inspect the `ConflictRecord` and both source observations. Conflicts are not
ranked automatically. Resolve the decision through a consumer overlay or rerun
the producer with corrected inputs; preserve the original evidence for audit.

## A decision overlay is stale

Rebuild similarity evidence for the current session and run:

```bash
anatomize review overlay-check \
  decision.json current-session.json current-similarity.json CANDIDATE_ID \
  --format json
```

Review the stale reasons and create a new overlay if the decision is reaffirmed.
Never mutate the content-addressed old overlay.

## Closure is incomplete, discrepant, or stale

Use obligation IDs from the intent, the exact after-state ID from the current
session, and evidence references that exist in that session. Each obligation
needs the evidence kinds it declared. Run native checks again when their
provider digest is reused from the before-state. A missing check is incomplete;
a failed observation is a discrepancy; a different state is stale.

Do not remove an obligation merely to make closure pass.

## MCP will not start

Install the optional transport and use local stdio first:

```bash
python -m pip install 'anatomize[mcp]'
anatomize mcp /path/to/repository
```

Streamable HTTP binds only to loopback. Authentication, TLS, reverse proxies,
and hosted multi-user operation are outside this release.

## Output is unsafe to publish

Sessions omit source by default, but explicitly included source and imported
provider payloads may contain private material. Inspect artifacts before
publishing. Anatomize is not a secret scanner. Prefer Markdown export for human
review and canonical JSON for a trusted machine boundary.
