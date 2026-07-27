# Security model

## Scope and threat boundary

`code-debugger` is a local developer tool, not a production service or hostile
multi-user isolation boundary. It binds to `127.0.0.1` and serves loopback HTTPS
only in normal operation. Host, Origin, per-process mutation capability, strict
framing, no-store responses, and no CORS protect the intended local browser/API
exchange. They do **not** authenticate against a malicious local process that
can access the user account, process memory, local certificate/private key, or
loopback socket. Do not claim otherwise.

The tool never connects to production servers/databases, non-loopback runtime
collectors, or source-discovered external URLs. It has no telemetry, exporter,
remote collector, or external traversal. An external URL is parsed without
fetching and represented only as a terminal structural boundary.

Configured roots are read-only. Workspace-relative roots must remain inside the
workspace; external roots must be explicit local absolute directories. Missing,
non-directory, root-symlink, traversal, credential-directory, and escaping
paths reject. Traversal prunes symlinks and `.ssh`, `.aws`, and `.config`.
Resolved absolute paths remain process-local. Public config uses only a safe
relative POSIX `displayRoot` or `external:<namespace>`; the compatibility
`repoRoots` alias has those same display strings, never absolute roots.

## HTTPS and API guards

`scripts/generate-dev-cert.sh` creates `pem/cert.pem` and `pem/key.pem` for the
local service; the private key is owner-readable/writable only and is never
printed. Clients/tests trust `pem/cert.pem` normally. Do not use `-k`, disabled
verification, TLS bypasses, or a non-loopback bind.

All `/api/*` responses are JSON with exact Content-Length and
`Cache-Control: no-store`; no CORS header is emitted. Exact API paths reject
any query component before work. The guard order for mutation is:

1. Host;
2. Origin;
3. mutation capability;
4. route/runtime policy;
5. transfer/media/length/body framing;
6. work.

Host must occur exactly once and be exactly `127.0.0.1:<bound-port>` or
`localhost:<bound-port>`; malformed/duplicate Host is `400 invalid_host_header`
and a well-formed other authority is `421 misdirected_request`. Origin is absent
or exactly the served scheme and accepted authority/port; malformed/duplicate
Origin is `400 invalid_origin_header`, and a present nonmatching Origin
(including `null`) is `403 origin_forbidden`.

Mutations require exactly one `X-KG-Debugger-Capability`. Missing/wrong is
`403 mutation_forbidden`; duplicates are `400 invalid_capability_header`. The
capability is generated per server process and is memory-only. It is supplied
only through `/api/config` over local HTTPS, must not be printed or persisted,
and is forbidden from SQLite, graph snapshots, analysis runs, diagnostics,
logs, URLs, browser storage/cache, rendered UI, and error objects.

The framing contract rejects any Transfer-Encoding (`400
unsupported_transfer_encoding`), missing Content-Length (`411 length_required`),
and duplicate/signed/OWS/comma/non-decimal/zero/noncanonical length (`400
invalid_content_length`). Bodies over 1,048,576 bytes are `413 request_too_large`;
missing/non-JSON Content-Type or charset other than UTF-8 is `415
unsupported_media_type`; timeout is `408 request_timeout`; premature EOF,
invalid UTF-8/JSON, trailing token, or non-object body is `400
invalid_json_body`. A rejected request performs no store/analyzer mutation.
Mutation connections close for both success and failure.

The API has no client project, run, scope, repository-set, or capture query
selector. `GET /api/runtime` is deliberately `404 {"error":"not_found"}`.
Any query component on any API path, known or unknown, returns
`400 {"error":"invalid_selector"}`. Unknown API paths/unsupported GET return
`404 {"error":"not_found"}` and API OPTIONS returns
`405 {"error":"method_not_allowed"}`.
`POST /api/analyze` accepts only `{}` or
`{"runtimeCaptureId":"<validated capture ID>"}`. `includeRuntime`, null,
unknown keys, and selectors reject. Graph load outcomes are: absent `404
snapshot_not_found` / `analyze`; incompatible `409 snapshot_incompatible` with
catalog code / `reanalyze`; invalid `422 snapshot_invalid` /
`delete_or_reanalyze`. Runtime-disabled capture analysis is `409
runtime_unavailable` / `analyze_without_capture`.

## Runtime compartment

