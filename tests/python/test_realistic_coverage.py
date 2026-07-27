from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from kg_debugger.adapters.django import analyze_django
from kg_debugger.graph.merge import canonicalize_fragment, merge_canonical_fragments

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "react-django-realistic"
NODE_BIN = REPO_ROOT / "venv" / "node24.14.1" / "bin" / "node"
ANALYZER = REPO_ROOT / "analyzers" / "index.mjs"
REPOSITORY = "fixture"
MANIFEST = [{"namespace": REPOSITORY}]


def backend_fragment():
    return canonicalize_fragment(analyze_django(FIXTURE_ROOT, REPOSITORY, repositories=MANIFEST))


def endpoint_nodes(snapshot):
    return {
        node.metadata["endpointId"]: node
        for node in snapshot.nodes
        if node.kind == "django_url_pattern"
    }


class RealisticDjangoCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fragment = backend_fragment()
        cls.snapshot = cls.fragment.snapshot
        cls.urls = endpoint_nodes(cls.snapshot)

    def test_skipped_files_report_catalog_diagnostics_without_source_details(self) -> None:
        codes = {item["code"] for item in self.snapshot.diagnostics}
        self.assertIn("unsupported_syntax", codes)
        for item in self.snapshot.diagnostics:
            self.assertEqual(set(item) - {"id", "code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"}, set())

    def test_include_prefixes_converters_and_methods_are_canonical_endpoint_nodes(self) -> None:
        expected = {
            "GET /api/orders/{p0}/", "POST /api/orders/{p0}/cancel/",
            "GET /api/customers/", "POST /api/customers/",
            "GET /api/billing/v1/invoices/", "GET /api/billing/v1/invoices/{p0}/",
            "POST /api/orders/", "DELETE /api/orders/{p0}/",
        }
        self.assertTrue(expected <= set(self.urls))
        node = self.urls["GET /api/orders/{p0}/"]
        self.assertEqual(node.metadata["normalizedPath"], "/api/orders/{p0}/")
        self.assertEqual(node.metadata["converters"][0]["kind"], "int")
        self.assertNotIn("path", node.metadata)
        self.assertNotIn("methods", node.metadata)
        self.assertNotIn("endpointIds", node.metadata)

    def test_views_models_queries_and_helpers_remain_linked(self) -> None:
        nodes = {node.id: node for node in self.snapshot.nodes}
        views = {node.label: node for node in nodes.values() if node.kind == "django_view"}
        models = {node.label: node for node in nodes.values() if node.kind == "model"}
        self.assertTrue({"OrderListCreateView", "OrderDetailView", "cancel_order", "CustomerViewSet"} <= set(views))
        self.assertTrue({"Order", "Customer"} <= set(models))
        order_queries = [node.id for node in nodes.values() if node.kind == "query_boundary" and node.metadata.get("modelQualifiedName", "").endswith(".Order")]
        self.assertTrue(order_queries)
        self.assertTrue(any(edge.target in order_queries for edge in self.snapshot.edges if edge.source == views["OrderListCreateView"].id))
        self.assertTrue(any(edge.source in order_queries and edge.target == models["Order"].id for edge in self.snapshot.edges))
        self.assertTrue({"notify_warehouse", "build_payload"} <= {node.label for node in nodes.values() if node.kind == "function"})


class RealisticCrossLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        if not NODE_BIN.exists():
            self.skipTest("local node toolchain is not bootstrapped")

    def test_literal_calls_link_while_route_parameter_calls_remain_unresolved(self) -> None:
        completed = subprocess.run([str(NODE_BIN), str(ANALYZER), "--frontend-only", "--repository", REPOSITORY, str(FIXTURE_ROOT)], check=True, text=True, capture_output=True, timeout=60)
        backend = backend_fragment()
        frontend = json.loads(completed.stdout)
        frontend["repositorySetId"] = backend.snapshot.repositorySetId
        frontend["repositories"] = MANIFEST
        snapshot = merge_canonical_fragments(
            backend,
            canonicalize_fragment(frontend),
            active_manifest=MANIFEST,
        )
        nodes = {node.id: node for node in snapshot.nodes}
        endpoint_by_source = {
            node.id: node.metadata.get("endpointId")
            for node in nodes.values()
            if node.kind == "http_call"
        }
        for edge in snapshot.edges:
            if edge.kind == "carries" and edge.source in endpoint_by_source:
                endpoint_by_source[edge.target] = endpoint_by_source[edge.source]
        pairs = {
            (endpoint_by_source.get(edge.source), nodes[edge.target].metadata.get("endpointId"))
            for edge in snapshot.edges
            if edge.kind == "resolves_to"
            and edge.source in endpoint_by_source
            and nodes[edge.target].kind == "django_url_pattern"
        }
        self.assertTrue({
            ("GET /api/orders/", "GET /api/orders/"),
            ("POST /api/orders/", "POST /api/orders/"),
            ("GET /api/customers/", "GET /api/customers/"),
        } <= pairs)

        dynamic_calls = [
            node for node in nodes.values()
            if node.kind == "http_call" and node.metadata.get("urlResolution") == "unbounded"
        ]
        self.assertGreaterEqual(len(dynamic_calls), 3)
        for call in dynamic_calls:
            self.assertNotIn("endpointId", call.metadata)
            targets = [
                nodes[edge.target] for edge in snapshot.edges
                if edge.kind == "resolves_to" and edge.source == call.id
            ]
            self.assertTrue(any(target.kind == "unresolved_target" for target in targets))
            self.assertFalse(any(target.kind == "django_url_pattern" for target in targets))


if __name__ == "__main__":
    unittest.main()
