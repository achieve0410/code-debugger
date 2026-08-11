# GraphSnapshotV2 contract

`GraphSnapshotV2` is the only persisted graph wire. It is strict at fragment,
merge, save, load, and API boundaries: no coercion, repair, unknown keys, or
unsorted input. Its required top-level keys are exactly
`schemaVersion`, `project`, `repositorySetId`, `repositories`, `routes`,
`nodes`, `edges`, and `diagnostics`. `schemaVersion` is integer `2` (never a
boolean); the repository set is 64 lowercase hex; repositories are sorted,
nonempty `{namespace}` records (1–64) that deep-equal the active manifest.
Namespaces are NFC/casefold-unique and match `[a-z][a-z0-9._-]{0,63}`.

Routes are exactly `{id,repository,framework,path,nodeId}`. `id` is the
recomputed `r_` SHA-256 identity of repository/framework/normalized path/node;
semantic `(repository,framework,path)` uniqueness is independent of display
order. The sole route key is:

```text
(1 if framework == "django" else 0, path, repository, framework, nodeId, id)
```

Strings compare after required NFC validation by Unicode code point. This makes
browser routes precede Django and breaks every remaining tie deterministically.
No source/root/fragment insertion order is an identity or ordering input.

## Record shapes and identity

A source location is exactly `{repository,path,line?,endLine?,symbol?}`.
`path` is a safe repository-relative POSIX path; absolute/drive/UNC,
backslash, traversal, encoded separators, controls, and symlink provenance are
invalid. The persisted/displayed graph retains that canonical path. Only the
explicit Inspector clipboard projection decodes each validated segment once to
produce a repository-relative filesystem `path[:line][:symbol]`; this does not
change graph identity or persistence. A node is exactly:

```text
{id,kind,identityKey,label,layer,source,evidence,confidence,metadata}
```

and an edge is exactly:

```text
{id,source,target,kind,evidence,confidence,metadata}
```

Node identity is `n_` plus SHA-256 of
`("node", repository, normalized path, kind, identityKey)`; edge identity is
`e_` plus SHA-256 of `("edge",source,target,kind)`. IDs are lower-case
64-hex forms. Equal identities merge only when semantically deep-equal;
otherwise they are a typed identity conflict, never suffix-repaired.

Identity may use an AST-local ordinal only when scoped to a stable semantic
owner and syntax class. It must not use line/end-line, byte offsets, global
traversal, source/root/configuration order, aliases, absolute paths, or display
names. Reordering same-class siblings under an owner may intentionally change
identity; unrelated lines/files and repository order may not.

Node kinds are `frontend_route`, `page`, `component`, `ui_event`, `function`,
`http_call`, `request_payload`, `django_url_pattern`, `django_view`, `model`,
`query_boundary`, `external_service`, and `unresolved_target`. Layers are
frontend/http/backend/data/external/unresolved as applicable. Confidence is a
finite JSON number in `[0,1]`; booleans are not numbers. Limits are 10,000
routes, 50,000 nodes, 200,000 edges, 10,000 diagnostics, 32 evidence records
per node/edge, 16 KiB metadata/record, 32 MiB canonical graph, and nesting 8.

Evidence is exactly `{kind,adapter,adapterVersion,reason?}` for static facts or
that shape plus server `eventId,timestamp` for runtime response facts. Kinds
are `inferred`, `unresolved`, and `observed`; persisted snapshots reject
observed evidence and runtime event fields. Adapters/version are bounded safe
tokens. Evidence is sorted/unique by its complete serialized tuple.

Diagnostics are exactly
`{id,code,severity,message,repository?,source?,nodeId?,edgeId?,candidateIds?,eventId?}`.
Their ID is recomputed from all non-ID fields. Code, severity, fixed message,
and allowed references come only from the catalog; custom messages/details,
exception text, capture/scope/request values, and arbitrary paths are illegal.
Runtime codes/event IDs are response-only.

## Metadata, paths, and catalogs

Metadata has no unknown keys. Primitive counts are integer (not boolean)
`0..1000`; member counts are `1..256`; candidate IDs are sorted/unique node IDs
`1..100`; ports are `1..65535`. `safeToken` is ASCII
`[A-Za-z][A-Za-z0-9_.-]{0,127}`. Python qualified/model names are NFC dotted
Python identifiers (3–512); converter names are Python identifiers (1–64).
Methods are uppercase RFC tokens. `normalizedPath` is a safe NFC origin path,
1–2048 characters, with no query/fragment/userinfo/backslash/control/whitespace,
encoded slash/backslash/dot segment, or dot segment.

