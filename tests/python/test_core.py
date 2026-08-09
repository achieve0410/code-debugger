from __future__ import annotations

import json
import shutil
import ssl
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.request import urlopen

from kg_debugger.adapters.django.analyzer import analyze_django
from kg_debugger.app import DebuggerServer, build_server, config_from_args, parse_args
from kg_debugger.config import DebuggerConfig
from kg_debugger.graph.identity import edge_identity, node_identity
from kg_debugger.graph.schema import (
    Edge,
    Evidence,
    GraphSnapshotV2,
    Node,
    SourceLocation,
)
from kg_debugger.graph.store import GraphStore, SnapshotNotFound
from kg_debugger.orchestrator import Orchestrator
from kg_debugger.security import (
    SecurityError,
    reject_sensitive_config,
    require_loopback_url,
    resolve_analysis_root,
    resolve_repo_path,
    sanitize_trace_headers,
)


class GraphContractIntegrationTests(unittest.TestCase):
    def test_canonical_identity_is_stable_and_rejects_ambiguous_paths(self) -> None:
        identity = node_identity("repo", "app/views.py", "django_view", "home")
        self.assertEqual(identity, node_identity("repo", "app/views.py", "django_view", "home"))
        self.assertNotEqual(identity, node_identity("repo", "app/views.py", "django_view", "other"))
        for path in ("app/../views.py", "app\\views.py", "/app/views.py", "app//views.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                node_identity("repo", path, "django_view", "home")
    def test_django_identity_ignores_optional_module_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "app" / "__init__.py").write_text("", encoding="utf-8")
            (root / "app" / "views.py").write_text("def home(request): pass\n", encoding="utf-8")
            (root / "app" / "urls.py").write_text(
                "from django.urls import path\nfrom .views import home\nurlpatterns = [path('home/', home)]\n",
                encoding="utf-8",
            )
            first = analyze_django(root, "repo")
            (root / "manage.py").write_text("", encoding="utf-8")
            second = analyze_django(root, "repo")
        first_id = next(node["identity"] for node in first["nodes"] if node["kind"] == "django_view")
        second_id = next(node["identity"] for node in second["nodes"] if node["kind"] == "django_view")
        self.assertEqual(first_id, second_id)

    def test_payload_precedes_django_url_in_a_legal_v2_graph(self) -> None:
        evidence = [Evidence("inferred", "test", "1", "request_payload_shape")]
        call_source = SourceLocation("repo", "src/api.ts", line=1, symbol="load")
        payload_source = SourceLocation("repo", "src/api.ts", line=1, symbol="body")
        url_source = SourceLocation("repo", "urls.py", line=1, symbol="items")
        call_id = node_identity("repo", "src/api.ts", "http_call", "load")
        payload_id = node_identity("repo", "src/api.ts", "request_payload", "body")
        url_id = node_identity("repo", "urls.py", "django_url_pattern", "items")
        unresolved_id = node_identity("repo", "urls.py", "unresolved_target", "items-view")
        call = Node(
            call_id, "http_call", "load", "GET /api/items", "http", call_source,
            evidence, 0.8,
            {"method": "GET", "urlResolution": "literal", "normalizedPath": "/api/items", "endpointId": "GET /api/items", "queryFieldCount": 0, "hasSensitiveQuery": False},
        )
        payload = Node(
            payload_id, "request_payload", "body", "Request payload", "http", payload_source,
            evidence, 0.8,
            {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False},
        )
        url = Node(
            url_id, "django_url_pattern", "items", "URL pattern", "backend", url_source,
            [Evidence("inferred", "test", "1", "django_url_declaration")], 0.9,
            {"declaredPath": "/api/items", "normalizedPath": "/api/items", "endpointId": "GET /api/items", "converters": []},
        )
        unresolved = Node(
            unresolved_id, "unresolved_target", "items-view", "Unresolved",
            "unresolved", url_source,
            [Evidence("unresolved", "test", "1", "dynamic_target_unproven")], 0.3,
            {"reasonCode": "dynamic_target_unproven"},
        )
        carries = Edge(
            edge_identity(call_id, payload_id, "carries"), call_id, payload_id, "carries",
            evidence, 0.8, {"payloadKinds": ["body"]},
        )
        resolves = Edge(
            edge_identity(payload_id, url_id, "resolves_to"), payload_id, url_id, "resolves_to",
            [Evidence("inferred", "test", "1", "exact_endpoint")], 0.9,
            {"resolutionTier": "exact_endpoint"},
        )
        url_resolves = Edge(
            edge_identity(url_id, unresolved_id, "resolves_to"), url_id, unresolved_id,
            "resolves_to", [Evidence("unresolved", "test", "1", "dynamic_target_unproven")],
            0.3, {"resolutionTier": "unbounded"},
        )
        snapshot = GraphSnapshotV2(
            "project", "a" * 64, [{"namespace": "repo"}], [],
            sorted([call, payload, url, unresolved], key=lambda node: node.id),
            sorted([carries, resolves, url_resolves], key=lambda edge: edge.id),
        )
        snapshot.validate_persistable()
        self.assertFalse(any(edge.source == call_id and edge.target == url_id for edge in snapshot.edges))

