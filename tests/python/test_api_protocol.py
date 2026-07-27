from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import ssl
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from kg_debugger.app import DebuggerServer, build_server
from kg_debugger.config import DebuggerConfig
from kg_debugger.graph.merge import canonicalize_fragment
from kg_debugger.graph.schema import GraphSnapshotV2
from kg_debugger.http import normalize_endpoint


class EndpointIdentityProtocolTests(unittest.TestCase):
    def test_identity_contains_only_method_and_strict_path(self) -> None:
        self.assertEqual(normalize_endpoint("GET", "/orders/%7Eactive")["id"], "GET /orders/%7Eactive")

    def test_identity_rejects_ambiguous_paths(self) -> None:
        for path in ("//host/path", "/a/../b", "/a%2fb", "/a%5cb", "/a?b", "/a#b", "/a\\b", "/a%zz"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_endpoint("GET", path)

    def test_identity_rejects_proxy_material(self) -> None:
        with self.assertRaises(ValueError):
            normalize_endpoint("GET", "/safe", proxy_metadata={"forwarded": "untrusted"})
    def test_public_config_is_closed_and_never_exposes_absolute_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            external = Path(tempfile.mkdtemp()).resolve()
            try:
                config = DebuggerConfig.from_dict(
                    workspace,
                    {
                        "project": "example",
                        "repositories": [
                            {"namespace": "zeta", "path": "."},
                            {"namespace": "alpha", "path": str(external)},
                        ],
                    },
                )
                payload = config.to_dict()

                self.assertEqual(
                    payload,
                    {
                        "project": "example",
                        "runtimeEnabled": False,
                        "schemaVersion": 2,
                        "repositorySetId": config.repository_set_id,
                        "repositories": [
                            {"namespace": "alpha", "displayRoot": "external:alpha"},
                            {"namespace": "zeta", "displayRoot": "."},
                        ],
                        "repoRoots": ["external:alpha", "."],
                        "compatibilityWarnings": ["repoRoots_deprecated_v2"],
                    },
                )
                self.assertNotIn("endpoint", payload)
                self.assertNotIn("storePath", payload)
                self.assertNotIn("capability", payload)
                self.assertNotIn(str(workspace), repr(payload))
                self.assertNotIn(str(external), repr(payload))
            finally:
                external.rmdir()
class ApiProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.server = DebuggerServer(
            DebuggerConfig.from_dict(root, {"project": "repo", "repoRoots": ["."], "bindPort": 0})
        )
        self.server.scheme = "http"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.directory.cleanup()

    def _request(
        self, request: bytes, *, shutdown_write: bool = False, timeout: float = 5
    ) -> tuple[int, dict[str, list[str]], dict[str, object]]:
        with socket.create_connection(self.server.server_address, timeout=timeout) as connection:
            connection.sendall(request)
            if shutdown_write:
                connection.shutdown(socket.SHUT_WR)
            reader = connection.makefile("rb")
            status = int(reader.readline().split()[1])
            headers: dict[str, list[str]] = {}
            while line := reader.readline():
                if line == b"\r\n":
                    break
                name, value = line.decode("ascii").split(":", 1)
                headers.setdefault(name.lower(), []).append(value.strip())
            length = int(headers["content-length"][0])
            return status, headers, json.loads(reader.read(length))

    def _origin(self) -> bytes:
        return f"{self.server.scheme}://localhost:{self.server.server_address[1]}".encode()

    def _get(self, target: str, host: str | None = None) -> tuple[int, dict[str, list[str]], dict[str, object]]:
        host = host or f"localhost:{self.server.server_address[1]}"
        return self._request(f"GET {target} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())

    def _mutation(
        self,
        target: str,
        body: bytes,
        extra: bytes = b"",
        content_type: bytes = b"application/json",
    ) -> tuple[int, dict[str, list[str]], dict[str, object]]:
        port = self.server.server_address[1]
        return self._request(
            b"POST "
            + target.encode()
            + b" HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: "
            + self._origin()
            + b"\r\nX-KG-Debugger-Capability: "
            + self.server.capability.encode()
            + b"\r\nContent-Type: "
            + content_type
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n"
            + extra
            + b"\r\n"
            + body
        )
    def _raw_mutation(self, headers: bytes, body: bytes = b"") -> bytes:
        port = self.server.server_address[1]
        return (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: "
            + self._origin()
            + b"\r\nX-KG-Debugger-Capability: "
            + self.server.capability.encode()
            + b"\r\n"
            + headers
            + b"\r\n\r\n"
            + body
        )

    def test_public_api_bodies_headers_and_selectors_are_exact(self) -> None:
        status, headers, body = self._get("/api/health")
        self.assertEqual((status, body), (200, {"ok": True, "status": "ready"}))
        self.assertEqual(headers["cache-control"], ["no-store"])
        self.assertNotIn("access-control-allow-origin", headers)

        status, _, body = self._get("/api/graph")
        self.assertEqual((status, body), (404, {"error": "snapshot_not_found", "action": "analyze"}))
        status, _, body = self._get("/api/runtime")
        self.assertEqual((status, body), (404, {"error": "not_found"}))
        status, _, body = self._get("/api/unknown?selector=1")
        self.assertEqual((status, body), (400, {"error": "invalid_selector"}))

        status, _, body = self._get("/api/config")
        self.assertEqual(
            set(body),
            {
                "project",
                "runtimeEnabled",
                "schemaVersion",
                "repositorySetId",
                "repositories",
                "repoRoots",
                "compatibilityWarnings",
                "mutationCapability",
            },
        )
        self.assertEqual(body["mutationCapability"], self.server.capability)
        self.assertNotIn("runtimeScopeId", body)
        self.assertNotIn("endpoint", body)
        self.assertNotIn("storePath", body)

    def _dynamic_unresolved_snapshot(self) -> GraphSnapshotV2:
        repository = self.server.config.repository_manifest[0]["namespace"]
        fragment = canonicalize_fragment(
            {
                "adapter": "test_adapter",
                "adapterVersion": "1",
                "repository": repository,
                "project": self.server.config.project,
                "repositorySetId": self.server.config.repository_set_id,
                "repositories": self.server.config.repository_manifest,
                "nodes": [
                    {
                        "key": "call",
                        "kind": "http_call",
                        "label": "GET /items/",
                        "source": {"repository": repository, "path": "client.ts"},
                        "metadata": {
                            "method": "GET",
                            "urlResolution": "literal",
                            "normalizedPath": "/items/",
                            "endpointId": "GET /items/",
                            "queryFieldCount": 0,
                            "hasSensitiveQuery": False,
                        },
                    },
                    {
                        "key": "target",
                        "kind": "unresolved_target",
                        "label": "Unresolved",
                        "source": {"repository": repository, "path": "client.ts"},
                        "metadata": {"reasonCode": "dynamic_target_unproven"},
                        "reason": "dynamic_target_unproven",
                        "evidenceKind": "unresolved",
                    },
                ],
                "edges": [
                    {
                        "source": "call",
                        "target": "target",
                        "kind": "resolves_to",
                        "metadata": {"resolutionTier": "unbounded"},
                        "reason": "dynamic_target_unproven",
                        "evidenceKind": "unresolved",
                    }
                ],
            }
        )
        snapshot = fragment.snapshot.to_dict()
        next(node for node in snapshot["nodes"] if node["kind"] == "http_call")[
            "metadata"
        ].pop("endpointId")
        return GraphSnapshotV2.from_dict(snapshot)

    def test_graph_dynamic_unresolved_endpoint_residue_is_safe(self) -> None:
        store = self.server.orchestrator.store
        snapshot = self._dynamic_unresolved_snapshot()
        store.save_snapshot(snapshot)
        forged = snapshot.to_dict()
        call = next(node for node in forged["nodes"] if node["kind"] == "http_call")
        terminal = next(
            node for node in forged["nodes"] if node["kind"] == "unresolved_target"
        )
        edge = next(item for item in forged["edges"] if item["kind"] == "resolves_to")
        self.assertEqual(terminal["metadata"]["reasonCode"], "dynamic_target_unproven")
        self.assertEqual(terminal["evidence"][0]["reason"], "dynamic_target_unproven")
        self.assertEqual(edge["evidence"][0]["reason"], "dynamic_target_unproven")
        call["metadata"]["endpointId"] = "GET /items/"
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute(
                "UPDATE graph_snapshots SET payload = ? WHERE project = ? AND repository_set_id = ?",
                (
                    json.dumps(forged),
                    self.server.config.project,
                    self.server.config.repository_set_id,
                ),
            )

        status, headers, body = self._get("/api/graph")
        self.assertEqual(
            (status, body),
            (422, {"error": "snapshot_invalid", "action": "delete_or_reanalyze"}),
        )
        self.assertEqual(headers["content-type"], ["application/json"])
        self.assertEqual(headers["cache-control"], ["no-store"])
        self.assertNotIn("GET /items/", repr((headers, body)))
        self.assertNotIn("endpointId", repr((headers, body)))
    def test_raw_guards_framing_and_precedence_are_exact(self) -> None:
        port = self.server.server_address[1]
        status, _, body = self._request(b"GET /api/health HTTP/1.1\r\nHost: bad.test\r\n\r\n")
        self.assertEqual((status, body), (421, {"error": "misdirected_request"}))
        status, _, body = self._request(
            b"GET /api/health HTTP/1.1\r\nHost: bad.test:" + str(port).encode() + b"\r\n\r\n"
        )
        self.assertEqual((status, body), (421, {"error": "misdirected_request"}))
        status, _, body = self._request(b"GET /api/health HTTP/1.1\r\nHost: localhost:" + str(port).encode() + b"\r\nHost: localhost:" + str(port).encode() + b"\r\n\r\n")
        self.assertEqual((status, body), (400, {"error": "invalid_host_header"}))

        status, headers, body = self._mutation("/api/analyze", b"{}", b"Transfer-Encoding: chunked\r\n")
        self.assertEqual((status, body), (400, {"error": "unsupported_transfer_encoding"}))
        self.assertEqual(headers["connection"], ["close"])
        status, _, body = self._mutation("/api/analyze", b"{}", b"Content-Length: 02\r\n")
        self.assertEqual((status, body), (400, {"error": "invalid_content_length"}))

        status, _, body = self._get("/api/health?")
        self.assertEqual((status, body), (400, {"error": "invalid_selector"}))
        wrong_scheme = (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: "
            + ("https" if self.server.scheme == "http" else "http").encode()
            + b"://localhost:"
            + str(port).encode()
            + b"\r\nX-KG-Debugger-Capability: "
            + self.server.capability.encode()
            + b"\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        )
        status, _, body = self._request(wrong_scheme)
        self.assertEqual((status, body), (403, {"error": "origin_forbidden"}))

        request = (
            b"POST /api/analyze?x=1 HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: null\r\nX-KG-Debugger-Capability: wrong\r\n\r\n"
        )
        status, _, body = self._request(request)
        self.assertEqual((status, body), (403, {"error": "origin_forbidden"}))

    def test_authority_syntax_and_mutation_guard_precedence(self) -> None:
        port = self.server.server_address[1]
        for value in (
            "localhost:0",
            "localhost:01",
            "localhost:65536",
            "local_host:80",
            "localhost%2e:80",
            "[::1]:80",
            "127.0.0.1:80:81",
        ):
            with self.subTest(host=value):
                status, _, body = self._request(f"GET /api/health HTTP/1.1\r\nHost: {value}\r\n\r\n".encode())
                self.assertEqual((status, body), (400, {"error": "invalid_host_header"}))

        for value in (f"example.test:{port}", f"localhost:{port + 1}"):
            with self.subTest(host=value):
                status, _, body = self._request(f"GET /api/health HTTP/1.1\r\nHost: {value}\r\n\r\n".encode())
                self.assertEqual((status, body), (421, {"error": "misdirected_request"}))
        status, _, body = self._request(b"GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        self.assertEqual((status, body), (421, {"error": "misdirected_request"}))

        malformed_origin = (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: http://[::1]:"
            + str(port).encode()
            + b"\r\nX-KG-Debugger-Capability: wrong\r\n\r\n"
        )
        status, headers, body = self._request(malformed_origin)
        self.assertEqual((status, body), (400, {"error": "invalid_origin_header"}))
        self.assertEqual(headers["connection"], ["close"])

        forbidden_origin = (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: http://example.test:"
            + str(port).encode()
            + b"\r\nX-KG-Debugger-Capability: wrong\r\n\r\n"
        )
        status, _, body = self._request(forbidden_origin)
        self.assertEqual((status, body), (403, {"error": "origin_forbidden"}))

        missing_capability = (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: "
            + self._origin()
            + b"\r\n\r\n"
        )
        status, _, body = self._request(missing_capability)
        self.assertEqual((status, body), (403, {"error": "mutation_forbidden"}))
    def test_raw_content_length_transfer_and_connection_boundaries_are_exact(self) -> None:
        content_length_cases = (
            (b"Content-Type: application/json", b"", 411, "length_required"),
            (b"Content-Type: application/json\r\nContent-Length: 0", b"", 400, "invalid_content_length"),
            (b"Content-Type: application/json\r\nContent-Length: +2", b"{}", 400, "invalid_content_length"),
            (b"Content-Type: application/json\r\nContent-Length: two", b"{}", 400, "invalid_content_length"),
            (b"Content-Type: application/json\r\nContent-Length:\t2", b"{}", 400, "invalid_content_length"),
            (b"Content-Type: application/json\r\nContent-Length: 2, 2", b"{}", 400, "invalid_content_length"),
            (
                b"Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2",
                b"{}",
                400,
                "invalid_content_length",
            ),
        )
        for headers, body, expected_status, expected_error in content_length_cases:
            with self.subTest(headers=headers):
                status, response_headers, payload = self._request(self._raw_mutation(headers, body))
                self.assertEqual((status, payload), (expected_status, {"error": expected_error}))
                self.assertEqual(response_headers["connection"], ["close"])

        for transfer_encoding in (
            b"chunked",
            b"identity",
            b"gzip, chunked",
            b"chunked, gzip",
            b"gzip",
            b"gzip\r\nTransfer-Encoding: chunked",
        ):
            with self.subTest(transfer_encoding=transfer_encoding):
                status, response_headers, payload = self._request(
                    self._raw_mutation(
                        b"Content-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: "
                        + transfer_encoding,
                        b"{}",
                    )
                )
                self.assertEqual((status, payload), (400, {"error": "unsupported_transfer_encoding"}))
                self.assertEqual(response_headers["connection"], ["close"])

        short_body = self._raw_mutation(b"Content-Type: application/json\r\nContent-Length: 3", b"{}")
        if self.server.scheme == "http":
            status, response_headers, payload = self._request(short_body, shutdown_write=True)
            self.assertEqual((status, payload), (400, {"error": "invalid_json_body"}))
            self.assertEqual(response_headers["connection"], ["close"])

        status, response_headers, payload = self._request(short_body, timeout=7)
        self.assertEqual((status, payload), (408, {"error": "request_timeout"}))
        self.assertEqual(response_headers["connection"], ["close"])

        status, response_headers, payload = self._request(
            self._raw_mutation(b"Content-Type: application/json\r\nContent-Length: 2", b"{}trailing")
        )
        self.assertEqual(status, 200)
        self.assertNotIn("error", payload)
        self.assertEqual(response_headers["connection"], ["close"])

        status, response_headers, payload = self._request(
            self._raw_mutation(
                b"Content-Type: application/json\r\nContent-Length: 2",
                b"{}GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n",
            )
        )
        self.assertEqual(status, 200)
        self.assertNotIn("error", payload)
        self.assertEqual(response_headers["connection"], ["close"])

    def test_duplicate_origin_is_rejected_before_capability(self) -> None:
        status, response_headers, payload = self._request(
            self._raw_mutation(
                b"Origin: "
                + self._origin()
                + b"\r\nContent-Type: application/json\r\nContent-Length: 2",
                b"{}",
            )
        )
        self.assertEqual((status, payload), (400, {"error": "invalid_origin_header"}))
        self.assertEqual(response_headers["connection"], ["close"])

    def test_mutation_framing_rejections_do_not_invoke_orchestrator(self) -> None:
        analyze = Mock()
        self.server.orchestrator.analyze = analyze
        cases = (
            (b"Transfer-Encoding: chunked\r\n", 400, "unsupported_transfer_encoding"),
            (b"Content-Length: 2\r\n", 400, "invalid_content_length"),
            (b"Content-Type: text/plain\r\n", 415, "unsupported_media_type"),
        )
        for extra, expected_status, expected_error in cases:
            with self.subTest(extra=extra):
                status, headers, body = self._mutation("/api/analyze", b"{}", extra)
                self.assertEqual((status, body), (expected_status, {"error": expected_error}))
                self.assertEqual(headers["connection"], ["close"])
        analyze.assert_not_called()

        request = (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(self.server.server_address[1]).encode()
            + b"\r\nOrigin: "
            + self._origin()
            + b"\r\nX-KG-Debugger-Capability: "
            + self.server.capability.encode()
            + b"\r\nContent-Type: application/json\r\nContent-Length: 1048577\r\n\r\n"
        )
        status, headers, body = self._request(request)
        self.assertEqual((status, body), (413, {"error": "request_too_large"}))
        self.assertEqual(headers["connection"], ["close"])
        analyze.assert_not_called()

    def test_internal_value_error_is_a_safe_500_and_logs_do_not_include_target(self) -> None:
        def fail(_: object) -> object:
            raise ValueError("internal graph defect: secret-value")

        self.server.orchestrator.analyze = fail
        with patch("kg_debugger.app.LOGGER") as logger:
            status, headers, body = self._mutation("/api/analyze", b"{}")
            self.assertEqual((status, body), (500, {"error": "internal_error"}))
            self.assertEqual(headers["connection"], ["close"])

            self._get("/api/health?token=raw-client-secret")
            logged = " ".join(str(call) for call in logger.info.call_args_list)
            self.assertIn("route", logged)
            self.assertNotIn("raw-client-secret", logged)
    def test_unknown_method_uses_host_first_json_dispatch_and_safe_logging(self) -> None:
        port = self.server.server_address[1]
        with patch("kg_debugger.app.LOGGER") as logger:
            status, headers, body = self._request(b"BREW /api/health?secret=raw-token HTTP/1.1\r\nHost: bad.test\r\n\r\n")
            self.assertEqual((status, body), (421, {"error": "misdirected_request"}))
            self.assertEqual(headers["connection"], ["close"])

            status, headers, body = self._request(
                b"BREW /api/health HTTP/1.1\r\nHost: localhost:" + str(port).encode() + b"\r\n\r\n"
            )
            self.assertEqual((status, body), (403, {"error": "mutation_forbidden"}))
            self.assertEqual(headers["connection"], ["close"])
            logged = " ".join(str(call) for call in logger.info.call_args_list)
            self.assertIn("other", logged)
            self.assertNotIn("BREW", logged)
            self.assertNotIn("raw-token", logged)

    def test_capability_and_content_type_singletons_are_exact(self) -> None:
        port = self.server.server_address[1]
        empty_capability = (
            b"POST /api/analyze HTTP/1.1\r\nHost: localhost:"
            + str(port).encode()
            + b"\r\nOrigin: "
            + self._origin()
            + b"\r\nX-KG-Debugger-Capability: \r\n\r\n"
        )
        status, headers, body = self._request(empty_capability)
        self.assertEqual((status, body), (403, {"error": "mutation_forbidden"}))
        self.assertEqual(headers["connection"], ["close"])

        status, _, body = self._mutation(
            "/api/analyze",
            b"{}",
            b"X-KG-Debugger-Capability: duplicate\r\n",
        )
        self.assertEqual((status, body), (400, {"error": "invalid_capability_header"}))

        status, headers, body = self._mutation(
            "/api/analyze",
            b"{}",
            content_type=b"Application/JSON ; CHARSET = UTF-8",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["connection"], ["close"])
        self.assertNotIn("error", body)

        for content_type in (
            b"application/json; charset=utf-8; charset=utf-8",
            b"application/json; charset=iso-8859-1",
            b"application/json; boundary=ignored",
        ):
            with self.subTest(content_type=content_type):
                status, headers, body = self._mutation("/api/analyze", b"{}", content_type=content_type)
                self.assertEqual((status, body), (415, {"error": "unsupported_media_type"}))
                self.assertEqual(headers["connection"], ["close"])

        status, _, body = self._mutation(
            "/api/analyze",
            b"{}",
            b"Content-Type: application/json\r\n",
        )
        self.assertEqual((status, body), (415, {"error": "unsupported_media_type"}))
class TlsApiProtocolTests(ApiProtocolTests):
    """Run the raw protocol matrix through the production TLS listener."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        bootstrap_pem = Path(__file__).resolve().parents[2] / "pem"
        local_pem = root / "pem"
        local_pem.mkdir()
        shutil.copy2(bootstrap_pem / "cert.pem", local_pem / "cert.pem")
        shutil.copy2(bootstrap_pem / "key.pem", local_pem / "key.pem")
        self.server = build_server(
            DebuggerConfig.from_dict(root, {"project": "repo", "repoRoots": ["."], "bindPort": 0})
        )
        self.client_context = ssl.create_default_context(cafile=str(local_pem / "cert.pem"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _request(
        self, request: bytes, *, shutdown_write: bool = False, timeout: float = 5
    ) -> tuple[int, dict[str, list[str]], dict[str, object]]:
        with socket.create_connection(self.server.server_address, timeout=timeout) as raw_connection:
            with self.client_context.wrap_socket(raw_connection, server_hostname="localhost") as connection:
                connection.settimeout(timeout)
                connection.sendall(request)
                if shutdown_write:
                    connection.shutdown(socket.SHUT_WR)
                reader = connection.makefile("rb")
                status = int(reader.readline().split()[1])
                headers: dict[str, list[str]] = {}
                while line := reader.readline():
                    if line == b"\r\n":
                        break
                    name, value = line.decode("ascii").split(":", 1)
                    headers.setdefault(name.lower(), []).append(value.strip())
                length = int(headers["content-length"][0])
                return status, headers, json.loads(reader.read(length))