Dynamic normalized placeholders occupy whole segments only: contiguous ordered
`{p0}`…`{p31}` for bounded paths or `{u0}`…`{u31}` for unbounded paths. They
never mix. Frontend declarations may use whole `:name` segments; Django uses
whole `<name>` or `<converter:name>` segments and producer normalization turns
them into ordered `{pN}` segments. Regex/malformed Django declarations remain
unresolved.

Exact node metadata fields:

| kind | required/allowed metadata |
|---|---|
| `frontend_route` | `framework` (`react|vue|nuxt|django`), `declaredPath` |
| `page`, `component` | sorted unique nonempty `frameworkOwners` subset of react/vue/nuxt |
| `ui_event` | `frameworkOwners`, safe `eventKind`, `elementKind`, sorted unique `modifiers` (0–16) |
| `function` | optional `frameworkOwners` and `pythonQualifiedName`; frontend requires owners, backend forbids them |
| `http_call` | `method`, `urlResolution` (`literal|bounded_template|unbounded`), `normalizedPath`, `queryFieldCount`, `hasSensitiveQuery`; optional `endpointId`, `targetRepository` |
| `request_payload` | nonempty sorted `payloadKinds` subset body/query/form; `bodyShape`, `bodyFieldCount`, `queryFieldCount`, `hasSensitiveFields` |
| `django_url_pattern` | `declaredPath`, `normalizedPath`, `endpointId`, ordered `converters` `{name,kind,segmentIndex}`; kind is int/str/slug/uuid/path/custom |
| `django_view`, `model` | optional uniquely proven `pythonQualifiedName` |
| `query_boundary` | `operation` (`all|filter|get|create|update|delete|aggregate|other`), optional `modelQualifiedName` |
| `external_service` | `method`, `scheme` http/https, canonical `host`, optional `port`, `pathPresent`, `queryFieldCount`, `hasSensitiveQuery`, `boundaryOnly:true` |
| `unresolved_target` | unresolved `reasonCode`, optional sorted `candidateIds` |

An `endpointId` is `METHOD SP normalizedPath` or
`METHOD SP namespace:normalizedPath`, and must byte-agree with sibling
method/path/target repository. It is forbidden on unbounded calls. Bounded
adapter fragments forbid it; merge creates it only after the proof below.

Edge metadata is `{}` for `renders`, `contains`, `handles`, `navigates_to`,
`calls`, `invokes`, `accesses`, and `branches_to`. `carries` is `{}` or exact
payload kinds matching its target. `resolves_to` requires
`resolutionTier` (`exact_endpoint|declared_path|configured_base|dynamic_converter|external_boundary|unbounded`) and may carry `targetRepository`.

Evidence reasons and diagnostics are closed catalogs. Inferred reasons include
`ast_route_declaration`, `ast_symbol_declaration`, `ast_call`,
`ast_handler_binding`, `ast_import_binding`, `literal_url`,
`finite_url_domain`, `request_payload_shape`, `django_url_declaration`,
`django_view_binding`, `django_query_call`, `external_boundary`,
`exact_endpoint`, `declared_path`, `configured_base`, and `dynamic_converter`.
Unresolved reasons are `dynamic_target_unproven`, `referenced_target_missing`,
`python_module_unproven`, `python_module_ambiguous`, `url_target_unmatched`,
`url_target_ambiguous`, and `unsupported_syntax`. Observed-only reasons are
`runtime_coherent_endpoint`, `runtime_coherent_view`, and
`runtime_coherent_resolution`.

Diagnostic codes are the fixed catalog:
`frontend_analyzer_unavailable`, `frontend_analyzer_failed`,
`frontend_analyzer_invalid_output`, `source_read_failed`, `unsupported_syntax`,
`unresolved_dynamic_target`, `unresolved_referenced_target`,
`unresolved_django_url`, `python_import_module_unresolved`,
`python_import_module_ambiguous`, `bounded_url_proof_invalid`,
`url_target_unmatched`, `url_target_ambiguous`, `runtime_capture_empty`,
`runtime_event_unmatched`, `runtime_event_ambiguous`, and
`runtime_identity_conflict`. Catalog validation supplies the exact
severity/message; producers never provide prose. Snapshot incompatibility codes
are `legacy_snapshot_incompatible`, `snapshot_schema_unsupported`,
`snapshot_repository_set_mismatch`, and `snapshot_manifest_mismatch`.

## Legal topology

Only these pairs are legal:

