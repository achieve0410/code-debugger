from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..http import EndpointConfig
from ..runtime import (
    RuntimeEventValidationError,
    validate_capture_id,
    validate_runtime_event,
)
from .contracts import (
    NAMESPACE_RE,
    reject_secret_material,
    validate_diagnostic,
    validate_millisecond_utc,
    validate_server_event_id,
)
from .identity import edge_identity, node_identity, route_identity
from .quarantine import payload_sha256, source_key_sha256
from .schema import GraphSnapshotV2

LEGACY_PROJECT = "__legacy_unscoped__"
LEGACY_RUNTIME_SCOPE_ID = "00000000-0000-0000-0000-000000000000"
LEGACY_CAPTURE_ID = "legacy-unscoped"
LEGACY_TIME = "1970-01-01T00:00:00.000Z"


class GraphStoreError(ValueError):
    """Base class for storage boundary errors."""


class SnapshotNotFound(GraphStoreError):
    code = "snapshot_not_found"


class SnapshotIncompatible(GraphStoreError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SnapshotInvalid(GraphStoreError):
    code = "snapshot_invalid"



_TABLE_DDL = {
    "runtime_events": """CREATE TABLE {table} (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE
        CHECK (length(event_id) BETWEEN 36 AND 71),
    project TEXT NOT NULL
        CHECK (length(project) BETWEEN 1 AND 128),
    runtime_scope_id TEXT NOT NULL
        CHECK (length(runtime_scope_id) = 36),
    capture_id TEXT NOT NULL
        CHECK (length(capture_id) BETWEEN 1 AND 128),
    received_at TEXT NOT NULL
        CHECK (received_at GLOB '????-??-??T??:??:??.???Z'),
    payload TEXT NOT NULL
        CHECK (json_valid(payload) AND json_type(payload) = 'object')
) STRICT""",
    "graph_snapshots": """CREATE TABLE {table} (
    project TEXT NOT NULL CHECK (length(project) BETWEEN 1 AND 128),
    repository_set_id TEXT NOT NULL
        CHECK (length(repository_set_id) = 64 AND lower(repository_set_id) = repository_set_id),
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    repository_manifest TEXT NOT NULL
        CHECK (json_valid(repository_manifest) AND json_type(repository_manifest) = 'array'),
    payload TEXT NOT NULL
        CHECK (json_valid(payload) AND json_type(payload) = 'object'),
    updated_at TEXT NOT NULL
        CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    PRIMARY KEY (project, repository_set_id)
) WITHOUT ROWID, STRICT""",
    "analysis_runs": """CREATE TABLE {table} (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32),
    project TEXT NOT NULL CHECK (length(project) BETWEEN 1 AND 128),
    repository_set_id TEXT NOT NULL
        CHECK (length(repository_set_id) = 64 AND lower(repository_set_id) = repository_set_id),
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    started_at TEXT NOT NULL
        CHECK (started_at GLOB '????-??-??T??:??:??.???Z'),
    completed_at TEXT NOT NULL
        CHECK (completed_at GLOB '????-??-??T??:??:??.???Z'),
    status TEXT NOT NULL CHECK (status IN ('complete','failed')),
    diagnostics TEXT NOT NULL
        CHECK (json_valid(diagnostics) AND json_type(diagnostics) = 'array')
) STRICT""",
    "storage_quarantine": """CREATE TABLE {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL
        CHECK (source_table IN ('runtime_events','graph_snapshots','analysis_runs')),
    source_key_sha256 TEXT NOT NULL
        CHECK (length(source_key_sha256) = 64 AND lower(source_key_sha256) = source_key_sha256),
    payload_sha256 TEXT NOT NULL
        CHECK (length(payload_sha256) = 64 AND lower(payload_sha256) = payload_sha256),
    project TEXT,
    repository_set_id TEXT,
    runtime_scope_id TEXT,
    capture_id TEXT,
    detected_schema_version INTEGER,
    reason TEXT NOT NULL CHECK (reason IN (
        'legacy_unscoped','legacy_incompatible','runtime_material',
        'identity_mismatch','manifest_mismatch','invalid_payload',
        'unsafe_content','invalid_timestamp'
    )),
    safe_payload TEXT
        CHECK (safe_payload IS NULL OR
               (json_valid(safe_payload) AND json_type(safe_payload) IN ('object','array'))),
    quarantined_at TEXT NOT NULL
        CHECK (quarantined_at GLOB '????-??-??T??:??:??.???Z'),
    UNIQUE (source_table, source_key_sha256, payload_sha256, reason)
) STRICT""",
}
_INDEX_DDL = (
    "CREATE INDEX runtime_events_active_capture ON runtime_events(project, runtime_scope_id, capture_id, received_at, event_id)",
    "CREATE INDEX analysis_runs_active_recent ON analysis_runs(project, repository_set_id, completed_at, id)",
    "CREATE INDEX storage_quarantine_scope ON storage_quarantine(source_table, project, repository_set_id, runtime_scope_id, capture_id)",
)
_LEGACY_COLUMNS = {
    "runtime_events": (("id", "INTEGER", 0, 1), ("run_id", "TEXT", 1, 0), ("payload", "TEXT", 1, 0), ("created_at", "TEXT", 0, 0)),
    "graph_snapshots": (("project", "TEXT", 0, 1), ("payload", "TEXT", 1, 0), ("updated_at", "TEXT", 0, 0)),
    "analysis_runs": (("id", "TEXT", 0, 1), ("project", "TEXT", 1, 0), ("created_at", "TEXT", 0, 0), ("status", "TEXT", 1, 0), ("diagnostics", "TEXT", 1, 0)),
}
_LEGACY_DDL = {
    "runtime_events": "CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "graph_snapshots": "CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "analysis_runs": "CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL)",
}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LEGACY_EVENT_ID_RE = re.compile(r"^legacy-[0-9a-f]{64}$")
_UUID_V4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", value):
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        elif re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        else:
            return None
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _bytes_to_text(value: Any) -> str | None:
    if not isinstance(value, bytes):
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _contains_runtime_material(value: Any) -> bool:
    if isinstance(value, dict):
        runtime_keys = {
            "captureId",
            "eventId",
            "receivedAt",
            "runId",
            "runtimeScopeId",
            "timestamp",
        }
        return (
            value.get("kind") == "observed"
            or bool(runtime_keys & set(value))
            or any(_contains_runtime_material(child) for child in value.values())
        )
    if isinstance(value, list):
        return any(_contains_runtime_material(child) for child in value)
    return False



class GraphStore:
    def __init__(self, path: str | Path, project: str, repository_set_id: str, manifest: list[dict[str, str]], *, endpoint_config: EndpointConfig | None = None) -> None:
        try:
            if (
                not isinstance(project, str)
                or not 1 <= len(project) <= 128
                or project != unicodedata.normalize("NFC", project)
                or any(ord(char) < 32 or ord(char) == 127 for char in project)
            ):
                raise ValueError
            reject_secret_material(project)
        except ValueError as exc:
            raise GraphStoreError("invalid project") from exc
        if not isinstance(repository_set_id, str) or not re.fullmatch(r"[0-9a-f]{64}", repository_set_id):
            raise GraphStoreError("invalid repository set")
        if (
            not isinstance(manifest, list)
            or not 1 <= len(manifest) <= 64
            or any(
                not isinstance(item, dict)
                or set(item) != {"namespace"}
                or not isinstance(item["namespace"], str)
                or not NAMESPACE_RE.fullmatch(item["namespace"])
                for item in manifest
            )
        ):
            raise GraphStoreError("invalid repository manifest")
        canonical_manifest = sorted(manifest, key=lambda item: item["namespace"])
        if manifest != canonical_manifest or len({item["namespace"].casefold() for item in manifest}) != len(manifest):
            raise GraphStoreError("invalid repository manifest")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.project = project
        self.repository_set_id = repository_set_id
        self.manifest = canonical_manifest
        self.endpoint_config = endpoint_config
        self._manifest_json = _canonical(canonical_manifest)
        self._init()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _table_columns(self, db: sqlite3.Connection, table: str) -> tuple[tuple[str, str, int, int], ...]:
        return tuple((row[1], row[2].upper(), row[3], row[5]) for row in db.execute(f"PRAGMA table_info({table})"))

    def _tables(self, db: sqlite3.Connection) -> set[str]:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

    def _schema_objects(self, db: sqlite3.Connection) -> dict[tuple[str, str], str | None]:
        return {
            (row[0], row[1]): row[2]
            for row in db.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%'"
            )
        }

    @staticmethod
    def _normal_sql(statement: str) -> str:
        return re.sub(r"\s+", "", statement).lower().rstrip(";")
    @staticmethod
    def _same_v2_sql(actual: str, expected: str) -> bool:
        normalized_actual = GraphStore._normal_sql(actual)
        normalized_expected = GraphStore._normal_sql(expected)
        for identifier in (*_TABLE_DDL, *(statement.split()[2] for statement in _INDEX_DDL)):
            normalized_actual = normalized_actual.replace(f'"{identifier}"', identifier)
        return normalized_actual == normalized_expected

    def _is_legacy(self, db: sqlite3.Connection) -> bool:
        objects = self._schema_objects(db)
        expected = {("table", table) for table in _LEGACY_COLUMNS}
        return (
            set(objects) == expected
            and all(self._table_columns(db, table) == columns for table, columns in _LEGACY_COLUMNS.items())
            and all(
                self._normal_sql(objects[("table", table)] or "")
                == self._normal_sql(statement)
                for table, statement in _LEGACY_DDL.items()
            )
            and db.execute("PRAGMA user_version").fetchone()[0] == 0
        )

    def _is_v2(self, db: sqlite3.Connection) -> bool:
        objects = self._schema_objects(db)
        expected = {("table", table) for table in _TABLE_DDL} | {
            ("index", statement.split()[2]) for statement in _INDEX_DDL
        }
        if set(objects) != expected:
            return False
        if any(
            not self._same_v2_sql(
                objects[("table", table)] or "", statement.format(table=table)
            )
            for table, statement in _TABLE_DDL.items()
        ):
            return False
        if any(
            not self._same_v2_sql(
                objects[("index", statement.split()[2])] or "", statement
            )
            for statement in _INDEX_DDL
        ):
            return False
        return db.execute("PRAGMA user_version").fetchone()[0] == 2

    def _init(self) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            tables = self._tables(db)
            if not tables:
                db.execute("BEGIN IMMEDIATE")
                self._create_live_schema(db)
                db.execute("PRAGMA user_version = 2")
                db.commit()
                return
            if self._is_v2(db):
                return
            if tables != set(_LEGACY_COLUMNS) or not self._is_legacy(db):
                raise GraphStoreError("mixed or unsupported graph storage schema")
            db.execute("BEGIN IMMEDIATE")
            try:
                self._reconstruct_legacy(db)
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _create_live_schema(self, db: sqlite3.Connection) -> None:
        for table, statement in _TABLE_DDL.items():
            db.execute(statement.format(table=table))
        for statement in _INDEX_DDL:
            db.execute(statement)

    def _reconstruct_legacy(self, db: sqlite3.Connection) -> None:
        source_counts = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in _LEGACY_COLUMNS
        }
        for table, statement in _TABLE_DDL.items():
            db.execute(statement.format(table=f"{table}_v2_new"))
        migrated = {
            "runtime_events": self._migrate_runtime(db),
            "graph_snapshots": self._migrate_snapshots(db),
            "analysis_runs": self._migrate_runs(db),
        }
        self._verify_accounting(db, source_counts, migrated)
        self._validate_reconstructed_content(db)
        for table in _LEGACY_COLUMNS:
            db.execute(f"ALTER TABLE {table} RENAME TO {table}_v1_old")
            db.execute(f"ALTER TABLE {table}_v2_new RENAME TO {table}")
        db.execute("ALTER TABLE storage_quarantine_v2_new RENAME TO storage_quarantine")
        for statement in _INDEX_DDL:
            db.execute(statement)
        for table in _LEGACY_COLUMNS:
            db.execute(f"DROP TABLE {table}_v1_old")
        db.execute("PRAGMA user_version = 2")
        if not self._is_v2(db):
            raise GraphStoreError("reconstructed schema validation failed")

    def _verify_accounting(
        self, db: sqlite3.Connection, source_counts: dict[str, int],
        migrated: dict[str, set[str]],
    ) -> None:
        for table, count in source_counts.items():
            quarantined_by_reason: dict[str, set[str]] = {}
            for source_key, reason in db.execute(
                "SELECT source_key_sha256, reason FROM storage_quarantine_v2_new "
                "WHERE source_table = ?", (table,)
            ):
                quarantined_by_reason.setdefault(reason, set()).add(source_key)
            quarantined = set().union(*quarantined_by_reason.values())
            overlap = migrated[table] & quarantined
            if (
                overlap - quarantined_by_reason.get("invalid_timestamp", set())
                or len(migrated[table] | quarantined) != count
            ):
                raise GraphStoreError("legacy row accounting failed")

    def _validate_reconstructed_content(self, db: sqlite3.Connection) -> None:
        for event_id, payload in db.execute(
            "SELECT event_id, payload FROM runtime_events_v2_new"
        ):
            if not _LEGACY_EVENT_ID_RE.fullmatch(event_id):
                raise GraphStoreError("invalid reconstructed runtime event")
            validate_runtime_event(json.loads(payload))
        for project, repository_set_id, manifest, payload in db.execute(
            "SELECT project, repository_set_id, repository_manifest, payload "
            "FROM graph_snapshots_v2_new"
        ):
            snapshot = GraphSnapshotV2.from_dict(json.loads(payload))
            snapshot.validate_persistable()
            if (
                project != self.project
                or repository_set_id != self.repository_set_id
                or manifest != self._manifest_json
                or snapshot.project != project
                or snapshot.repositorySetId != repository_set_id
                or snapshot.repositories != self.manifest
            ):
                raise GraphStoreError("invalid reconstructed snapshot")
        if db.execute("SELECT count(*) FROM analysis_runs_v2_new").fetchone()[0]:
            raise GraphStoreError("legacy analysis run was reconstructed")
        for table, safe_payload in db.execute(
            "SELECT source_table, safe_payload FROM storage_quarantine_v2_new "
            "WHERE safe_payload IS NOT NULL"
        ):
            parsed = json.loads(safe_payload)
            if table == "graph_snapshots":
                snapshot = GraphSnapshotV2.from_dict(parsed)
                snapshot.validate_persistable()
            elif table == "analysis_runs":
                if not isinstance(parsed, list):
                    raise GraphStoreError("invalid safe diagnostics")
                for diagnostic in parsed:
                    validate_diagnostic(diagnostic, persistable=True)
            else:
                raise GraphStoreError("runtime quarantine payload is not permitted")

    @staticmethod
    def _digest_argument(sqlite_type: str, value: Any) -> Any:
        if sqlite_type in {"text", "blob", "null"}:
            return value
        if not isinstance(value, bytes):
            raise GraphStoreError("legacy SQLite value bytes are unavailable")
        try:
            return int(value) if sqlite_type == "integer" else float(value)
        except ValueError as exc:
            raise GraphStoreError("legacy SQLite value is malformed") from exc

    def _legacy_digest(self, table: str, key_type: str, key: Any, payload_type: str, payload: Any) -> tuple[str, str]:
        return (
            source_key_sha256(table, key_type, self._digest_argument(key_type, key)),
            payload_sha256(table, payload_type, self._digest_argument(payload_type, payload)),
        )

    def _quarantine(self, db: sqlite3.Connection, table: str, key_type: str, key: Any, payload_type: str, payload: Any, reason: str, *, source_digest: str | None = None, payload_digest: str | None = None, project: str | None = None, repository_set_id: str | None = None, safe_payload: Any | None = None) -> str:
        source_digest = source_digest or source_key_sha256(
            table, key_type, self._digest_argument(key_type, key)
        )
        payload_digest = payload_digest or payload_sha256(
            table, payload_type, self._digest_argument(payload_type, payload)
        )
        encoded_safe_payload = _canonical(safe_payload) if safe_payload is not None else None
        db.execute(
            "INSERT INTO storage_quarantine_v2_new (source_table, source_key_sha256, payload_sha256, project, repository_set_id, detected_schema_version, reason, safe_payload, quarantined_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?) ON CONFLICT(source_table, source_key_sha256, payload_sha256, reason) DO NOTHING",
            (table, source_digest, payload_digest, project, repository_set_id, reason,
             encoded_safe_payload, _now()),
        )
        return source_digest

    @staticmethod
    def _legacy_value(row: sqlite3.Row, name: str) -> Any:
        sqlite_type, raw = row[f"{name}_type"], row[f"{name}_bytes"]
        if sqlite_type == "null":
            return None
        if sqlite_type == "text":
            return _bytes_to_text(raw)
        if sqlite_type == "blob":
            return raw
        try:
            return int(raw) if sqlite_type == "integer" else float(raw)
        except (TypeError, ValueError) as exc:
            raise GraphStoreError("legacy SQLite value is malformed") from exc

    @classmethod
    def _legacy_json_value(cls, row: sqlite3.Row, name: str) -> tuple[Any, bool]:
        """Decode a legacy SQLite value only when it can enter canonical JSON."""
        try:
            value = cls._legacy_value(row, name)
            if row[f"{name}_type"] == "text" and value is None:
                return None, False
            _canonical(value)
        except (GraphStoreError, TypeError, ValueError):
            return None, False
        return value, True

    def _legacy_rows(self, db: sqlite3.Connection, table: str, key: str, payload: str, extras: tuple[str, ...]) -> Iterator[sqlite3.Row]:
        db.row_factory = sqlite3.Row
        columns = (key, payload, *extras)
        projection = ", ".join(
            f"typeof({column}) AS {column}_type, CAST({column} AS BLOB) AS {column}_bytes"
            for column in columns
        )
        return iter(db.execute(f"SELECT {projection} FROM {table}"))

    def _migrate_runtime(self, db: sqlite3.Connection) -> set[str]:
        migrated: set[str] = set()
        for row in self._legacy_rows(db, "runtime_events", "id", "payload", ("run_id", "created_at")):
            source_digest, payload_digest = self._legacy_digest(
                "runtime_events", row["id_type"], row["id_bytes"],
                row["payload_type"], row["payload_bytes"],
            )
            values: dict[str, Any] = {}
            valid: dict[str, bool] = {}
            for column in ("id", "run_id", "payload", "created_at"):
                values[column], valid[column] = self._legacy_json_value(row, column)
            if not all(valid.values()):
                reason = "invalid_payload" if not valid["payload"] else "legacy_incompatible"
                self._quarantine(
                    db, "runtime_events", row["id_type"], row["id_bytes"],
                    row["payload_type"], row["payload_bytes"], reason,
                    source_digest=source_digest, payload_digest=payload_digest,
                )
                continue
            try:
                event_id = "legacy-" + hashlib.sha256(
                    _canonical([
                        values["id"], values["run_id"], values["payload"],
                        values["created_at"],
                    ]).encode("utf-8")
                ).hexdigest()
                payload = json.loads(values["payload"]) if isinstance(values["payload"], str) else None
                if not isinstance(payload, dict):
                    raise ValueError("runtime payload is not an object")
                _canonical(payload)
            except (ValueError, TypeError, json.JSONDecodeError):
                self._quarantine(db, "runtime_events", row["id_type"], row["id_bytes"], row["payload_type"], row["payload_bytes"], "invalid_payload", source_digest=source_digest, payload_digest=payload_digest)
                continue
            try:
                reject_secret_material(payload)
            except ValueError:
                self._quarantine(db, "runtime_events", row["id_type"], row["id_bytes"], row["payload_type"], row["payload_bytes"], "unsafe_content", source_digest=source_digest, payload_digest=payload_digest)
                continue
            try:
                canonical_payload = validate_runtime_event(payload).payload
            except RuntimeEventValidationError:
                self._quarantine(db, "runtime_events", row["id_type"], row["id_bytes"], row["payload_type"], row["payload_bytes"], "legacy_incompatible", source_digest=source_digest, payload_digest=payload_digest)
                continue
            timestamp = _timestamp(values["created_at"])
            if timestamp is None:
                timestamp = LEGACY_TIME
                self._quarantine(db, "runtime_events", row["id_type"], row["id_bytes"], row["payload_type"], row["payload_bytes"], "invalid_timestamp", source_digest=source_digest, payload_digest=payload_digest)
            db.execute("INSERT INTO runtime_events_v2_new (event_id, project, runtime_scope_id, capture_id, received_at, payload) VALUES (?, ?, ?, ?, ?, ?)", (event_id, LEGACY_PROJECT, LEGACY_RUNTIME_SCOPE_ID, LEGACY_CAPTURE_ID, timestamp, _canonical(canonical_payload)))
            migrated.add(source_digest)
        return migrated

    def _convert_legacy_snapshot(self, payload: Any) -> GraphSnapshotV2:
        """Convert only the frozen, single-root GraphSnapshot wire shape."""
        if (
            not isinstance(payload, dict)
            or set(payload) != {"project", "routes", "nodes", "edges", "diagnostics"}
            or payload["project"] != self.project
            or len(self.manifest) != 1
            or self.manifest[0]["namespace"] != self.project
            or not all(isinstance(payload[name], list) for name in ("routes", "nodes", "edges", "diagnostics"))
        ):
            raise ValueError("legacy identity proof failed")

        nodes: list[dict[str, Any]] = []
        legacy_node_ids: set[str] = set()
        for old in payload["nodes"]:
            if not isinstance(old, dict) or set(old) != {
                "id", "kind", "label", "layer", "source", "evidence", "confidence", "metadata"
            }:
                raise ValueError("invalid legacy node")
            source = old["source"]
            if not isinstance(source, dict) or set(source) - {"path", "line", "endLine", "symbol"} or "path" not in source:
                raise ValueError("invalid legacy source")
            identity_key = source.get("symbol") if isinstance(source.get("symbol"), str) else old["label"]
            if not isinstance(identity_key, str) or old["id"] != node_identity(
                self.project, source["path"], old["kind"], identity_key
            ):
                raise ValueError("legacy node identity mismatch")
            legacy_node_ids.add(old["id"])
            nodes.append({
                **old,
                "identityKey": identity_key,
                "source": {"repository": self.project, **source},
            })

        for old in payload["edges"]:
            if not isinstance(old, dict) or set(old) != {
                "id", "source", "target", "kind", "evidence", "confidence", "metadata"
            } or old["id"] != edge_identity(old["source"], old["target"], old["kind"]):
                raise ValueError("legacy edge identity mismatch")

        routes: list[dict[str, Any]] = []
        for old in payload["routes"]:
            if not isinstance(old, dict) or set(old) - {"id", "key", "framework", "path", "nodeId"}:
                raise ValueError("invalid legacy route")
            if not {"framework", "path", "nodeId"} <= set(old) or old["nodeId"] not in legacy_node_ids:
                raise ValueError("invalid legacy route")
            route_id = route_identity(self.project, old["framework"], old["path"], old["nodeId"])
            if "id" in old and old["id"] != route_id:
                raise ValueError("legacy route identity mismatch")
            routes.append({
                "id": route_id,
                "repository": self.project,
                "framework": old["framework"],
                "path": old["path"],
                "nodeId": old["nodeId"],
            })

        converted = {
            "schemaVersion": 2,
            "project": self.project,
            "repositorySetId": self.repository_set_id,
            "repositories": self.manifest,
            "routes": routes,
            "nodes": nodes,
            "edges": payload["edges"],
            "diagnostics": payload["diagnostics"],
        }
        snapshot = GraphSnapshotV2.from_dict(converted)
        snapshot.validate_persistable()
        return snapshot

    def _migrate_snapshots(self, db: sqlite3.Connection) -> set[str]:
        migrated: set[str] = set()
        for row in self._legacy_rows(db, "graph_snapshots", "project", "payload", ("updated_at",)):
            source_digest, payload_digest = self._legacy_digest("graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"])
            project, valid_project = self._legacy_json_value(row, "project")
            text, valid_payload = self._legacy_json_value(row, "payload")
            updated_at, valid_timestamp = self._legacy_json_value(row, "updated_at")
            if not all((valid_project, valid_payload, valid_timestamp)):
                reason = "invalid_payload" if not valid_payload else "legacy_incompatible"
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], reason, source_digest=source_digest, payload_digest=payload_digest)
                continue
            try:
                parsed = json.loads(text) if text is not None else None
            except (TypeError, json.JSONDecodeError):
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], "invalid_payload", source_digest=source_digest, payload_digest=payload_digest)
                continue
            if _contains_runtime_material(parsed):
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], "runtime_material", source_digest=source_digest, payload_digest=payload_digest)
                continue
            try:
                reject_secret_material(parsed)
            except ValueError:
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], "unsafe_content", source_digest=source_digest, payload_digest=payload_digest)
                continue
            try:
                snapshot = self._convert_legacy_snapshot(parsed)
            except ValueError:
                reason = "identity_mismatch" if isinstance(parsed, dict) and (project != self.project or parsed.get("project") != self.project) else "legacy_incompatible"
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], reason, source_digest=source_digest, payload_digest=payload_digest)
                continue
            if project != self.project:
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], "identity_mismatch", source_digest=source_digest, payload_digest=payload_digest)
                continue
            timestamp = _timestamp(updated_at)
            if timestamp is None:
                timestamp = LEGACY_TIME
                self._quarantine(db, "graph_snapshots", row["project_type"], row["project_bytes"], row["payload_type"], row["payload_bytes"], "invalid_timestamp", source_digest=source_digest, payload_digest=payload_digest)
            db.execute("INSERT INTO graph_snapshots_v2_new (project, repository_set_id, schema_version, repository_manifest, payload, updated_at) VALUES (?, ?, 2, ?, ?, ?)", (self.project, self.repository_set_id, self._manifest_json, _canonical(snapshot.to_dict()), timestamp))
            migrated.add(source_digest)
        return migrated

    def _migrate_runs(self, db: sqlite3.Connection) -> set[str]:
        for row in self._legacy_rows(db, "analysis_runs", "id", "diagnostics", ("project", "created_at", "status")):
            source_digest, payload_digest = self._legacy_digest("analysis_runs", row["id_type"], row["id_bytes"], row["diagnostics_type"], row["diagnostics_bytes"])
            text = self._legacy_value(row, "diagnostics") if row["diagnostics_type"] == "text" else None
            try:
                diagnostics = json.loads(text) if text is not None else None
                if not isinstance(diagnostics, list):
                    raise ValueError("diagnostics are not a list")
                for diagnostic in diagnostics:
                    validate_diagnostic(diagnostic, persistable=True)
            except (ValueError, TypeError, json.JSONDecodeError):
                self._quarantine(db, "analysis_runs", row["id_type"], row["id_bytes"], row["diagnostics_type"], row["diagnostics_bytes"], "legacy_unscoped", source_digest=source_digest, payload_digest=payload_digest)
            else:
                self._quarantine(db, "analysis_runs", row["id_type"], row["id_bytes"], row["diagnostics_type"], row["diagnostics_bytes"], "legacy_unscoped", source_digest=source_digest, payload_digest=payload_digest, safe_payload=diagnostics)
        return set()

    def save_snapshot(self, snapshot: GraphSnapshotV2, *, updated_at: str | None = None) -> None:
        try:
            snapshot.validate_persistable()
        except ValueError as exc:
            raise SnapshotInvalid() from exc
        if snapshot.project != self.project or snapshot.repositorySetId != self.repository_set_id:
            raise SnapshotIncompatible("snapshot_repository_set_mismatch")
        if snapshot.repositories != self.manifest:
            raise SnapshotIncompatible("snapshot_manifest_mismatch")
        timestamp = _timestamp(updated_at) if updated_at is not None else _now()
        if timestamp is None:
            raise GraphStoreError("invalid timestamp")
        with self._connect() as db:
            db.execute("INSERT INTO graph_snapshots (project, repository_set_id, schema_version, repository_manifest, payload, updated_at) VALUES (?, ?, 2, ?, ?, ?) ON CONFLICT(project, repository_set_id) DO UPDATE SET schema_version=excluded.schema_version, repository_manifest=excluded.repository_manifest, payload=excluded.payload, updated_at=excluded.updated_at", (self.project, self.repository_set_id, self._manifest_json, _canonical(snapshot.to_dict()), timestamp))

    def load_snapshot(self) -> GraphSnapshotV2:
        with self._connect() as db:
            row = db.execute("SELECT repository_manifest, payload FROM graph_snapshots WHERE project = ? AND repository_set_id = ?", (self.project, self.repository_set_id)).fetchone()
        if row is None:
            raise SnapshotNotFound()
        try:
            stored_manifest = json.loads(row[0])
            if (
                not isinstance(stored_manifest, list)
                or _canonical(stored_manifest) != row[0]
            ):
                raise ValueError("invalid stored manifest")
            snapshot_payload = json.loads(row[1])
            if not isinstance(snapshot_payload, dict):
                raise ValueError("invalid stored snapshot")
            snapshot = GraphSnapshotV2.from_dict(snapshot_payload)
            snapshot.validate_persistable()
        except (TypeError, ValueError, KeyError, AttributeError, json.JSONDecodeError) as exc:
            raise SnapshotInvalid() from exc
        if stored_manifest != self.manifest or snapshot.repositories != self.manifest:
            raise SnapshotIncompatible("snapshot_manifest_mismatch")
        if snapshot.project != self.project or snapshot.repositorySetId != self.repository_set_id:
            raise SnapshotIncompatible("snapshot_repository_set_mismatch")
        if snapshot.repositories != self.manifest:
            raise SnapshotIncompatible("snapshot_manifest_mismatch")
        return snapshot

    def save_run(self, run_id: str, status: str, diagnostics: list[dict[str, Any]], *, started_at: str | None = None, completed_at: str | None = None) -> None:
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id) or status not in {"complete", "failed"}:
            raise GraphStoreError("invalid analysis run")
        if not isinstance(diagnostics, list):
            raise GraphStoreError("invalid diagnostics")
        try:
            for diagnostic in diagnostics:
                validate_diagnostic(diagnostic, persistable=True)
        except ValueError as exc:
            raise GraphStoreError("invalid diagnostics") from exc
        started = _timestamp(started_at) if started_at is not None else _now()
        completed = _timestamp(completed_at) if completed_at is not None else _now()
        if started is None or completed is None:
            raise GraphStoreError("invalid timestamp")
        with self._connect() as db:
            db.execute("INSERT INTO analysis_runs (id, project, repository_set_id, schema_version, started_at, completed_at, status, diagnostics) VALUES (?, ?, ?, 2, ?, ?, ?, ?)", (run_id, self.project, self.repository_set_id, started, completed, status, _canonical(diagnostics)))

    def add_runtime_event(self, event_id: str, runtime_scope_id: str, capture_id: str, payload: dict[str, Any], *, received_at: str | None = None) -> None:
        if not isinstance(event_id, str) or not _UUID_V4_RE.fullmatch(event_id):
            raise GraphStoreError("invalid event id")
        if not isinstance(runtime_scope_id, str) or not _UUID_V4_RE.fullmatch(runtime_scope_id):
            raise GraphStoreError("invalid runtime scope")
        if not isinstance(payload, dict):
            raise GraphStoreError("invalid runtime payload")
        try:
            canonical_payload = validate_runtime_event(
                payload, endpoint_config=self.endpoint_config
            ).payload
            canonical_capture_id = validate_capture_id(capture_id)
        except RuntimeEventValidationError as exc:
            raise GraphStoreError("invalid runtime payload") from exc
        if canonical_payload["captureId"] != canonical_capture_id:
            raise GraphStoreError("runtime capture does not match payload")
        try:
            encoded = _canonical(canonical_payload)
        except (TypeError, ValueError) as exc:
            raise GraphStoreError("invalid runtime payload") from exc
        try:
            timestamp = (
                validate_millisecond_utc(received_at)
                if received_at is not None
                else _now()
            )
        except ValueError as exc:
            raise GraphStoreError("invalid timestamp") from exc
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO runtime_events (event_id, project, runtime_scope_id, capture_id, received_at, payload) VALUES (?, ?, ?, ?, ?, ?)", (event_id, self.project, runtime_scope_id, canonical_capture_id, timestamp, encoded))

    def list_runtime_events(self, runtime_scope_id: str, capture_id: str) -> list[dict[str, Any]]:
        if not isinstance(runtime_scope_id, str) or not _UUID_V4_RE.fullmatch(runtime_scope_id):
            raise GraphStoreError("invalid runtime scope")
        try:
            canonical_capture_id = validate_capture_id(capture_id)
        except RuntimeEventValidationError as exc:
            raise GraphStoreError("invalid runtime scope") from exc
        with self._connect() as db:
            rows = db.execute("SELECT event_id, received_at, payload FROM runtime_events WHERE project = ? AND runtime_scope_id = ? AND capture_id = ? ORDER BY received_at, event_id", (self.project, runtime_scope_id, canonical_capture_id)).fetchall()
        try:
            events: list[dict[str, Any]] = []
            for event_id, received_at, encoded_payload in rows:
                if not isinstance(event_id, str) or not _UUID_V4_RE.fullmatch(event_id):
                    raise ValueError("invalid event id")
                validate_server_event_id(event_id)
                timestamp = validate_millisecond_utc(received_at)
                payload = json.loads(encoded_payload)
                event = validate_runtime_event(
                    payload, endpoint_config=self.endpoint_config
                ).payload
                if (
                    event["captureId"] != canonical_capture_id
                    or _canonical(event) != encoded_payload
                ):
                    raise ValueError("runtime row disagreement")
                events.append({
                    "eventId": event_id,
                    "receivedAt": timestamp,
                    "payload": event,
                })
            return events
        except (TypeError, ValueError, json.JSONDecodeError, RuntimeEventValidationError) as exc:
            raise GraphStoreError("invalid stored runtime event") from exc
