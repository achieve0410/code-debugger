# AGENTS.md

Repository operating guide for `kg-debugger`. Higher-priority instructions
prevail.

## Product boundary

Maintain a local-first execution-flow debugger for React/TS/JS/JSX/TSX, Vue 3
SFCs, Nuxt 3+ file-based routing and request APIs, and Django
URL/view/helper/model/recognizable ORM structure. Nuxt `server/api` routes are
terminal interface boundaries and are never traversed. It produces a strict
static `GraphSnapshotV2` and may return a bounded, explicit local runtime
overlay. Do not claim exhaustive discovery: unsupported or dynamic facts must
remain `Unresolved`.

Keep the topology truthful: request payload nodes resolve before Django,
external, or unresolved targets; page/component HTTP calls are legal without a
synthetic function. Preserve canonical route order
`(browserBackendGroup, path, repository, framework, nodeId, id)` and the
Routes:Graph:Inspector 3:6:3 desktop layout.

## Security and data rules

- Bind the service to `127.0.0.1`; retain HTTPS certificate verification.
- Never connect to production, databases, non-loopback collectors, or external
  URLs. External URLs are terminal boundaries only.
- Never add telemetry, exports, CORS, external traversal, TLS bypasses, or
  capability persistence/printing.
- Never read/store/echo credentials, cookies, authorization/session data,
  private keys, request/response bodies, raw source expressions, URL query
  values/userinfo, source excerpts, or absolute roots.
- The mutation capability is memory-only/no-store. Keep it out of graph/store,
  diagnostics, logs, URLs, UI, browser storage/cache, and error text.
- Runtime capture is disabled by default. Enabled middleware requires an
  explicit non-secret injected capture ID and collector. Do not restore GET
  runtime, all-event selection, query selectors, `includeRuntime`, or client
  project/run/scope/repository-set selection.
- Static save precedes a selected capture overlay. Persisted graph snapshots
  and analysis runs are static-only; overlay evidence/diagnostics are response
  ephemeral and capture/session isolated.
- Do not execute/import analyzed code, Django settings, or custom converters.
  Finite URL linking requires the full proven-domain/converter/unique-target
  contract; encoder syntax or broad `number` is insufficient.
- External repositories require user-supplied absolute roots. Do not follow
  symlinks or scan `.ssh`, `.aws`, or `.config`; never disclose resolved
  absolute roots.

No local web-service claim authenticates against a malicious local process.
The loopback Host/Origin/capability controls protect the intended local browser
and server boundary; they are not a multi-user hostile-host security boundary.

## Local artifacts

Never commit: `venv/`, `node_modules/`, `pem/`, `.kg-debugger/`, `.omx/`,
`qa-screenshots/`, screenshots/QA JSON, `web/dist/`, `playwright-report/`,
`test-results/`, coverage, `.env*`, logs, editor files, caches, private keys,
credentials, local absolute paths, or analyzed-project data.

## Commands

Run from repository root; scripts source `scripts/env.sh` for pinned local
toolchains.

```sh
./scripts/bootstrap.sh
./scripts/run.sh --project fixtures/react-django
./scripts/check.sh
```

Package commands:

```sh
npm run typecheck
npm run lint
npm run test:js
npm run test:python
npm run build
npm run test:e2e
```

`./scripts/check.sh` runs typecheck, lint, JS/Python tests, build, and E2E.
Use `--cacert ./pem/cert.pem` for HTTPS curl/smoke checks; never use insecure
TLS options. Documentation-only changes must be checked against current
scripts, package commands, CLI, API, and contract code.

## API and recovery invariants

All `/api/*` paths use an empty query string. `/api/config` supplies the
memory-only mutation capability and safe repository display roots;
`POST /api/analyze` accepts exactly `{}` or an explicit
`{"runtimeCaptureId":"..."}`. `GET /api/runtime` is always `404 not_found`.
The canonical status recovery contract is: no snapshot `404
snapshot_not_found`; incompatible `409 snapshot_incompatible` / `reanalyze`;
invalid `422 snapshot_invalid` / `delete_or_reanalyze`; runtime-unavailable
capture `409 runtime_unavailable` / `analyze_without_capture`.

UI config/capability loading is independent of graph recovery. Never use demo
content for 404/409/422/network failure. Install successful Analyze POST data
directly; capture data is transient until explicit static Refresh, never
Analyze-then-GET. Preserve selection only for surviving IDs and use canonical
browser-then-Django fallback.

## Change and verification discipline

Read callers, tests, and `docs/graph-contract.md` plus
`docs/security-model.md` before changing a contract. Use existing patterns;
do not widen graph metadata, evidence/diagnostic catalogs, legal tuples, API
status/envelopes, DDL, identity inputs, persistence, or static minimization.
Unknown data fails closed rather than being redacted into valid graph data.

For behavior changes, run the focused test appropriate to the changed area and
then the relevant broader check. Security/protocol work requires strict HTTPS
and negative framing/guard coverage; graph/analyzer work requires contract and
merge coverage; UI work requires typecheck/build/E2E and affected viewport
inspection. Bootstrap/dependency changes require shell syntax, a clean
bootstrap, and `./scripts/check.sh`. Do not suppress warnings or checks. State
an unrun check precisely.

## Git workflow

This is a solo-maintained public repository. External contributions use pull
requests. The maintainer may use the verified solo squash workflow below
without requiring a second-person approval.

1. Fast-forward local `main` from `origin/main`.
2. Create an `agent/<description>` branch; do not work directly on `main`.
3. Stage only the intended scope and create a Lore-format commit.
4. Run `./scripts/check.sh`.
5. Push the feature branch and wait for its GitHub Actions `check` to pass.
6. Squash the verified branch into local `main` with a Lore-format commit.
7. Push `main` and verify that local `main` matches `origin/main`.

Never force-push. A failed or pending local check or GitHub Actions check blocks
the merge.

## Definition of done

- The requested behavior is implemented without expanding scope.
- Targeted regressions and `./scripts/check.sh` pass.
- No production/external-data behavior was introduced.
- No generated, private, or analyzed-project artifact is staged.
- README usage and limitations still match the implementation.

Graph-v2 downgrade is destructive: stop debugger and middleware processes,
delete `.kg-debugger/graph.sqlite3`, install the older binary, regenerate static
analysis, then restart. Do not create an in-place downgrade or compatibility
bypass.
