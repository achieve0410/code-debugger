from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kg_debugger.graph.merge import canonicalize_fragment, merge_canonical_fragments
from kg_debugger.graph.quarantine import (
    encode_quarantine_frames,
    payload_sha256,
    source_key_sha256,
)
from kg_debugger.graph.schema import GraphSnapshotV2
from kg_debugger.graph.store import (
    _TABLE_DDL,
    GraphStore,
    GraphStoreError,
    SnapshotIncompatible,
    SnapshotInvalid,
    SnapshotNotFound,
)


class QuarantineDigestProtocolTests(unittest.TestCase):
    def test_normative_fixed_vectors(self) -> None:
        cases = (
            (
                "runtime_events",
                "integer",
                1,
                "text",
                b"{}",
                "88ad9283f685c11cc3e099f3f708a21a84668e8c2db8acb0884b151cb10cf32d",
                "aea369ca252d8b79b9d1b35e1bd646cece50d0e4bff12623f6eff9f2dbe8d0af",
            ),
            (
                "runtime_events",
                "integer",
                2,
                "text",
                b"{",
                "f4a44908d8e741fd3b7d5e115b334e1b337577fc719fab6dfbfcce0e3e6908f9",
                "88344183fee5d7e0400d204ff46b759ae99dbb7f94da11703b5712dbe56b12af",
            ),
            (
                "graph_snapshots",
                "text",
                b"p",
                "text",
                b"{}",
                "0f3763fa2f29e0a4c2533166acf549cc9c45910d1bd3f5a9abe22b74f3c48650",
                "cb23fcdc8d01a51e4cb9728979a40bc4be42ef97b3f9f5aff0315087832cdf89",
            ),
            (
                "graph_snapshots",
                "text",
                b"bad",
                "null",
                None,
                "014eb36996fcd45ed2c345875620d50b319b6aa9be88e4de9cd57b7fbcab4cae",
                "6a3e7581d3b938357df826b104e868f80a37e891eca0bce2c2432367a044a35a",
            ),
            (
                "analysis_runs",
                "text",
                b"a",
                "text",
                b"[]",
                "8785e680a7d1c6cb08b7e02728a07fcb00d41692f9aed93081d3976e0e1e28e7",
                "fb31ea7e4008f2ba2aa2065b22165102385d1122148f17a05351f578a518ceb5",
            ),
            (
                "analysis_runs",
                "text",
                b"b",
                "text",
                b"x",
                "f891a24ea4530cc2a692fcbbcb2bd613a93b6652c80f6794da7ab009c4df10bb",
                "abefbed23fbc7322b30745b5eb60f83c941ab914af44fd42f21c021bc9344e0a",
            ),
        )
        for table, key_type, key, value_type, value, expected_key, expected_payload in cases:
            with self.subTest(table=table, key=key, value=value):
                self.assertEqual(source_key_sha256(table, key_type, key), expected_key)
                self.assertEqual(payload_sha256(table, value_type, value), expected_payload)

    def test_invalid_utf8_text_and_same_byte_blob_are_distinct(self) -> None:
        malformed_utf8 = bytes.fromhex("c328")
        self.assertEqual(
            payload_sha256("runtime_events", "text", malformed_utf8),
            "b7b2224229cc1bed8b41968f43f4f86b2ccb3b569c42a0958c336d94d9d2fe8f",
        )
        self.assertEqual(
            payload_sha256("runtime_events", "blob", malformed_utf8),
            "e2b947309c3bb8504e9d3bd79f51a90d1ef2647c31818c9c7b97b4d33077aae5",
        )
        self.assertNotEqual(
            payload_sha256("runtime_events", "text", malformed_utf8),
            payload_sha256("runtime_events", "blob", malformed_utf8),
        )

    def test_framing_types_domains_and_columns_are_sensitive(self) -> None:
        self.assertEqual(
            encode_quarantine_frames("domain", "column", "integer", -1),
            b"kg-debugger:qv2\x00K00000006:domainK00000006:columnI00000002:-1",
        )
        self.assertTrue(
            encode_quarantine_frames("domain", "column", "null", None).endswith(b"N00000000:")
        )
        self.assertTrue(
            encode_quarantine_frames("domain", "column", "real", 1.5).endswith(
                b"R00000008:\x3f\xf8\x00\x00\x00\x00\x00\x00"
            )
        )
        self.assertNotEqual(
            encode_quarantine_frames("domain", "column", "text", b"x"),
            encode_quarantine_frames("domain", "column", "blob", b"x"),
        )
        self.assertNotEqual(
            encode_quarantine_frames("one", "column", "text", b"x"),
            encode_quarantine_frames("two", "column", "text", b"x"),
        )
        self.assertNotEqual(
            encode_quarantine_frames("domain", "one", "text", b"x"),
            encode_quarantine_frames("domain", "two", "text", b"x"),
        )

    def test_payload_digest_preserves_whitespace_and_null_type(self) -> None:
        self.assertNotEqual(
            payload_sha256("analysis_runs", "text", b"[]"),
            payload_sha256("analysis_runs", "text", b"[ ]"),
        )
        self.assertNotEqual(
            payload_sha256("graph_snapshots", "null", None),
            payload_sha256("graph_snapshots", "text", b""),
        )

    def test_invalid_inputs_are_rejected(self) -> None:
        invalid_cases = (
            ("domain", "column", "missing", b"x"),
            ("domain", "column", "null", b"x"),
            ("domain", "column", "integer", True),
            ("domain", "column", "integer", 2**63),
            ("domain", "column", "real", 1),
            ("domain", "column", "text", "x"),
            ("domain", "column", "blob", bytearray(b"x")),
            ("", "column", "text", b"x"),
            ("domain", "", "text", b"x"),
        )
        for domain, column, sqlite_type, value in invalid_cases:
            with self.subTest(sqlite_type=sqlite_type, value=value):
                with self.assertRaises((TypeError, ValueError)):
                    encode_quarantine_frames(domain, column, sqlite_type, value)
        with self.assertRaises(ValueError):
            source_key_sha256("unknown", "integer", 1)
        with self.assertRaises(ValueError):
            payload_sha256("unknown", "text", b"payload")
