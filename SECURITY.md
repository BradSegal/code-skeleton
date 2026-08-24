# Security policy

## Supported versions

Security fixes apply to the latest release. Anatomize accepts only exact
current artifact schemas; regeneration from source is the compatibility path.

## Report a vulnerability

Use [GitHub private vulnerability
reporting](https://github.com/BradSegal/anatomize/security/advisories/new), not a
public issue. Include the affected version, input boundary, minimal synthetic
reproduction, expected containment, and known mitigation. Do not include live
credentials or private repository content.

## Trust boundary

Repository files, symlinks, embedded documentation, provider envelopes, native
tool artifacts, diagnostic messages, and suggested fixes are untrusted data.
Anatomize:

- normalizes repository paths and resolves selected files inside the declared
  root;
- reads baseline files without importing or executing repository code;
- never discovers provider plugins, invokes tools, accesses the network, or
  follows external artifact references during review;
- parses strict exact-version models with byte, depth, collection, string, and
  numeric bounds;
- rejects XML DTDs and entity expansion;
- validates state, digest, coordinate, identity, and cross-reference bindings;
- omits source text unless the caller names an exact path;
- publishes requested files atomically and protects session-store publication
  with an advisory operating-system lock; and
- confines MCP streamable HTTP to loopback with bounded inputs, results, and
  operation duration.

Anatomize is a data processor, not an operating-system sandbox. The caller is
responsible for safely running compilers, tests, scanners, workflow engines,
and other artifact producers before import. Artifact encodings preserve
structure; they do not make embedded content safe to execute or obey.

## Out of scope guarantees

Anatomize is not a secret scanner, malware detector, vulnerability scanner,
dependency resolver, package-signature verifier, authorization service,
multi-user server, test runner, scientific-validity assessor, or release
approval. Use the appropriate specialist tool and import its bounded result
when it contributes to review.

Before publishing an Anatomize artifact, inspect explicitly included source and
provider payloads for sensitive material. A content-free baseline reduces
exposure but cannot guarantee that filenames, symbols, diagnostics, metadata,
or imported artifacts contain no confidential information.