Runtime capture is disabled by default. Enabled `RuntimeEvidenceMiddleware`
requires an application-injected callable collector and validated non-secret
capture ID at construction; no environment/default/request-header/generated
capture ID exists. Canonical client event fields are only `captureId`, `method`,
`path`, optional `target`, `endpointId`, `viewQualifiedName`, `status`,
`durationMs`, `traceparent`, and `tracestate`. The exact v1 adapter normalizes
`runId`, `view`, and nested `trace`; mixed canonical/legacy equivalents reject.
The server, not the client, assigns UUIDv4 event ID and UTC-millisecond
`receivedAt` provenance.

Each runtime read is bound to active project, runtime scope, and capture ID.
Static graph validation/save and static `analysis_runs` write happen before any
selected overlay. The overlay is a response-only clone: it cannot persist
`Observed` evidence, runtime diagnostics, event IDs, capture IDs, or scope IDs.
An event changes a graph only after all supplied endpoint/view identities prove
one canonical URL/view flow; unmatched, ambiguous, conflicting, and empty
capture cases add fixed, value-free transient diagnostics and no partial
observation. A capture never falls back to all events or another capture.

## Static minimization and graph proof

Static graph wire and persistence are allowlisted. It retains only structural
method/path shape, route ownership, body/query counts and sensitive booleans,
converter types, and fixed catalog codes/messages. It rejects unknown metadata,
diagnostic/reason codes, secret-like keys/values, raw source bodies/expressions,
request/response bodies, field names/values, cookies, authorization headers,
userinfo, trace baggage, credentials, and absolute roots. Redaction never turns
invalid input into valid graph material.

Secret-key detection NFKC-normalizes/casefolds/splits camel boundaries and
rejects normalized terms such as authorization, cookie, password, secret,
token, API key, credential, private/client key, session, CSRF/XSRF, baggage,
and request/response body (except the catalogued sensitive booleans). Free
strings reject private-key PEM headers, Bearer/Basic credentials, JWT shapes,
AWS/GitHub/Slack token patterns, labelled secret assignments, and unlabelled
long hex/base64url runs. Content IDs/hashes, server UUIDs, and schema-validated
Python qualified names are exempt only in their structurally validated fields. Detection hits
are never reflected in diagnostics, errors, logs, API data, or quarantine safe
payloads.

A bounded computed URL uses a temporary, value-free proof sidecar. It contains
only version, local call binding, normalized placeholder template, position,
member count, and accepted built-in converter kinds. It never contains finite
members, expressions, identifiers, member hashes, examples, or secret/value
material. The producer must prove every finite member segment-safe through
normalization and one/two decode checks; unshadowed global encoder use is
necessary but not sufficient, and broad/unsafe numbers are unbounded. Django
converter acceptance is limited to built-in int/str/slug/uuid; custom/path
converters are never executed or exact-linked.

Canonicalization binds the sidecar to recomputed HTTP identity. Merge validates
an active-manifest unique Django target, exact placeholder positions, converter
acceptance, and optional target repository before creating a dynamic-converter
link. Any absence/malformed/mismatch/ambiguity produces unresolved topology.
The proof is destroyed before `GraphSnapshotV2`; it cannot appear in SQLite,
API, UI, diagnostics, or logs and cannot be recreated after load/remerge.

Django analysis never imports application code/settings or executes custom
converters. Python qualification is emitted only after a unique static
configured/manage.py import-root proof; otherwise it remains unresolved.

## Storage, migration, and recovery

The SQLite store holds static snapshots/runs, scoped minimized runtime events,
and quarantine—not a capability or persisted runtime overlay. Graph v2
migration runs in one `BEGIN IMMEDIATE` transaction. It recognizes only the
literal old schema, reconstructs literal strict v2 tables/indexes, validates and
counts rows, swaps, and commits. Failure rolls back to the old tables/rows; a
matching v2 database performs no writes; a partial/mixed schema is an error.
No opaque legacy blob is copied.

Quarantine uses a qv2 SHA-256 preimage over a domain-separated, length-framed
SQLite type tag and exact original bytes, before UTF-8 or JSON parsing. NULL,
TEXT, BLOB, integers, reals, invalid UTF-8, non-JSON, and scalar values are
distinguished. Unsafe/malformed values are hash-only; `safe_payload` is allowed
only after current minimization and validation. Duplicate quarantine rows use
conflict-do-nothing so the first timestamp/safe fields remain unchanged. These
digests are integrity/audit identifiers, not capabilities or security tokens.

After a committed v2 migration, downgrade is destructive: stop debugger and
middleware processes, delete `.kg-debugger/graph.sqlite3`, install the older
binary, regenerate static analysis, and then restart. There is no
compatibility flag, in-place downmigration, or retained proof/value data that
can make old code safely read v2 storage.
