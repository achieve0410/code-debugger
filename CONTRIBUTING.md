# Contributing to code-debugger

Thank you for helping improve `code-debugger`. Contributions should preserve its
local-first, fail-closed security and graph-contract boundaries.

## Start here

- [ROADMAP.md](ROADMAP.md) describes current direction and explicit non-goals.
- [MAINTAINERS.md](MAINTAINERS.md) documents ownership and response
  expectations.
- [docs/analyzer-conformance.md](docs/analyzer-conformance.md) explains the
  synthetic fixture workflow and support matrix.
- [docs/graph-contract.md](docs/graph-contract.md) and
  [docs/security-model.md](docs/security-model.md) define the trusted product
  boundary.

## Before opening a change

- Search existing issues and pull requests first.
- Use an issue to discuss large behavior, graph-contract, API, persistence, or
  security changes before implementation.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
  Do not disclose vulnerability details in a public issue.

## Development setup

Requirements are macOS or Linux, Python 3.13, OpenSSL, and access to the pinned
toolchain distributions used by bootstrap.

```sh
git clone https://github.com/achieve0410/code-debugger.git
cd code-debugger
./scripts/bootstrap.sh
./scripts/check.sh
```

Run the debugger against a synthetic fixture with:

```sh
./scripts/run.sh --project fixtures/react-django
```

Open `https://localhost:8443` and trust the generated local development
certificate normally. Never disable TLS verification.

## Change requirements

Keep changes focused and reuse existing patterns. In particular:

- Preserve the strict `GraphSnapshotV2` contract and canonical route ordering.
- Keep unknown, dynamic, or unsupported facts explicitly `Unresolved`.
- Never execute or import analyzed application code, Django settings, or custom
  converters.
- Never connect to production, databases, non-loopback collectors, or external
  URLs.
- Never add telemetry, CORS, automatic/bulk exports, TLS bypasses, or persistent
  capabilities.
- Keep the Inspector clipboard action limited to an explicit selected local
  node's segment-wise-decoded repository-relative filesystem
  `path[:line][:symbol]`; never widen it to source text, graph/body data,
  absolute roots, credentials, capabilities, or edge/external/unresolved
  selections.
- Do not collect or publish credentials, cookies, headers, request/response
  bodies, query values, source excerpts, absolute roots, or real analyzed
  project data.
- Use only synthetic data in fixtures, tests, issues, and pull requests.
- Do not commit generated or local artifacts such as `venv/`, `node_modules/`,
  `pem/`, `.kg-debugger/`, `web/dist/`, test reports, screenshots, logs,
  coverage, or `.env*` files.

Read [docs/graph-contract.md](docs/graph-contract.md) and
[docs/security-model.md](docs/security-model.md) before changing graph, API,
runtime, storage, or security behavior.

## Analyzer fixture contributions

Analyzer fixtures are synthetic conformance inputs, not plugin packages or real
project samples. Add frontend examples under `fixtures/<name>/frontend/`,
Django examples under `fixtures/<name>/backend/`, and update
`fixtures/analyzer-conformance.json` when the fixture is part of the stable
conformance set. Keep expected outputs structural and value-free.

See [docs/analyzer-conformance.md](docs/analyzer-conformance.md) for the
fixture workflow, support/limitations matrix, and release metadata consistency
checks. The focused validation command is:

```sh
npm run test:js
```

## Verification

Run the focused test for the changed area and then the complete check:

```sh
./scripts/check.sh
```

Available focused commands include:

```sh
npm run typecheck
npm run lint
npm run test:js
npm run test:python
npm run build
npm run test:e2e
```

Do not suppress warnings, weaken assertions, or bypass certificate validation to
make a check pass.

## Agent-assisted contributions

Agent-assisted changes follow the same review, security, authorship, and
verification requirements as any other contribution.

- The submitter owns and reviews the complete result.
- Never provide an agent with credentials, private source, non-public project
  data, analyzed-project output, absolute roots, or real request/runtime data.
- Treat generated code and analysis as untrusted until checked against the
  current source, contracts, and tests.
- Keep automated changes focused and include a failing-first regression proof
  for behavior changes.
- Do not let an agent publish releases, change repository permissions, merge
  pull requests, or disclose security reports without explicit maintainer
  authorization.
- Mention material agent assistance in the pull-request summary when it helps
  reviewers understand how the change was produced or verified.

Using an agent does not replace human review, required checks, or responsibility
for licensing and provenance.

## Pull requests

A pull request should:

- explain the user-visible problem and the chosen minimal solution;
- identify affected security or graph-contract boundaries;
- include regression coverage for behavior changes;
- update directly affected documentation and fixtures;
- contain no generated, private, or analyzed-project artifacts; and
- pass the required GitHub Actions checks.

Maintainers may request changes or close proposals that broaden data collection,
weaken fail-closed behavior, or fall outside the project's supported boundary.

## Maintainer releases

Use the [release checklist](docs/releasing.md) to keep public versions, supported
release lines, verification evidence, and GitHub release notes consistent.