class GraphStoreV2Tests(unittest.TestCase):
    project = "repo"
    repository_set_id = "a" * 64
    manifest = [{"namespace": "repo"}]

    def _store(self, directory: str) -> GraphStore:
        return GraphStore(
            Path(directory) / "graph.sqlite3",
            self.project,
            self.repository_set_id,
            self.manifest,
        )

    @staticmethod
    def _create_legacy_schema(path: Path) -> None:
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                """
                CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL);
                """
            )

    @staticmethod
    def _empty_legacy_snapshot(project: str = "repo") -> str:
        return json.dumps(
            {
                "project": project,
                "routes": [],
                "nodes": [],
                "edges": [],
                "diagnostics": [],
            }
        )
    def _bounded_dynamic_snapshot(self, candidate_count: int = 1) -> GraphSnapshotV2:
        frontend = {
            "adapter": "test_adapter",
            "adapterVersion": "1",
            "repository": "repo",
            "project": self.project,
            "repositorySetId": self.repository_set_id,
            "repositories": self.manifest,
            "nodes": [
                {
                    "key": "call",
                    "kind": "http_call",
                    "label": "GET /items/{p0}/",
                    "source": {"repository": "repo", "path": "client.ts"},
                    "metadata": {
                        "method": "GET",
                        "urlResolution": "bounded_template",
                        "normalizedPath": "/items/{p0}/",
                        "queryFieldCount": 0,
                        "hasSensitiveQuery": False,
                    },
                },
                {
                    "key": "unresolved",
                    "kind": "unresolved_target",
                    "label": "Unresolved",
                    "source": {"repository": "repo", "path": "client.ts"},
                    "metadata": {"reasonCode": "dynamic_target_unproven"},
                    "reason": "dynamic_target_unproven",
                    "evidenceKind": "unresolved",
                },
            ],
            "edges": [{
                "source": "call",
                "target": "unresolved",
                "kind": "resolves_to",
                "metadata": {"resolutionTier": "unbounded"},
                "reason": "dynamic_target_unproven",
                "evidenceKind": "unresolved",
            }],
            "boundedUrlProofs": [{
                "version": 1,
                "callKey": "call",
                "normalizedPath": "/items/{p0}/",
                "placeholders": [{
                    "token": "p0",
                    "segmentIndex": 1,
                    "memberCount": 1,
                    "acceptedConverters": ["int"],
                }],
            }],
        }
        django = {
            "adapter": "test_adapter",
            "adapterVersion": "1",
            "repository": "repo",
            "project": self.project,
            "repositorySetId": self.repository_set_id,
            "repositories": self.manifest,
            "nodes": [
                *[
                    {
                        "key": f"url{index}",
                        "kind": "django_url_pattern",
                        "label": f"url{index}",
                        "source": {"repository": "repo", "path": "urls.py"},
                        "metadata": {
                            "declaredPath": "/items/<int:item>/",
                            "normalizedPath": "/items/{p0}/",
                            "endpointId": "GET /items/{p0}/",
                            "converters": [{
                                "name": "item",
                                "kind": "int",
                                "segmentIndex": 1,
                            }],
                        },
                        "reason": "django_url_declaration",
                    }
                    for index in range(candidate_count)
                ],
                {
                    "key": "django-unresolved",
                    "kind": "unresolved_target",
                    "label": "Unresolved",
                    "source": {"repository": "repo", "path": "urls.py"},
                    "metadata": {"reasonCode": "dynamic_target_unproven"},
                    "reason": "dynamic_target_unproven",
                    "evidenceKind": "unresolved",
                },
            ],
            "edges": [
                {
                    "source": f"url{index}",
                    "target": "django-unresolved",
                    "kind": "resolves_to",
                    "metadata": {"resolutionTier": "unbounded"},
                    "reason": "dynamic_target_unproven",
                    "evidenceKind": "unresolved",
                }
                for index in range(candidate_count)
            ],
        }
        return merge_canonical_fragments(
            canonicalize_fragment(frontend),
            canonicalize_fragment(django),
            active_manifest=self.manifest,
        )

    def test_bounded_dynamic_snapshot_persistence_rejects_proof_bypass_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            snapshot = self._bounded_dynamic_snapshot()
            store.save_snapshot(snapshot)
            self.assertEqual(store.load_snapshot().to_dict(), snapshot.to_dict())

            unresolved = self._bounded_dynamic_snapshot(0).to_dict()
            unresolved_call = next(
                node for node in unresolved["nodes"] if node["kind"] == "http_call"
            )
            cases = (
                ("exact_endpoint", "exact_endpoint"),
                ("configured_base", "configured_base"),
                ("dynamic_unresolved_endpoint_residue", None),
            )
            for name, tier in cases:
                with self.subTest(name=name):
                    forged = copy.deepcopy(unresolved if tier is None else snapshot.to_dict())
                    if tier is not None:
                        edge = next(
                            item
                            for item in forged["edges"]
                            if item["kind"] == "resolves_to"
                            and item["metadata"]["resolutionTier"] == "dynamic_converter"
                        )
                        edge["metadata"]["resolutionTier"] = tier
                    else:
                        edge = next(
                            item
                            for item in forged["edges"]
                            if item["kind"] == "resolves_to"
                            and item["source"] == unresolved_call["id"]
                        )
                        terminal = next(
                            node for node in forged["nodes"] if node["id"] == edge["target"]
                        )
                        terminal["metadata"]["reasonCode"] = "dynamic_target_unproven"
                        terminal["evidence"][0]["reason"] = "dynamic_target_unproven"
                        edge["evidence"][0]["reason"] = "dynamic_target_unproven"
                        call = next(
                            node
                            for node in forged["nodes"]
                            if node["id"] == unresolved_call["id"]
                        )
                        self.assertNotIn("endpointId", call["metadata"])
                        GraphSnapshotV2.from_dict(forged)
                        call["metadata"]["endpointId"] = "GET /items/{p0}/"
                    with closing(sqlite3.connect(store.path)) as db, db:
                        db.execute(
                            "UPDATE graph_snapshots SET payload = ? WHERE project = ? AND repository_set_id = ?",
                            (json.dumps(forged), self.project, self.repository_set_id),
                        )
                    with self.assertRaises(SnapshotInvalid):
                        store.load_snapshot()

    def test_fresh_schema_and_active_scope_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            with closing(sqlite3.connect(store.path)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(
                    {row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )},
                    {"runtime_events", "graph_snapshots", "analysis_runs", "storage_quarantine"},
                )
                self.assertEqual(
                    {row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%'"
                    )},
                    {
                        "runtime_events_active_capture",
                        "analysis_runs_active_recent",
                        "storage_quarantine_scope",
                    },
                )
            with self.assertRaises(SnapshotNotFound):
                store.load_snapshot()
            scope = "11111111-1111-4111-8111-111111111111"
            store.add_runtime_event(
                "22222222-2222-4222-8222-222222222222",
                scope,
                "capture-a",
                {"captureId": "capture-a", "method": "GET", "path": "/"},
            )
            events = store.list_runtime_events(scope, "capture-a")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["eventId"], "22222222-2222-4222-8222-222222222222")
            self.assertEqual(events[0]["payload"], {"captureId": "capture-a", "method": "GET", "path": "/"})
            self.assertRegex(events[0]["receivedAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
            self.assertEqual(store.list_runtime_events(scope, "capture-b"), [])
    def test_runtime_capture_accepts_more_than_32_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            scope = "11111111-1111-4111-8111-111111111111"
            for index in range(33):
                store.add_runtime_event(
                    f"00000000-0000-4000-8000-{index:012x}",
                    scope,
                    "capture-many",
                    {"captureId": "capture-many", "method": "GET", "path": "/"},
                )
            self.assertEqual(len(store.list_runtime_events(scope, "capture-many")), 33)

    def test_legacy_runtime_uses_sentinels_and_invalid_payload_is_hash_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL);
                    """
                )
                db.execute(
                    "INSERT INTO runtime_events (run_id, payload, created_at) VALUES (?, ?, ?)",
                    ("old", json.dumps({"captureId": "old", "method": "GET", "path": "/"}), "2025-01-02 03:04:05"),
                )
                db.execute(
                    "INSERT INTO runtime_events (run_id, payload) VALUES (?, ?)",
                    ("bad", "{"),
                )
            store = self._store(directory)
            with closing(sqlite3.connect(path)) as db:
                event = db.execute(
                    "SELECT event_id, project, runtime_scope_id, capture_id, received_at FROM runtime_events"
                ).fetchone()
                self.assertEqual(
                    event,
                    (
                        "legacy-" + hashlib.sha256(
                            json.dumps(
                                [
                                    1,
                                    "old",
                                    json.dumps(
                                        {"captureId": "old", "method": "GET", "path": "/"}
                                    ),
                                    "2025-01-02 03:04:05",
                                ],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                        "__legacy_unscoped__",
                        "00000000-0000-0000-0000-000000000000",
                        "legacy-unscoped",
                        "2025-01-02T03:04:05.000Z",
                    ),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT safe_payload FROM storage_quarantine WHERE source_table='runtime_events' AND reason='invalid_payload'"
                    ).fetchone()[0],
                    None,
                )
            self.assertEqual(store.list_runtime_events("11111111-1111-4111-8111-111111111111", "capture-a"), [])
    def test_legacy_migration_quarantines_malformed_utf8_and_migrates_invalid_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL);
                    """
                )
                db.execute(
                    "INSERT INTO runtime_events (run_id, payload, created_at) VALUES (?, CAST(x'c328' AS TEXT), ?)",
                    ("bad-utf8", "2025-01-01 00:00:00"),
                )
                db.execute(
                    "INSERT INTO runtime_events (run_id, payload, created_at) VALUES (?, ?, ?)",
                    ("bad-time", json.dumps({"captureId": "old", "method": "GET", "path": "/"}), "not-a-time"),
                )
            self._store(directory)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT received_at FROM runtime_events WHERE capture_id = ?",
                        ("legacy-unscoped",),
                    ).fetchone()[0],
                    "1970-01-01T00:00:00.000Z",
                )
                self.assertEqual(
                    db.execute(
                        "SELECT reason FROM storage_quarantine WHERE source_table = 'runtime_events' ORDER BY id"
                    ).fetchall(),
                    [("invalid_payload",), ("invalid_timestamp",)],
                )
    def test_legacy_runtime_null_timestamp_uses_sentinel_and_canonical_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            payload = json.dumps({"captureId": "old", "method": "GET", "path": "/"})
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL);
                    """
                )
                db.execute(
                    "INSERT INTO runtime_events (run_id, payload, created_at) VALUES (?, ?, NULL)",
                    ("old", payload),
                )
            self._store(directory)
            expected_id = "legacy-" + hashlib.sha256(
                json.dumps([1, "old", payload, None], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT event_id, received_at FROM runtime_events"
                    ).fetchone(),
                    (expected_id, "1970-01-01T00:00:00.000Z"),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT safe_payload FROM storage_quarantine "
                        "WHERE source_table = 'runtime_events' AND reason = 'invalid_timestamp'"
                    ).fetchone(),
                    (None,),
                )

    def test_runtime_timestamp_type_matrix_preserves_json_identity_inputs(self) -> None:
        # Literal v1 timestamp columns have TEXT affinity, so INTEGER and REAL need
        # this no-affinity source fixture to exercise the migration's type boundary.
        payload = json.dumps({"captureId": "old", "method": "GET", "path": "/"})
        cases = (
            ("null", None, True, "invalid_timestamp"),
            ("invalid-text", "not-a-time", True, "invalid_timestamp"),
            ("integer", 7, True, "invalid_timestamp"),
            ("real", 1.5, True, "invalid_timestamp"),
            ("malformed-blob", sqlite3.Binary(b"\xc3("), False, "legacy_incompatible"),
        )
        for name, created_at, migrates, reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "graph.sqlite3"
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(
                        "CREATE TABLE runtime_events "
                        "(id INTEGER PRIMARY KEY, run_id, payload, created_at)"
                    )
                    db.execute(_TABLE_DDL["runtime_events"].format(
                        table="runtime_events_v2_new"
                    ))
                    db.execute(_TABLE_DDL["storage_quarantine"].format(
                        table="storage_quarantine_v2_new"
                    ))
                    db.execute(
                        "INSERT INTO runtime_events VALUES (?, ?, ?, ?)",
                        (1, "old", payload, created_at),
                    )
                    store = GraphStore.__new__(GraphStore)
                    store._migrate_runtime(db)
                    self.assertEqual(
                        list(map(tuple, db.execute(
                            "SELECT reason, safe_payload FROM storage_quarantine_v2_new"
                        ).fetchall())),
                        [(reason, None)],
                    )
                    if migrates:
                        expected_id = "legacy-" + hashlib.sha256(
                            json.dumps(
                                [1, "old", payload, created_at],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()
                        self.assertEqual(
                            tuple(db.execute(
                                "SELECT event_id, received_at FROM runtime_events_v2_new"
                            ).fetchone()),
                            (expected_id, "1970-01-01T00:00:00.000Z"),
                        )
                    else:
                        self.assertEqual(
                            tuple(db.execute(
                                "SELECT count(*) FROM runtime_events_v2_new"
                            ).fetchone()),
                            (0,),
                        )
    def test_legacy_runtime_incompatible_typed_value_is_hash_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL);
                    """
                )
                db.execute(
                    "INSERT INTO runtime_events (run_id, payload, created_at) VALUES (CAST(x'c328' AS TEXT), ?, ?)",
                    (json.dumps({"captureId": "old", "method": "GET", "path": "/"}), "2025-01-01 00:00:00"),
                )
            self._store(directory)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM runtime_events").fetchone(), (0,))
                self.assertEqual(
                    db.execute(
                        "SELECT reason, safe_payload FROM storage_quarantine "
                        "WHERE source_table = 'runtime_events'"
                    ).fetchone(),
                    ("legacy_incompatible", None),
                )

    def test_graph_snapshot_quarantine_reason_and_typed_byte_matrix(self) -> None:
        valid_snapshot = self._empty_legacy_snapshot()
        cases = (
            ("malformed-json", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", "{", "2025-01-01 00:00:00"), "invalid_payload", 0),
            ("scalar-json", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", "[]", "2025-01-01 00:00:00"), "legacy_incompatible", 0),
            ("runtime-material", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", json.dumps({"runtimeScopeId": "scope"}), "2025-01-01 00:00:00"), "runtime_material", 0),
            ("unsafe-content", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", json.dumps({"token": "secret-value"}), "2025-01-01 00:00:00"), "unsafe_content", 0),
            ("identity-mismatch", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", self._empty_legacy_snapshot("other"), "2025-01-01 00:00:00"), "identity_mismatch", 0),
            ("legacy-shape", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", json.dumps({"project": "repo"}), "2025-01-01 00:00:00"), "legacy_incompatible", 0),
            ("blob-payload", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", sqlite3.Binary(valid_snapshot.encode()), "2025-01-01 00:00:00"), "invalid_payload", 0),
            ("malformed-utf8", "INSERT INTO graph_snapshots VALUES (?, CAST(x'c328' AS TEXT), ?)", ("repo", "2025-01-01 00:00:00"), "invalid_payload", 0),
            ("invalid-time", "INSERT INTO graph_snapshots VALUES (?, ?, ?)", ("repo", valid_snapshot, None), "invalid_timestamp", 1),
        )
        for name, statement, parameters, expected_reason, expected_snapshots in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "graph.sqlite3"
                self._create_legacy_schema(path)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(statement, parameters)
                self._store(directory)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(
                        db.execute(
                            "SELECT reason, safe_payload FROM storage_quarantine"
                        ).fetchall(),
                        [(expected_reason, None)],
                    )
                    self.assertEqual(
                        db.execute("SELECT count(*) FROM graph_snapshots").fetchone()[0],
                        expected_snapshots,
                    )
                    if expected_snapshots:
                        self.assertEqual(
                            db.execute(
                                "SELECT updated_at FROM graph_snapshots"
                            ).fetchone()[0],
                            "1970-01-01T00:00:00.000Z",
                        )

    def test_snapshot_timestamp_type_matrix_uses_sentinel_or_hash_only(self) -> None:
        payload = self._empty_legacy_snapshot()
        cases = (
            ("null", None, True),
            ("invalid-text", "not-a-time", True),
            ("integer", 7, True),
            ("real", 1.5, True),
            ("malformed-blob", sqlite3.Binary(b"\xc3("), False),
        )
        for name, updated_at, migrates in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "graph.sqlite3"
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(
                        "CREATE TABLE graph_snapshots (project, payload, updated_at)"
                    )
                    db.execute(_TABLE_DDL["graph_snapshots"].format(
                        table="graph_snapshots_v2_new"
                    ))
                    db.execute(_TABLE_DDL["storage_quarantine"].format(
                        table="storage_quarantine_v2_new"
                    ))
                    db.execute(
                        "INSERT INTO graph_snapshots VALUES (?, ?, ?)",
                        ("repo", payload, updated_at),
                    )
                    store = GraphStore.__new__(GraphStore)
                    store.project = self.project
                    store.repository_set_id = self.repository_set_id
                    store.manifest = self.manifest
                    store._manifest_json = json.dumps(
                        self.manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    store._migrate_snapshots(db)
                    self.assertEqual(
                        list(map(tuple, db.execute(
                            "SELECT reason, safe_payload FROM storage_quarantine_v2_new"
                        ).fetchall())),
                        [(
                            "invalid_timestamp" if migrates else "legacy_incompatible",
                            None,
                        )],
                    )
                    self.assertEqual(
                        tuple(db.execute(
                            "SELECT count(*) FROM graph_snapshots_v2_new"
                        ).fetchone()),
                        (1 if migrates else 0,),
                    )
                    if migrates:
                        self.assertEqual(
                            tuple(db.execute(
                                "SELECT updated_at FROM graph_snapshots_v2_new"
                            ).fetchone()),
                            ("1970-01-01T00:00:00.000Z",),
                        )
    def test_analysis_run_quarantine_minimizes_only_valid_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            self._create_legacy_schema(path)
            with closing(sqlite3.connect(path)) as db, db:
                rows = (
                    ("safe", "repo", "2025-01-01 00:00:00", "complete", "[]"),
                    ("malformed", "repo", "2025-01-01 00:00:00", "complete", "{"),
                    ("scalar", "repo", "2025-01-01 00:00:00", "complete", "{}"),
                    ("blob", "repo", "2025-01-01 00:00:00", "complete", sqlite3.Binary(b"[]")),
                )
                db.executemany("INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?)", rows)
                db.execute(
                    "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, CAST(x'c328' AS TEXT))",
                    ("malformed-utf8", "repo", "2025-01-01 00:00:00", "complete"),
                )
            self._store(directory)
            safe_source = source_key_sha256("analysis_runs", "text", b"safe")
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute(
                    "SELECT source_key_sha256, reason, safe_payload "
                    "FROM storage_quarantine WHERE source_table = 'analysis_runs'"
                ).fetchall()
                self.assertEqual(len(rows), 5)
                self.assertEqual({reason for _, reason, _ in rows}, {"legacy_unscoped"})
                self.assertEqual(
                    [
                        json.loads(safe_payload)
                        for source_key, _, safe_payload in rows
                        if source_key == safe_source
                    ],
                    [[]],
                )
                self.assertTrue(
                    all(
                        safe_payload is None
                        for source_key, _, safe_payload in rows
                        if source_key != safe_source
                    )
                )
                self.assertEqual(
                    db.execute("SELECT count(*) FROM analysis_runs").fetchone()[0],
                    0,
                )

    def test_quarantine_conflict_keeps_first_audit_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            with closing(sqlite3.connect(store.path)) as db, db:
                db.execute(
                    "ALTER TABLE storage_quarantine "
                    "RENAME TO storage_quarantine_v2_new"
                )
                with patch(
                    "kg_debugger.graph.store._now",
                    return_value="2025-01-01T00:00:00.000Z",
                ):
                    store._quarantine(
                        db,
                        "analysis_runs",
                        "text",
                        b"same",
                        "text",
                        b"[]",
                        "legacy_unscoped",
                        project="first",
                        safe_payload=[],
                    )
                with patch(
                    "kg_debugger.graph.store._now",
                    return_value="2025-01-02T00:00:00.000Z",
                ):
                    store._quarantine(
                        db,
                        "analysis_runs",
                        "text",
                        b"same",
                        "text",
                        b"[]",
                        "legacy_unscoped",
                        project="second",
                    )
                    store._quarantine(
                        db,
                        "analysis_runs",
                        "text",
                        b"same",
                        "text",
                        b"[]",
                        "invalid_payload",
                    )
                    store._quarantine(
                        db,
                        "analysis_runs",
                        "text",
                        b"same",
                        "text",
                        b"[ ]",
                        "legacy_unscoped",
                    )
                rows = db.execute(
                    "SELECT reason, payload_sha256, project, safe_payload, quarantined_at "
                    "FROM storage_quarantine_v2_new ORDER BY id"
                ).fetchall()
                self.assertEqual(len(rows), 3)
                self.assertEqual(
                    rows[0],
                    (
                        "legacy_unscoped",
                        payload_sha256("analysis_runs", "text", b"[]"),
                        "first",
                        "[]",
                        "2025-01-01T00:00:00.000Z",
                    ),
                )
                self.assertEqual(
                    {(reason, digest) for reason, digest, *_ in rows[1:]},
                    {
                        (
                            "invalid_payload",
                            payload_sha256("analysis_runs", "text", b"[]"),
                        ),
                        (
                            "legacy_unscoped",
                            payload_sha256("analysis_runs", "text", b"[ ]"),
                        ),
                    },
                )
    def test_legacy_swap_rollback_preserves_original_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE runtime_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE graph_snapshots (project TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, diagnostics TEXT NOT NULL);
                    """
                )
            with patch.object(
                GraphStore,
                "_validate_reconstructed_content",
                side_effect=GraphStoreError("injected migration failure"),
            ), self.assertRaises(GraphStoreError):
                self._store(directory)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(),
                    [("analysis_runs",), ("graph_snapshots",), ("runtime_events",), ("sqlite_sequence",)],
                )

    def test_repeat_initialization_does_not_write_a_v2_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            with closing(sqlite3.connect(store.path)) as db:
                before = db.execute(
                    "SELECT count(*) FROM runtime_events, graph_snapshots, analysis_runs, storage_quarantine"
                ).fetchone()[0]
            restarted = self._store(directory)
            with closing(sqlite3.connect(restarted.path)) as db:
                after = db.execute(
                    "SELECT count(*) FROM runtime_events, graph_snapshots, analysis_runs, storage_quarantine"
                ).fetchone()[0]
            self.assertEqual((before, after), (0, 0))

    def test_mixed_schema_is_not_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE runtime_events (id INTEGER PRIMARY KEY)")
            with self.assertRaises(GraphStoreError):
                self._store(directory)

    def test_snapshot_is_bound_to_active_project_set_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            snapshot = GraphSnapshotV2(
                self.project,
                self.repository_set_id,
                self.manifest,
            )
            store.save_snapshot(snapshot)
            self.assertEqual(store.load_snapshot().to_dict(), snapshot.to_dict())
            other = GraphStore(
                store.path,
                "other",
                "b" * 64,
                [{"namespace": "other"}],
            )
            with self.assertRaises(SnapshotNotFound):
                other.load_snapshot()
    def test_manifest_mismatch_is_v2_load_incompatibility_not_v1_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.save_snapshot(
                GraphSnapshotV2(self.project, self.repository_set_id, self.manifest)
            )
            mismatched_manifest = GraphStore(
                store.path,
                self.project,
                self.repository_set_id,
                [{"namespace": "other"}],
            )
            with self.assertRaisesRegex(
                SnapshotIncompatible, "snapshot_manifest_mismatch"
            ):
                mismatched_manifest.load_snapshot()
            # V1 rows have no manifest; reconstruction cannot emit this reason.
            with closing(sqlite3.connect(store.path)) as db:
                self.assertNotIn(
                    "manifest_mismatch",
                    {
                        reason for reason, in db.execute(
                            "SELECT DISTINCT reason FROM storage_quarantine"
                        )
                    },
                )
