from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kg_debugger.graph.identity import edge_identity, node_identity
from kg_debugger.graph.merge import FragmentValidationError
from kg_debugger.graph.schema import (
    Edge,
    Evidence,
    GraphSnapshotV2,
    Node,
    SourceLocation,
)
from kg_debugger.http import EndpointConfig
from kg_debugger.orchestrator import Orchestrator


class RuntimeOverlayProtocolTests(unittest.TestCase):
    def test_runtime_diagnostic_is_ephemeral_and_has_no_client_payload(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        diagnostic = orchestrator._runtime_diagnostic("runtime_capture_empty")
        self.assertEqual(diagnostic["code"], "runtime_capture_empty")
        self.assertEqual(set(diagnostic), {"id", "code", "severity", "message"})

    def test_runtime_mismatch_diagnostic_contains_only_server_event_identity(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        diagnostic = orchestrator._runtime_diagnostic("runtime_event_unmatched", "22222222-2222-4222-8222-222222222222")
        self.assertEqual(diagnostic["eventId"], "22222222-2222-4222-8222-222222222222")
        self.assertNotIn("captureId", diagnostic)
        self.assertNotIn("path", diagnostic)
    def test_frontend_analyzer_unavailable_and_fragment_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            orchestrator = object.__new__(Orchestrator)
            orchestrator.config = SimpleNamespace(workspace_root=workspace)
            fragments, diagnostics = orchestrator._run_frontend_analyzer(
                {workspace: "repo"}
            )
            self.assertEqual(fragments, [])
            self.assertEqual(
                [diagnostic["code"] for diagnostic in diagnostics],
                ["frontend_analyzer_unavailable"],
            )

            analyzer = workspace / "analyzers" / "index.mjs"
            node = workspace / "venv" / "node24.14.1" / "bin" / "node"
            analyzer.parent.mkdir()
            node.parent.mkdir(parents=True)
            analyzer.touch()
            node.touch()
            orchestrator.config = SimpleNamespace(
                workspace_root=workspace,
                repository_set_id="a" * 64,
                repository_manifest=[{"namespace": "repo"}],
                project="repo",
                endpoint=EndpointConfig(),
            )
            completed = SimpleNamespace(returncode=0, stdout='{"repository":"repo"}')
            with patch(
                "kg_debugger.orchestrator.subprocess.run", return_value=completed
            ), patch(
                "kg_debugger.orchestrator.canonicalize_fragment",
                side_effect=FragmentValidationError("fragment_invalid"),
            ):
                _, diagnostics = orchestrator._run_frontend_analyzer({workspace: "repo"})
            self.assertEqual(
                [diagnostic["code"] for diagnostic in diagnostics],
                ["frontend_analyzer_invalid_output"],
            )
            with patch(
                "kg_debugger.orchestrator.subprocess.run", return_value=completed
            ), patch(
                "kg_debugger.orchestrator.canonicalize_fragment",
                side_effect=FragmentValidationError("bounded_url_proof_invalid"),
            ):
                _, diagnostics = orchestrator._run_frontend_analyzer({workspace: "repo"})
            self.assertEqual(
                [diagnostic["code"] for diagnostic in diagnostics],
                ["bounded_url_proof_invalid"],
            )
    def test_frontend_analyzer_invalid_utf8_is_fixed_invalid_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            analyzer = workspace / "analyzers" / "index.mjs"
            node = workspace / "venv" / "node24.14.1" / "bin" / "node"
            analyzer.parent.mkdir()
            node.parent.mkdir(parents=True)
            analyzer.touch()
            node.touch()
            orchestrator = object.__new__(Orchestrator)
            orchestrator.config = SimpleNamespace(
                workspace_root=workspace,
                repository_set_id="a" * 64,
                repository_manifest=[{"namespace": "repo"}],
                project="repo",
                endpoint=EndpointConfig(),
            )
            completed = SimpleNamespace(returncode=0, stdout=b"\xff")
            with patch(
                "kg_debugger.orchestrator.subprocess.run", return_value=completed
            ):
                fragments, diagnostics = orchestrator._run_frontend_analyzer(
                    {workspace: "repo"}
                )
            self.assertEqual(fragments, [])
            self.assertEqual(
                [diagnostic["code"] for diagnostic in diagnostics],
                ["frontend_analyzer_invalid_output"],
            )

    def test_frontend_analyzer_receives_configured_base_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            analyzer = workspace / "analyzers" / "index.mjs"
            node = workspace / "venv" / "node24.14.1" / "bin" / "node"
            analyzer.parent.mkdir()
            node.parent.mkdir(parents=True)
            analyzer.touch()
            node.touch()
            orchestrator = object.__new__(Orchestrator)
            orchestrator.config = SimpleNamespace(
                workspace_root=workspace,
                repository_set_id="a" * 64,
                repository_manifest=[{"namespace": "repo"}],
                project="repo",
                endpoint=EndpointConfig(base_paths=("/app/v1", "/api/v1")),
            )
            completed = SimpleNamespace(returncode=0, stdout='{"repository":"repo"}')
            fragment = object()
            with patch.dict(
                "kg_debugger.orchestrator.os.environ",
                {"KG_DEBUGGER_NODE": ""},
            ), patch(
                "kg_debugger.orchestrator.subprocess.run", return_value=completed
            ) as run, patch(
                "kg_debugger.orchestrator.canonicalize_fragment",
                return_value=fragment,
            ):
                fragments, diagnostics = orchestrator._run_frontend_analyzer(
                    {workspace: "repo"}
                )

            self.assertEqual(fragments, [fragment])
            self.assertEqual(diagnostics, [])
            self.assertEqual(
                run.call_args.args[0],
                [
                    str(node),
                    str(analyzer),
                    "--repository",
                    "repo",
                    "--base-path",
                    "/app/v1",
                    "--base-path",
                    "/api/v1",
                    str(workspace),
                ],
            )
    def _static_snapshot(
        self, *, include_view_edge: bool = True, ambiguous_url: bool = False
    ) -> GraphSnapshotV2:
        evidence = [Evidence("inferred", "test", "1", "ast_symbol_declaration")]
        url_source = SourceLocation("repo", "urls.py", line=1, symbol="items")
        view_source = SourceLocation("repo", "views.py", line=1, symbol="items")
        call_source = SourceLocation("repo", "client.ts", line=1, symbol="load")
        url_id = node_identity("repo", "urls.py", "django_url_pattern", "GET /api/items/")
        view_id = node_identity("repo", "views.py", "django_view", "views.items")
        call_id = node_identity("repo", "client.ts", "http_call", "load")
        url = Node(
            url_id,
            "django_url_pattern",
            "GET /api/items/",
            "api/items/",
            "backend",
            url_source,
            evidence,
            0.8,
            {
                "declaredPath": "/api/items/",
                "normalizedPath": "/api/items/",
                "endpointId": "GET /api/items/",
                "converters": [],
            },
        )
        view = Node(
            view_id,
            "django_view",
            "views.items",
            "items",
            "backend",
            view_source,
            evidence,
            0.8,
            {"pythonQualifiedName": "views.items"},
        )
        call = Node(
            call_id,
            "http_call",
            "load",
            "GET /api/items/",
            "http",
            call_source,
            evidence,
            0.8,
            {
                "method": "GET",
                "urlResolution": "literal",
                "normalizedPath": "/api/items/",
                "endpointId": "GET /api/items/",
                "queryFieldCount": 0,
                "hasSensitiveQuery": False,
            },
        )
        edges = [
            Edge(
                edge_identity(call_id, url_id, "resolves_to"),
                call_id,
                url_id,
                "resolves_to",
                evidence,
                0.8,
                {"resolutionTier": "exact_endpoint"},
            )
        ]
        nodes = [call, url, view]
        if include_view_edge:
            edges.append(
                Edge(
                    edge_identity(url_id, view_id, "resolves_to"),
                    url_id,
                    view_id,
                    "resolves_to",
                    evidence,
                    0.8,
                    {"resolutionTier": "declared_path"},
                )
            )
        else:
            missing_id = node_identity(
                "repo", "urls.py", "unresolved_target", "missing-view"
            )
            missing = Node(
                missing_id,
                "unresolved_target",
                "missing-view",
                "Unresolved",
                "unresolved",
                url_source,
                [Evidence("unresolved", "test", "1", "dynamic_target_unproven")],
                0.3,
                {"reasonCode": "dynamic_target_unproven"},
            )
            nodes.append(missing)
            edges.append(
                Edge(
                    edge_identity(url_id, missing_id, "resolves_to"),
                    url_id,
                    missing_id,
                    "resolves_to",
                    [Evidence("unresolved", "test", "1", "dynamic_target_unproven")],
                    0.3,
                    {"resolutionTier": "unbounded"},
                )
            )
        if ambiguous_url:
            alternate_source = SourceLocation(
                "repo", "alternate_urls.py", line=1, symbol="items"
            )
            nodes.append(
                Node(
                    node_identity(
                        "repo",
                        "alternate_urls.py",
                        "django_url_pattern",
                        "GET /api/items/ alternate",
                    ),
                    "django_url_pattern",
                    "GET /api/items/ alternate",
                    "api/items/",
                    "backend",
                    alternate_source,
                    evidence,
                    0.8,
                    {
                        "declaredPath": "/api/items/",
                        "normalizedPath": "/api/items/",
                        "endpointId": "GET /api/items/",
                        "converters": [],
                    },
                )
            )
            alternate = nodes[-1]
            edges.append(
                Edge(
                    edge_identity(alternate.id, view_id, "resolves_to"),
                    alternate.id,
                    view_id,
                    "resolves_to",
                    evidence,
                    0.8,
                    {"resolutionTier": "declared_path"},
                )
            )
        return GraphSnapshotV2(
            "repo",
            "a" * 64,
            [{"namespace": "repo"}],
            [],
            sorted(nodes, key=lambda node: node.id),
            sorted(edges, key=lambda edge: edge.id),
            [],
        )

    def test_overlay_places_coherent_reasons_without_mutating_static(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        orchestrator.config = SimpleNamespace(endpoint=EndpointConfig())
        orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
        orchestrator.store = SimpleNamespace(
            list_runtime_events=lambda scope, capture: [
                {
                    "eventId": "22222222-2222-4222-8222-222222222222",
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {
                        "captureId": capture,
                        "method": "GET",
                        "path": "/api/items/",
                        "viewQualifiedName": "views.items",
                    },
                }
            ]
        )
        static = self._static_snapshot()
        overlay = orchestrator._overlay_runtime(static, "capture-1")
        observed = {
            item.id: {evidence.reason for evidence in item.evidence if evidence.kind == "observed"}
            for item in [*overlay.nodes, *overlay.edges]
        }
        self.assertEqual(
            observed[
                node_identity("repo", "urls.py", "django_url_pattern", "GET /api/items/")
            ],
            {"runtime_coherent_endpoint"},
        )
        self.assertEqual(
            observed[node_identity("repo", "views.py", "django_view", "views.items")],
            {"runtime_coherent_view"},
        )
        self.assertEqual(
            observed[
                edge_identity(
                    node_identity("repo", "urls.py", "django_url_pattern", "GET /api/items/"),
                    node_identity("repo", "views.py", "django_view", "views.items"),
                    "resolves_to",
                )
            ],
            {"runtime_coherent_view"},
        )
        self.assertEqual(
            observed[
                edge_identity(
                    node_identity("repo", "client.ts", "http_call", "load"),
                    node_identity("repo", "urls.py", "django_url_pattern", "GET /api/items/"),
                    "resolves_to",
                )
            ],
            {"runtime_coherent_resolution"},
        )
        self.assertFalse(
            any(
                evidence.kind == "observed"
                for item in [*static.nodes, *static.edges]
                for evidence in item.evidence
            )
        )
    def test_overlay_uses_configured_base_path_for_runtime_identity(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        orchestrator.config = SimpleNamespace(
            endpoint=EndpointConfig(base_paths=("/base",))
        )
        orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
        orchestrator.store = SimpleNamespace(
            list_runtime_events=lambda scope, capture: [
                {
                    "eventId": "22222222-2222-4222-8222-222222222222",
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {
                        "captureId": capture,
                        "method": "GET",
                        "path": "/base/api/items/",
                    },
                }
            ]
        )
        overlay = orchestrator._overlay_runtime(self._static_snapshot(), "capture-1")
        url_id = node_identity(
            "repo", "urls.py", "django_url_pattern", "GET /api/items/"
        )
        url = next(node for node in overlay.nodes if node.id == url_id)
        self.assertEqual(
            {evidence.reason for evidence in url.evidence if evidence.kind == "observed"},
            {"runtime_coherent_endpoint"},
        )

    def test_unmatched_runtime_event_does_not_partially_overlay(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        orchestrator.config = SimpleNamespace(endpoint=EndpointConfig())
        orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
        orchestrator.store = SimpleNamespace(
            list_runtime_events=lambda scope, capture: [
                {
                    "eventId": "22222222-2222-4222-8222-222222222222",
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {
                        "captureId": capture,
                        "method": "GET",
                        "path": "/api/missing/",
                    },
                }
            ]
        )
        overlay = orchestrator._overlay_runtime(self._static_snapshot(), "capture-1")
        self.assertEqual(
            [diagnostic["code"] for diagnostic in overlay.diagnostics],
            ["runtime_event_unmatched"],
        )
        self.assertFalse(
            any(
                evidence.kind == "observed"
                for item in [*overlay.nodes, *overlay.edges]
                for evidence in item.evidence
            )
        )
    def test_overlay_is_commutative_for_coherent_runtime_events(self) -> None:
        events = [
            {
                "eventId": "22222222-2222-4222-8222-222222222222",
                "receivedAt": "2026-01-01T00:00:00.000Z",
                "payload": {
                    "captureId": "capture-1",
                    "method": "GET",
                    "path": "/api/items/",
                },
            },
            {
                "eventId": "33333333-3333-4333-8333-333333333333",
                "receivedAt": "2026-01-01T00:00:01.000Z",
                "payload": {
                    "captureId": "capture-1",
                    "method": "GET",
                    "path": "/api/items/",
                },
            },
        ]

        def overlay_for(ordered_events: list[dict[str, object]]) -> dict[str, object]:
            orchestrator = object.__new__(Orchestrator)
            orchestrator.config = SimpleNamespace(endpoint=EndpointConfig())
            orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
            orchestrator.store = SimpleNamespace(
                list_runtime_events=lambda scope, capture: ordered_events
            )
            return orchestrator._overlay_runtime(
                self._static_snapshot(), "capture-1"
            ).to_dict()

        self.assertEqual(overlay_for(events), overlay_for(list(reversed(events))))
    def test_overlay_selects_deterministic_evidence_from_more_than_32_events(self) -> None:
        events = [
            {
                "eventId": f"00000000-0000-4000-8000-{index:012x}",
                "receivedAt": f"2026-01-01T00:00:{index:02d}.000Z",
                "payload": {
                    "captureId": "capture-1",
                    "method": "GET",
                    "path": "/api/items/",
                },
            }
            for index in range(33)
        ]

        def overlay_for(ordered_events: list[dict[str, object]]) -> GraphSnapshotV2:
            orchestrator = object.__new__(Orchestrator)
            orchestrator.config = SimpleNamespace(endpoint=EndpointConfig())
            orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
            orchestrator.store = SimpleNamespace(
                list_runtime_events=lambda scope, capture: ordered_events
            )
            return orchestrator._overlay_runtime(self._static_snapshot(), "capture-1")

        first = overlay_for(events)
        second = overlay_for(list(reversed(events)))
        url_id = node_identity(
            "repo", "urls.py", "django_url_pattern", "GET /api/items/"
        )
        first_url = next(node for node in first.nodes if node.id == url_id)
        self.assertEqual(len(first_url.evidence), 32)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_view_conflict_emits_diagnostic_without_partial_overlay(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        orchestrator.config = SimpleNamespace(endpoint=EndpointConfig())
        orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
        orchestrator.store = SimpleNamespace(
            list_runtime_events=lambda scope, capture: [
                {
                    "eventId": "22222222-2222-4222-8222-222222222222",
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {
                        "captureId": capture,
                        "method": "GET",
                        "path": "/api/items/",
                        "viewQualifiedName": "views.items",
                    },
                }
            ]
        )
        overlay = orchestrator._overlay_runtime(
            self._static_snapshot(include_view_edge=False), "capture-1"
        )
        self.assertEqual(
            [item["code"] for item in overlay.diagnostics],
            ["runtime_identity_conflict"],
        )
        self.assertFalse(
            any(
                evidence.kind == "observed"
                for item in [*overlay.nodes, *overlay.edges]
                for evidence in item.evidence
            )
        )
    def test_ambiguous_url_emits_diagnostic_without_partial_overlay(self) -> None:
        orchestrator = object.__new__(Orchestrator)
        orchestrator.config = SimpleNamespace(endpoint=EndpointConfig())
        orchestrator.runtime_scope_id = "11111111-1111-1111-1111-111111111111"
        orchestrator.store = SimpleNamespace(
            list_runtime_events=lambda scope, capture: [
                {
                    "eventId": "22222222-2222-4222-8222-222222222222",
                    "receivedAt": "2026-01-01T00:00:00.000Z",
                    "payload": {
                        "captureId": capture,
                        "method": "GET",
                        "path": "/api/items/",
                    },
                }
            ]
        )
        overlay = orchestrator._overlay_runtime(
            self._static_snapshot(ambiguous_url=True), "capture-1"
        )
        self.assertEqual(
            [item["code"] for item in overlay.diagnostics],
            ["runtime_event_ambiguous"],
        )
        self.assertFalse(
            any(
                evidence.kind == "observed"
                for item in [*overlay.nodes, *overlay.edges]
                for evidence in item.evidence
            )
        )