- `renders`: frontend_route→page/component; page/component→component.
- `contains`: page/component→ui_event/function.
- `handles`: ui_event→function/unresolved_target.
- `navigates_to`: page/component/function→frontend_route/unresolved_target.
- `calls`: page/component/function→http_call/function/external_service/unresolved_target; django_view→function/external_service/unresolved_target.
- `carries`: http_call→request_payload.
- `resolves_to`: http_call/request_payload→django_url_pattern/external_service/unresolved_target; django_url_pattern→django_view/unresolved_target.
- `invokes`: django_view/function→function/unresolved_target.
- `accesses`: django_view/function→query_boundary/unresolved_target; query_boundary→model/unresolved_target.
- `branches_to`: page/component/function/django_view→unresolved_target.

Every payload has exactly one incoming `carries` and one outgoing
`resolves_to`. A payload-bearing call has one or more carries and no direct
resolution; a no-payload call has no carries and exactly one direct resolution.
Unbounded calls resolve only to unresolved targets (through payloads when
present), have no endpoint ID, and undergo no candidate lookup.

## Bounded URL proof sidecar

`boundedUrlProofs` is an adapter-to-merge transport, not graph wire or
metadata. `GraphSnapshotV2.from_dict` rejects it. A fragment may carry at most
10,000 sorted records, each exactly:

```json
{"version":1,"callKey":"local-call","normalizedPath":"/items/{p0}","placeholders":[{"token":"p0","segmentIndex":1,"memberCount":2,"acceptedConverters":["int","str"]}]}
```

Proofs have no unknown/optional fields, are at most 8 KiB, bind one-for-one to
bounded `http_call` local keys, byte-match the call path, cover all contiguous
placeholder positions, use 1–32 ordered placeholders, count product ≤4096, and
list a sorted nonempty subset of `int|str|slug|uuid`. They contain no members,
expressions, identifiers, member hashes, examples, URLs with values, or secrets.
Malformed/missing/stale/wrong-kind proof rejects the whole adapter fragment and
emits only `bounded_url_proof_invalid`.

Canonicalization binds proof to recomputed call ID in a nonserializable envelope.
Merge considers only active-manifest Django patterns (and target repository when
specified), requiring exact template/position match, only built-in target
converters, proof acceptance for every converter, and exactly one candidate.
Only then it adds `endpointId` and a `dynamic_converter` edge. Zero/multiple,
path/custom, repository, endpoint, or converter mismatch creates unresolved
topology and fixed unmatched/ambiguous diagnostics. Merge destroys the proof
map before constructing the validated snapshot; save, SQLite, API, UI, logs,
and remerge cannot retain or recreate it.

## Static persistence, runtime overlay, and storage

Persisted snapshots and `analysis_runs` contain static evidence/diagnostics
only. Static validation/save occurs before a selected capture overlay. The
response-only overlay clones the validated graph; each event must coherently
prove its endpoint, optional view, their URL→view relationship, and unique
inbound frontend resolution before atomic `Observed` mutation. Empty,
unmatched, ambiguous, or conflicting events add one fixed transient diagnostic
and no observation. Runtime storage/read isolation binds active project,
runtime scope, and capture ID.

SQLite target version is `PRAGMA user_version = 2` with strict tables:
`runtime_events(row_id,event_id,project,runtime_scope_id,capture_id,received_at,payload)`,
`graph_snapshots(project,repository_set_id,schema_version,repository_manifest,payload,updated_at) WITHOUT ROWID`,
`analysis_runs(id,project,repository_set_id,schema_version,started_at,completed_at,status,diagnostics)`,
and `storage_quarantine`. The live keys are runtime active capture index,
`(project,repository_set_id)` snapshot primary key, active recent run index,
and quarantine uniqueness `(source_table,source_key_sha256,payload_sha256,reason)`.
All payload/manifest/diagnostics JSON columns require object/array as applicable;
schema version is exactly 2; timestamps are UTC milliseconds; quarantine reason
is one of `legacy_unscoped`, `legacy_incompatible`, `runtime_material`,
`identity_mismatch`, `manifest_mismatch`, `invalid_payload`, `unsafe_content`,
`invalid_timestamp`.

The normative DDL is:

