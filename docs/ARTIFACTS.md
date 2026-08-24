# Artifact gallery

Every Anatomize output has one owner and one lifecycle purpose. Canonical JSON
is the interchange contract; Markdown and text are projections from the same
validated object.

| Artifact | Produced by | Use it for | Usually review as |
| --- | --- | --- | --- |
| Capability document | `review capabilities` | Negotiate operations, profiles, target kinds, schemas, and interaction rules | JSON |
| Review session | `review start` | Reuse one exact evidence state across questions | JSON |
| Dossier exchange | `review dossier` / `expand` | Answer one bounded orientation, design, audit, localisation, implementation, change-review, or closure question | Markdown plus JSON when traceability matters |
| Similarity artifact | `review similarity` | Discover conservative implementation, test, and documentation candidates | JSON |
| Consolidation dossier | `review consolidate` | Decide whether one possible duplicate should be merged, kept, or investigated further | Markdown |
| Decision record (overlay) | `review overlay-create` | Preserve your disposition, owner, rationale, and review conditions separately from observed evidence | JSON or Markdown |
| Overlay evaluation | `review overlay-check` | Determine whether relevant evidence changed after a decision | Markdown |
| Implementation intent | `review intent` | Bind explicit obligations to the exact before-state | JSON |
| Change dossier | `review change` | Compare canonical evidence across independent states | Markdown plus JSON |
| Closure report | `review verify` | Account for every obligation against fresh evidence | Markdown plus JSON |
| Artifact check | `review check` | Validate schema, digest, identity, state, and references without mutation | JSON |

## Reading a dossier

Read in this order:

1. **Status** — whether the evidence is complete, partial, or blocked.
2. **Question and targets** — the exact decision boundary the dossier answers.
3. **Evidence groups** — why each fact matters, not merely where it was found.
4. **Conflicts** — incompatible source-bound claims preserved without ranking.
5. **Omissions and limitations** — absent evidence and what the methods cannot
   establish.
6. **Expansion actions** — exact, bounded ways to obtain more evidence.

The selection reasons are part of the output contract. They make a compact
context human-reviewable and help an agent distinguish essential evidence from
optional orientation.

## Choosing an output format

Use `--format json` between processes and independent releases. JSON artifacts
have strict current schemas, deterministic ordering and serialization, stable
identity, and explicit version negotiation.

Use `--format markdown` for durable code-review attachments and design records.
Headings and labels preserve conflicts, omissions, limitations, and recovery
actions without relying on colour.

Use `--format text` for terminals and logs. It is concise and ANSI-free, but it
is not an interchange format and consumers should not parse it.

## Source and privacy

A session excludes repository source text by default. Exact `--include-source`
paths make selected content portable for a trusted consumer, but also change
the publication boundary. Imported provider text is untrusted and can contain
private material even when Anatomize validates its structure.

Before publishing an artifact:

- prefer a Markdown projection over a complete machine session;
- inspect explicitly included source and provider content;
- retain repository-relative paths and content digests;
- do not treat artifact encoding as secret scanning; and
- publish only the evidence needed to understand the decision.

## Schemas and compatibility

The [generated schema catalogue](generated/schemas.md) provides reviewable and
downloadable JSON Schemas. Negotiate current schema versions using
`review capabilities`. There is no migration layer: regenerate an older or
unknown artifact with the current producer rather than editing its version,
identity, or digest.