class SecurityIntegrationTests(unittest.TestCase):
    def test_security_helpers_enforce_local_only_and_redact_trace_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(resolve_repo_path(root, ".").resolve(), root.resolve())
            with self.assertRaises(SecurityError):
                resolve_repo_path(root, "../outside")
        require_loopback_url("http://127.0.0.1:8000")
        require_loopback_url("https://localhost:8000")
        for url in ("https://example.test", "ftp://127.0.0.1:21"):
            with self.subTest(url=url), self.assertRaises(SecurityError):
                require_loopback_url(url)
        with self.assertRaises(SecurityError):
            reject_sensitive_config({"apiToken": "secret"})
        valid = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
        self.assertEqual(sanitize_trace_headers({"traceparent": valid}), {"traceparent": valid})
        for header in ({"tracestate": "user=user@example.test"}, {"baggage": "user@example.test"}):
            with self.subTest(header=header), self.assertRaises(SecurityError):
                sanitize_trace_headers(header)

    def test_external_analysis_root_requires_an_explicit_real_directory(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_tmp, tempfile.TemporaryDirectory() as external_tmp:
            workspace, external = Path(workspace_tmp), Path(external_tmp).resolve()
            self.assertEqual(resolve_analysis_root(workspace, external), external.resolve())
            with self.assertRaises(SecurityError):
                resolve_analysis_root(workspace, "../external")
            link = workspace / "external-link"
            link.symlink_to(external, target_is_directory=True)
            with self.assertRaises(SecurityError):
                resolve_analysis_root(workspace, link)


class DjangoAndFrontendIntegrationTests(unittest.TestCase):
    def test_django_malformed_continuation_does_not_block_qualified_import_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "urls.py").write_text(
                "from django.urls import path\nfrom app.views import items\nurlpatterns = [path('api/items/', items)]\n",
                encoding="utf-8",
            )
            (root / "app").mkdir()
            (root / "app" / "__init__.py").write_text("", encoding="utf-8")
            (root / "app" / "views.py").write_text(
                "from .services import list_items\n\ndef items(request):\n    return list_items()\n",
                encoding="utf-8",
            )
            (root / "app" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            (root / "app" / "services.py").write_text(
                "def list_items():\n    return None\n", encoding="utf-8"
            )
            config = DebuggerConfig.from_dict(
                root, {"project": "fixture", "repoRoots": ["."], "storePath": "graph.sqlite3"}
            )
            snapshot = Orchestrator(config, "11111111-1111-4111-8111-111111111111").analyze()
        self.assertTrue(any(node.source.path == "app/services.py" and node.label == "list_items" for node in snapshot.nodes))
        self.assertTrue(any(item["code"] == "unsupported_syntax" for item in snapshot.diagnostics))

    def test_real_react_and_vue_django_fixtures_are_analyzed(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        for fixture, framework in (("fixtures/react-django", "react"), ("fixtures/vue-django", "vue")):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory(
                prefix=".tmp-core-fixture-",
                dir=workspace,
            ) as directory:
                store_path = Path(directory).relative_to(workspace) / "graph.sqlite3"
                config = DebuggerConfig.from_dict(
                    workspace,
                    {
                        "project": fixture.rsplit("/", 1)[-1],
                        "repoRoots": [fixture],
                        "storePath": store_path.as_posix(),
                    },
                )
                snapshot = Orchestrator(config, "11111111-1111-4111-8111-111111111111").analyze()
                self.assertTrue(any(route["framework"] == framework for route in snapshot.routes))
                kinds = {node.id: node.kind for node in snapshot.nodes}
                edges = {(edge.source, edge.target, edge.kind) for edge in snapshot.edges}

                def has_edge(source_kind: str, target_kind: str, edge_kind: str) -> bool:
                    return any(kinds.get(source) == source_kind and kinds.get(target) == target_kind and kind == edge_kind
                               for source, target, kind in edges)

                self.assertTrue(has_edge("frontend_route", "page", "renders") or has_edge("frontend_route", "component", "renders"))
                self.assertTrue(has_edge("ui_event", "function", "handles"))
                self.assertTrue(has_edge("function", "http_call", "calls"))
                self.assertTrue(has_edge("http_call", "request_payload", "carries"))
                self.assertTrue(has_edge("request_payload", "django_url_pattern", "resolves_to"))
                self.assertTrue(has_edge("django_url_pattern", "django_view", "resolves_to"))
                self.assertTrue(has_edge("django_view", "function", "invokes"))
                self.assertTrue(has_edge("function", "query_boundary", "accesses"))
                self.assertTrue(has_edge("query_boundary", "model", "accesses"))
                self.assertTrue(has_edge("function", "external_service", "calls") or any(
                    node.kind == "unresolved_target" for node in snapshot.nodes
                ))


class StoreAndRuntimeIntegrationTests(unittest.TestCase):
    def test_store_round_trip_is_bound_to_active_project_set_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite3"
            snapshot = GraphSnapshotV2("repo", "a" * 64, [{"namespace": "repo"}])
            store = GraphStore(path, "repo", "a" * 64, [{"namespace": "repo"}])
            store.save_snapshot(snapshot)
            self.assertEqual(store.load_snapshot().to_dict(), snapshot.to_dict())
            other = GraphStore(path, "other", "b" * 64, [{"namespace": "other"}])
            with self.assertRaises(SnapshotNotFound):
                other.load_snapshot()
    def test_runtime_events_return_server_provenance_for_exact_scope_and_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(
                Path(directory) / "graph.sqlite3",
                "repo",
                "a" * 64,
                [{"namespace": "repo"}],
            )
            scope = "11111111-1111-4111-8111-111111111111"
            other_scope = "22222222-2222-4222-8222-222222222222"
            first = "33333333-3333-4333-8333-333333333333"
            second = "44444444-4444-4444-8444-444444444444"
            store.add_runtime_event(
                second,
                scope,
                "capture-a",
                {"captureId": "capture-a", "method": "GET", "path": "/second/"},
                received_at="2026-01-01T00:00:00.000Z",
            )
            store.add_runtime_event(
                first,
                scope,
                "capture-a",
                {"captureId": "capture-a", "method": "GET", "path": "/first/"},
                received_at="2026-01-01T00:00:00.000Z",
            )
            store.add_runtime_event(
                "55555555-5555-4555-8555-555555555555",
                other_scope,
                "capture-a",
                {"captureId": "capture-a", "method": "GET", "path": "/other-scope/"},
            )
            store.add_runtime_event(
                "66666666-6666-4666-8666-666666666666",
                scope,
                "capture-b",
                {"captureId": "capture-b", "method": "GET", "path": "/other-capture/"},
            )
            events = store.list_runtime_events(scope, "capture-a")
        self.assertEqual(
            events,
            [
                {
                    "eventId": first,
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {"captureId": "capture-a", "method": "GET", "path": "/first/"},
                },
                {
                    "eventId": second,
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {"captureId": "capture-a", "method": "GET", "path": "/second/"},
                },
            ],
        )

    def test_static_snapshot_persists_without_ephemeral_runtime_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "urls.py").write_text(
                "from django.urls import path\nfrom views import items\nurlpatterns = [path('api/items/', items)]\n",
                encoding="utf-8",
            )
            (root / "views.py").write_text("def items(request):\n    return None\n", encoding="utf-8")
            config = DebuggerConfig.from_dict(
                root,
                {
                    "project": "fixture",
                    "repoRoots": ["."],
                    "storePath": "graph.sqlite3",
                    "runtimeEnabled": True,
                },
            )
            scope = "11111111-1111-4111-8111-111111111111"
            orchestrator = Orchestrator(config, scope)
            event_id, _ = orchestrator.record_runtime_event(
                {"captureId": "capture-1", "method": "GET", "path": "/api/items/"}
            )
            overlay = orchestrator.analyze("capture-1")
            persisted = orchestrator.store.load_snapshot()
        self.assertTrue(
            any(
                evidence.eventId == event_id
                for node in overlay.nodes
                for evidence in node.evidence
                if evidence.kind == "observed"
            )
        )
        self.assertFalse(
            any(evidence.kind == "observed" for node in persisted.nodes for evidence in node.evidence)
        )


class ServerAndCliIntegrationTests(unittest.TestCase):
    def _server_config(self, root: Path, *, runtime: bool = False) -> DebuggerConfig:
        (root / "web").mkdir()
        (root / "web" / "index.html").write_text("<main>ok</main>", encoding="utf-8")
        return DebuggerConfig.from_dict(
            root,
            {
                "project": "repo",
                "repoRoots": ["."],
                "storePath": "graph.sqlite3",
                "bindPort": 0,
                "runtimeEnabled": runtime,
            },
        )

    def _request(
        self,
        server,
        method: str,
        target: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request(method, target, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def test_loopback_http_server_enforces_host_origin_capability_framing_and_api_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = DebuggerServer(self._server_config(Path(directory)))
            server.scheme = "http"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, headers, body = self._request(server, "GET", "/api/health")
                self.assertEqual((status, body), (200, {"ok": True, "status": "ready"}))
                self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
                self.assertEqual(headers["referrer-policy"], "no-referrer")

                status, _, body = self._request(server, "GET", "/api/graph")
                self.assertEqual((status, body), (404, {"error": "snapshot_not_found", "action": "analyze"}))
                status, _, body = self._request(server, "OPTIONS", "/api/health")
                self.assertEqual((status, body), (405, {"error": "method_not_allowed"}))
                status, _, body = self._request(server, "GET", "/api/health?legacy=1")
                self.assertEqual((status, body), (400, {"error": "invalid_selector"}))
                status, _, body = self._request(
                    server,
                    "GET",
                    "/api/health",
                    headers={"Host": f"example.test:{server.server_address[1]}"},
                )
                self.assertEqual((status, body), (421, {"error": "misdirected_request"}))

                mutation_headers = {
                    "Origin": f"http://localhost:{server.server_address[1]}",
                    "Content-Type": "application/json",
                }
                status, _, body = self._request(server, "POST", "/api/analyze", headers=mutation_headers, body="{}")
                self.assertEqual((status, body), (403, {"error": "mutation_forbidden"}))

                mutation_headers["X-KG-Debugger-Capability"] = server.capability
                mutation_headers["Origin"] = "null"
                status, _, body = self._request(server, "POST", "/api/analyze", headers=mutation_headers, body="{}")
                self.assertEqual((status, body), (403, {"error": "origin_forbidden"}))

                mutation_headers["Origin"] = f"http://localhost:{server.server_address[1]}"
                mutation_headers["Content-Type"] = "text/plain"
                status, _, body = self._request(server, "POST", "/api/analyze", headers=mutation_headers, body="{}")
                self.assertEqual((status, body), (415, {"error": "unsupported_media_type"}))
                mutation_headers["Content-Type"] = "application/json"
                mutation_headers["Content-Length"] = "0"
                status, _, body = self._request(server, "POST", "/api/analyze", headers=mutation_headers, body="{}")
                self.assertEqual((status, body), (400, {"error": "invalid_content_length"}))
                del mutation_headers["Content-Length"]

                mutation_headers["Content-Type"] = "application/json"
                status, _, body = self._request(server, "POST", "/api/runtime", headers=mutation_headers, body="{}")
                self.assertEqual((status, body), (403, {"error": "runtime_disabled"}))
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_https_health_uses_project_certificate_with_verification(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._server_config(root)
            (root / "pem").mkdir()
            shutil.copy2(workspace / "pem" / "cert.pem", root / "pem" / "cert.pem")
            shutil.copy2(workspace / "pem" / "key.pem", root / "pem" / "key.pem")
            server = build_server(config)
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            try:
                context = ssl.create_default_context(cafile=str(workspace / "pem" / "cert.pem"))
                with urlopen(
                    f"https://localhost:{server.server_address[1]}/api/health",
                    context=context,
                    timeout=5,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), {"ok": True, "status": "ready"})
            finally:
                thread.join(timeout=5)
                server.server_close()

    def test_cli_named_external_repositories_have_safe_display_roots(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_tmp, tempfile.TemporaryDirectory() as project_tmp:
            workspace, project = Path(workspace_tmp), Path(project_tmp).resolve()
            frontend, backend = project / "frontend", project / "backend"
            frontend.mkdir()
            backend.mkdir()
            config = config_from_args(
                parse_args(
                    [
                        "--repo",
                        f"frontend={frontend}",
                        "--repo",
                        f"backend={backend}",
                    ]
                ),
                workspace,
            )
            self.assertEqual(
                [(item.namespace, item.resolved_root) for item in config.repositories],
                [("backend", backend.resolve()), ("frontend", frontend.resolve())],
            )
            self.assertEqual(
                config.to_dict()["repositories"],
                [
                    {"namespace": "backend", "displayRoot": "external:backend"},
                    {"namespace": "frontend", "displayRoot": "external:frontend"},
                ],
            )
            serialized = json.dumps(config.to_dict())
            self.assertNotIn(str(project.resolve()), serialized)
            self.assertEqual(config.bind_port, 8443)
    def test_cli_rejects_plaintext_http_mode(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--http"])


if __name__ == "__main__":
    unittest.main()