```sql
CREATE TABLE runtime_events (
 row_id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) BETWEEN 36 AND 71),
 project TEXT NOT NULL CHECK (length(project) BETWEEN 1 AND 128),
 runtime_scope_id TEXT NOT NULL CHECK (length(runtime_scope_id) = 36),
 capture_id TEXT NOT NULL CHECK (length(capture_id) BETWEEN 1 AND 128),
 received_at TEXT NOT NULL CHECK (received_at GLOB '????-??-??T??:??:??.???Z'),
 payload TEXT NOT NULL CHECK (json_valid(payload) AND json_type(payload) = 'object')
) STRICT;
CREATE INDEX runtime_events_active_capture
 ON runtime_events(project, runtime_scope_id, capture_id, received_at, event_id);

CREATE TABLE graph_snapshots (
 project TEXT NOT NULL CHECK (length(project) BETWEEN 1 AND 128),
 repository_set_id TEXT NOT NULL CHECK (length(repository_set_id) = 64 AND lower(repository_set_id) = repository_set_id),
 schema_version INTEGER NOT NULL CHECK (schema_version = 2),
 repository_manifest TEXT NOT NULL CHECK (json_valid(repository_manifest) AND json_type(repository_manifest) = 'array'),
 payload TEXT NOT NULL CHECK (json_valid(payload) AND json_type(payload) = 'object'),
 updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
 PRIMARY KEY (project, repository_set_id)
) WITHOUT ROWID, STRICT;

CREATE TABLE analysis_runs (
 id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32),
 project TEXT NOT NULL CHECK (length(project) BETWEEN 1 AND 128),
 repository_set_id TEXT NOT NULL CHECK (length(repository_set_id) = 64 AND lower(repository_set_id) = repository_set_id),
 schema_version INTEGER NOT NULL CHECK (schema_version = 2),
 started_at TEXT NOT NULL CHECK (started_at GLOB '????-??-??T??:??:??.???Z'),
 completed_at TEXT NOT NULL CHECK (completed_at GLOB '????-??-??T??:??:??.???Z'),
 status TEXT NOT NULL CHECK (status IN ('complete','failed')),
 diagnostics TEXT NOT NULL CHECK (json_valid(diagnostics) AND json_type(diagnostics) = 'array')
) STRICT;
CREATE INDEX analysis_runs_active_recent
 ON analysis_runs(project, repository_set_id, completed_at, id);

CREATE TABLE storage_quarantine (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_table TEXT NOT NULL CHECK (source_table IN ('runtime_events','graph_snapshots','analysis_runs')),
 source_key_sha256 TEXT NOT NULL CHECK (length(source_key_sha256) = 64 AND lower(source_key_sha256) = source_key_sha256),
 payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64 AND lower(payload_sha256) = payload_sha256),
 project TEXT, repository_set_id TEXT, runtime_scope_id TEXT, capture_id TEXT,
 detected_schema_version INTEGER,
 reason TEXT NOT NULL CHECK (reason IN ('legacy_unscoped','legacy_incompatible','runtime_material','identity_mismatch','manifest_mismatch','invalid_payload','unsafe_content','invalid_timestamp')),
 safe_payload TEXT CHECK (safe_payload IS NULL OR (json_valid(safe_payload) AND json_type(safe_payload) IN ('object','array'))),
 quarantined_at TEXT NOT NULL CHECK (quarantined_at GLOB '????-??-??T??:??:??.???Z'),
 UNIQUE (source_table, source_key_sha256, payload_sha256, reason)
) STRICT;
CREATE INDEX storage_quarantine_scope
 ON storage_quarantine(source_table, project, repository_set_id, runtime_scope_id, capture_id);
PRAGMA user_version = 2;
```
Migration starts `BEGIN IMMEDIATE`, recognizes only the literal v1 tables,
reconstructs v2, verifies rows, swaps, sets user version, and commits. Any
failure rolls back all changes. A fully matching v2 database performs no write;
mixed/partial schemas are initialization errors. Legacy sentinels are
`__legacy_unscoped__`, `00000000-0000-0000-0000-000000000000`,
`legacy-unscoped`, and `1970-01-01T00:00:00.000Z`. Only a fully proven safe v1
static graph may enter the active set; runtime/Observed/incompatible legacy data
is quarantined.

Quarantine hashes original SQLite typed bytes before decode/JSON parsing:

```text
b"kg-debugger:qv2\x00" || frame(domain) || frame(columnName) || frame(sqliteType, exactValue)
```

A frame is tag + eight lowercase-hex byte length + `:` + bytes. Types are `N`
NULL, `I` canonical signed decimal, `R` big-endian binary64, `T` exact TEXT
bytes, and `B` exact BLOB bytes. Source domains/keys are
`source-key/runtime_events:id`, `source-key/graph_snapshots:project`, and
`source-key/analysis_runs:id`; payload domains/columns are respectively
`payload`, `payload`, and `diagnostics`. SQLite `typeof` plus `CAST AS BLOB`
recovers bytes. Invalid UTF-8, BLOB, NULL, non-JSON, scalar JSON, and nonfinite
values remain hash-only. Duplicate quarantine insertion is `ON CONFLICT ... DO
NOTHING`, preserving first timestamp/safe fields.

There is no downmigration: stop debugger and middleware processes, delete
`.kg-debugger/graph.sqlite3`, install the older binary, regenerate static
analysis, then restart.
