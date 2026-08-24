# Agent and automation integration

An agent needs two different capabilities: reproducible observation and
qualitative judgement. Anatomize supplies the first. The reliable integration
loop negotiates the machine contract, captures one state, asks a bounded
question, and exposes missing evidence before the agent decides. After editing,
it reacquires the repository and verifies the obligations created by that
decision.

## Negotiate before use

```bash
anatomize review capabilities --format json
```

Check `application`, `application_api_version`, the required operation and
profile, target kinds, schema versions, and interaction policy. Do not infer
capabilities from the package version or scrape command help.

The generated [capability reference](generated/capabilities.md) is convenient
for people; the runtime result is authoritative for automation.

## Prefer the CLI boundary between releases

For an independently released agent, invoke the CLI with canonical JSON,
bounded duration and output, and an explicit working directory. Write outputs
to a dedicated derived-artifact directory rather than source paths.

A minimal loop is:

```bash
anatomize review start /path/to/repository \
  --repository-id repository:example \
  --format json --output review/session.json

anatomize review dossier review/session.json src/package/core.py \
  --target-kind file --profile implementation \
  --format json --output review/dossier.json
```

Consume structured status, groups, conflicts, omissions, limitations, and
actions. Never infer completeness from exit code 0 alone, discard a conflict,
or widen a blocked query silently.

## Use Python inside one trusted process

The high-level public boundary is `ReviewApplication`:

```python
from pathlib import Path

from anatomize.dossiers import DossierProfile
from anatomize.review import ReviewApplication

application = ReviewApplication()
capabilities = application.capabilities()
session = application.start(
    Path("/path/to/repository"),
    repository_id="repository:example",
)
exchange = application.dossier(session, profile=DossierProfile.ORIENTATION)

print(capabilities["application_api_version"])
print(exchange.dossier.status.value)
```

Use the lower-level public namespaces only when constructing typed inputs or
integrating evidence. The [generated Python API inventory](generated/python-api.md)
lists the exact names in each namespace's `__all__`.

## Use MCP for local tool hosts

Install the transport extra and start the read-only server:

```bash
python -m pip install 'anatomize[mcp]'
anatomize mcp /path/to/repository
```

The default stdio transport is the safest integration for a local agent host.
MCP delegates to the same `ReviewApplication` and adds no second analysis path.
Streamable HTTP binds only to loopback; it is not a hosted multi-user security
boundary. The server operator still controls supplied provider artifacts and
source inclusion.

## Control context growth

Start with orientation, then ask one targeted question. Prefer a stable file,
symbol, test, documentation section, workflow, data item, or diagnostic over a
repository-wide implementation request. Dossiers select required proof roles
before optional context and preserve every reason an item was selected.

If a result is partial, choose an advertised action and preserve its opaque
`action_id`. Actions support bounded budget increases, relationship traversal,
and pagination without silently changing the base question. Cache the immutable
session, not an unversioned prose summary.

## Keep decisions separate from observations

An automation may propose a design or consolidation decision, but it should
write that judgement to a decision overlay with an owner, rationale, evidence
digest, and review conditions. For example, two similar path helpers remain a
candidate until their consumers, contracts, tests, and differences have been
examined. The overlay records the resulting judgement without rewriting the
source observation.

Before editing, create an implementation intent. After editing:

1. run native repository assurance under the caller's authority;
2. import bounded artifacts such as SARIF, JUnit, coverage, mutation, or
   CycloneDX where they add decision evidence;
3. capture a fresh after-state under the same repository identity;
4. compare the states; and
5. verify each obligation with exact current evidence references.

Fresh evidence for each obligation provides a machine-checkable stopping
condition and a compact human handoff without treating agent completion as
approval.

## Failure policy

Public domain errors expose a stable code, remedy, and exit class. Treat exit 2
as invalid input or artifact, 3 as a valid but incomplete or blocked lifecycle
result, and 4 as stale consumer judgement. Parse structured errors or artifacts,
not prose. Retry only after applying the supplied remediation or obtaining new
evidence.

An integration is complete when the agent can negotiate the contract, keep
context growth deliberate, preserve the distinction between evidence and
judgement, and return a compact closure artifact that a person can review
without trusting the agent's hidden state.
