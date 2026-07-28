# code-debugger

[![CI](https://github.com/achieve0410/code-debugger/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/achieve0410/code-debugger/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> Project status: early `v0.2.x`. The graph and security contracts are strict,
> but framework coverage remains intentionally bounded and unsupported behavior
> is reported as `Unresolved`.

`code-debugger` is a local execution-flow debugger for React, Vue 3, Nuxt 3+,
and Django. It statically connects browser routes, UI ownership/events, HTTP
and payload shapes, Django URLs/views/helpers, and ORM boundaries. Unknown or
unsupported cases remain `Unresolved`; it does not claim exhaustive discovery.
Nuxt `server/api` routes are terminal interface boundaries and are not
traversed.

The repository and distribution are named `code-debugger`. The Python import
namespace (`kg_debugger`) and existing on-disk, wire, and serialized identifiers
remain stable compatibility identifiers in `v0.2.x`.

It binds the UI/API to loopback HTTPS. It does not contact production services,
databases, or external URLs, and it has no telemetry or export path.

## Demo

[![code-debugger v0.2.0 showing Routes, an expanded orders execution graph, Flow Outline, and Evidence Inspector for the synthetic Nuxt and Django fixture](https://github.com/achieve0410/code-debugger/releases/download/v0.2.0/code-debugger-v0.2.0-demo.png)](https://github.com/achieve0410/code-debugger/releases/download/v0.2.0/code-debugger-v0.2.0-demo.png)

*Actual `v0.2.0` workbench using only synthetic `fixtures/nuxt-django` data.
Select **Analyze**, choose `/orders`, and use **Expand all** to reveal the
frontend-to-Django flow. Select any node or edge to inspect its bounded evidence
on the right. Click the image for the full-size view.*

## What it shows

For the synthetic Nuxt/Django fixture, selecting `/orders` exposes a bounded
chain like:

```text
/orders
  -> frontend/pages/orders/index.vue
  -> POST /api/orders/
  -> request payload
  -> orders.urls: POST /api/orders/
  -> orders.views.order_collection
  -> orders.models.Order
```

By contrast, a dynamic `/orders/:id` request remains `Unresolved` when its route
value cannot be proven finite. The graph shows the supported evidence without
guessing across that boundary.

## Setup and run

Requirements: macOS/Linux, Python 3.13, OpenSSL, and access to the official
Node.js/npm/PyPI/Playwright distributions used by bootstrap.

The `v0.2.x` releases are source-only. This project does not publish npm/PyPI
packages or prebuilt binaries; use the pinned bootstrap workflow below.

```sh
git clone https://github.com/achieve0410/code-debugger.git
cd code-debugger
./scripts/bootstrap.sh
./scripts/run.sh --project fixtures/react-django
```

Open `https://localhost:8443`. The server binds only `127.0.0.1`; trust the
locally generated development certificate normally—do not disable certificate
verification. Bootstrap creates local `venv/`, `pem/`, and
`.kg-debugger/graph.sqlite3` artifacts, all ignored by Git.

Analyze a workspace-relative project or explicitly supplied external local
root:

```sh
./scripts/run.sh --project fixtures/react-django
./scripts/run.sh --project fixtures/nuxt-django
./scripts/run.sh --repo /absolute/path/to/frontend --repo /absolute/path/to/backend
```

Relative roots resolve only inside this workspace. External roots require an
explicit absolute directory. Root symlinks, traversal, credential directories
(`.ssh`, `.aws`, `.config`), and traversed symlinks are rejected/pruned.
Config/API display roots are safe relative POSIX paths or `external:<namespace>`;
resolved absolute roots never leave process-local configuration.

## Graph and static analysis

The persisted wire is strict `GraphSnapshotV2` (`schemaVersion: 2`) scoped to
the active project, repository set, and manifest. It persists static evidence
only. Routes are canonicalized browser-first using
`(browserBackendGroup, path, repository, framework, nodeId, id)`; payload
nodes, when present, resolve before Django/external/unresolved targets.

The analyzer retains only structural facts: route/path shape, method, body/query
counts and booleans, converter kind, ownership, and catalogued diagnostics. It
never stores source expressions, query/body names or values, userinfo, headers,
cookies, credentials, or source excerpts. A literal URL can link normally; a
computed URL links exactly only after finite-domain, encoding/decode, built-in
converter, active-manifest, and unique-target proof. An encoder spelling or a
`number` type alone is not proof. Broad/dynamic values, unsafe members,
shadowed encoders, and `path`/custom converters remain unbounded and unresolved.

React Router v6 JSX/data routes, Vue 3 SFC routes, and Nuxt 3+ file-based
`pages/` routes are supported. Nuxt `useFetch`/`$fetch` and
`NuxtLink`/`navigateTo` are analyzed; layouts and middleware chains are outside
the current route graph. Unreadable, oversized, or unparseable source remains
bounded by catalogued diagnostics rather than aborting the whole analysis.

Django analysis never imports project code, Django settings, or converters. A
Python qualified name is emitted only under its unique configured/manage.py
import-root proof; ambiguity or absence remains unresolved.

See [the graph contract](docs/graph-contract.md) for wire fields, legal tuples,
proof lifecycle, identity, migration, and API status details.

## Optional local runtime capture

Runtime capture is disabled by default:

```sh
./scripts/run.sh --runtime --project fixtures/react-django
```

Enabled middleware requires an application-injected, non-secret capture ID and
collector; it has no request-header/default/generated capture fallback. The
server creates event ID and timestamp provenance. `GET /api/runtime` does not
exist. Runtime is selected only by an explicit `runtimeCaptureId` in
`POST /api/analyze`; client project/run/scope/set selectors, `includeRuntime`,
and every API query string are rejected.

The following example keeps the capability only in shell/process memory: it is
not printed, written, or placed in command history. Its expansion in the
request-header argument is unavoidable. `CAPTURE_ID` is an explicit non-secret
identifier chosen for this capture.

```sh
PYTHON=./venv/python3.13/bin/python
CAPABILITY="$(curl --fail --silent --show-error --cacert ./pem/cert.pem \
  https://localhost:8443/api/config | "$PYTHON" -c \
  'import json,sys; print(json.load(sys.stdin)["mutationCapability"])')"
CAPTURE_ID=local-capture-20260726

curl --fail --silent --show-error --cacert ./pem/cert.pem \
  -H "X-KG-Debugger-Capability: $CAPABILITY" \
  -H 'Content-Type: application/json' \
  --data '{"captureId":"local-capture-20260726","method":"GET","path":"/api/items/","status":200,"durationMs":12}' \
  https://localhost:8443/api/runtime

curl --fail --silent --show-error --cacert ./pem/cert.pem \
  -H "X-KG-Debugger-Capability: $CAPABILITY" \
  -H 'Content-Type: application/json' \
  --data '{"runtimeCaptureId":"local-capture-20260726"}' \
  https://localhost:8443/api/analyze
unset CAPABILITY
```

Canonical runtime input uses only `captureId`, `method`, `path`, optional
`target`, `endpointId`, `viewQualifiedName`, `status`, `durationMs`,
`traceparent`, and `tracestate`. Exact v1 legacy spelling is accepted only via
its documented normalization (`runId`, `view`, `trace`), never mixed with the
canonical equivalents. The capability is memory-only and no-store: never put it
in SQLite, graph data, diagnostics, URLs, browser storage/cache, logs, rendered
text, or error objects.

Static analysis is saved first. A selected capture is overlaid only in the
Analyze POST response and is never persisted. Every supplied event identity
must prove one URL/view flow before any `Observed` evidence is appended;
unmatched, ambiguous, conflicting, or empty captures yield only bounded
transient diagnostics. Captures are isolated by active project, runtime scope,
and capture ID. The UI shows a transient badge for this response; it does not
issue an automatic graph GET. Use explicit **Refresh** to replace it with the
saved static graph. The desktop layout is Routes:Graph:Inspector = 3:6:3.

## API summary

All `/api/*` responses are JSON, `Cache-Control: no-store`, with no CORS.
Exact paths have no query string. `GET /api/health`, `/api/config`, and
`/api/graph` are available; graph absence is `404 snapshot_not_found`, an
incompatible snapshot is `409 snapshot_incompatible` with `action: reanalyze`,
and invalid stored data is `422 snapshot_invalid` with
`action: delete_or_reanalyze`. `GET /api/runtime` and unknown routes return
`404 not_found`; OPTIONS is `405 method_not_allowed`.
`GET /api/health` returns `{"ok":true,"status":"ready"}`. Config is exactly:

```json
{
  "project": "<active project>",
  "runtimeEnabled": false,
  "schemaVersion": 2,
  "repositorySetId": "<64 lowercase hex>",
  "repositories": [{"namespace":"frontend","displayRoot":"fixtures/frontend"}],
  "repoRoots": ["fixtures/frontend"],
  "compatibilityWarnings": ["repoRoots_deprecated_v2"],
  "mutationCapability": "<memory-only capability>"
}
```

Repositories are namespace-sorted. `repoRoots` is one-release display-only
compatibility data, not an accepted identity input.

`POST /api/runtime` returns `202` with
`{"ok":true,"eventId":"<server UUIDv4>","captureId":"<canonical capture ID>","receivedAt":"<UTC ms Z>","warnings":[]}`;
a normalized v1 event instead has `warnings:["legacy_runtime_event_v1"]`.
`POST /api/analyze` returns the saved static graph or a selected transient
overlay. Runtime-disabled capture analysis is `409 runtime_unavailable` with
`action: analyze_without_capture`. Host, Origin, capability, route/runtime
policy, and strict JSON framing are checked before work. See
[security model](docs/security-model.md) for the complete guard and framing
matrix.

## Migration and downgrade

Graph v2 migration is transactional and strict. It reconstructs the literal v2
SQLite schema, quarantines only minimized/hash-only legacy material, and uses
qv2 typed-byte SHA-256 hashes rather than parsed JSON. A matching v2 database
initializes without writes; mixed/partial schemas fail rather than being
repaired.

Downgrade is destructive: stop all debugger and middleware processes, delete
`.kg-debugger/graph.sqlite3`, install the older binary, regenerate static
analysis, then restart. There is no in-place downmigration and proof/quarantine
hashes do not provide one.

## Verification

```sh
./scripts/check.sh
```

Focused commands are `npm run test:js`, `npm run test:python`, and
`npm run test:e2e`; `npm run typecheck`, `npm run lint`, and `npm run build`
remain available. HTTPS verification trusts `pem/cert.pem`; no check should
bypass TLS verification.

## Repository layout

- `analyzers/` — React/Vue/Nuxt static analyzer
- `src/kg_debugger/` — Django analysis, graph/storage, runtime, HTTPS API
- `web/src/` — React/Cytoscape UI
- `fixtures/` — deterministic React/Vue/Nuxt and Django examples
- `fixtures/analyzer-conformance.json` — stable analyzer fixture manifest
- `docs/` — graph and security contracts
- `scripts/` — bootstrap, run, and verification entry points
- `tests/` — Python, Node, and Playwright checks

Local/generated/sensitive artifacts (`venv/`, `node_modules/`, `pem/`,
`.kg-debugger/`, `web/dist/`, reports, coverage, logs, and `.env*`) stay out of
Git.

## Getting help

- Use [GitHub Discussions](https://github.com/achieve0410/code-debugger/discussions)
  for usage questions and open-ended design conversations.
- Use the repository issue forms for reproducible bugs and bounded feature
  requests, with synthetic non-sensitive examples only.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md);
  never disclose sensitive details in a public issue or discussion.

## Analyzer conformance

Contributors can validate analyzer changes with the static fixture workflow in
[docs/analyzer-conformance.md](docs/analyzer-conformance.md). It documents the
synthetic fixture contribution process, support/limitations matrix, and release
metadata consistency checks used by `npm run test:js`.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull-request
requirements and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community
standards. Report vulnerabilities privately according to
[SECURITY.md](SECURITY.md). Maintainers should follow the
[release checklist](docs/releasing.md).

## License

[MIT License](LICENSE)
