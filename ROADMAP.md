# Roadmap

This roadmap communicates direction, not delivery commitments. It has no fixed
dates, adoption targets, or guaranteed features. A capability is supported only
after it is merged, documented, released, and covered by the maintained
conformance workflow.

## Current direction

### Verifiable source distribution

- Publish versioned source archives with SHA-256 checksums.
- Run clean archive installation smoke tests on Linux and macOS.
- Attach an SPDX dependency SBOM and GitHub artifact attestations to tagged
  releases.
- Keep npm, PyPI, binary, container, and remote-installer distribution outside
  the supported contract until their complete runtime and security boundaries
  can be proven.

### Contributor and maintainer clarity

- Keep public ownership, review routing, and response expectations explicit.
- Maintain bounded contribution examples and synthetic fixture guidance.
- Reserve `good first issue` and `help wanted` for genuinely open, scoped work.
- Grow maintainer access only through sustained reviewable contributions.

### Privacy-safe project evidence

- Define consent and minimization rules before publishing any pilot result.
- Publish only aggregate, value-free evidence with explicit limitations.
- Keep synthetic conformance evidence separate from third-party adoption
  evidence.

## Analyzer evolution

Analyzer support may expand when a proposed fact can be represented without
executing analyzed code, collecting private values, or weakening deterministic
graph identities. Unsupported or ambiguous behavior continues to resolve as
catalog-backed `Unresolved` evidence.

The current maintained matrix is documented in
[docs/analyzer-conformance.md](docs/analyzer-conformance.md).

## Non-goals

The roadmap does not include:

- telemetry or remote source upload;
- production service, database, or external URL traversal;
- request or response body collection;
- exhaustive analysis claims;
- automatic conversion of unverified runtime observations into graph topology;
- TLS bypasses or persistent mutation capabilities; or
- fabricated usage, benchmark, or adoption claims.

## Contributing

Use the issue forms to propose a bounded problem and read
[CONTRIBUTING.md](CONTRIBUTING.md) before implementation. The maintainer may
accept, defer, narrow, or decline roadmap proposals based on security,
maintenance cost, and fit with the documented product boundary.
