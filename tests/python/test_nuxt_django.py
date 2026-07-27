from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from kg_debugger.adapters.django import analyze_django
from kg_debugger.graph.merge import canonicalize_fragment, merge_canonical_fragments

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "nuxt-django"
NODE_BIN = REPO_ROOT / "venv" / "node24.14.1" / "bin" / "node"
ANALYZER = REPO_ROOT / "analyzers" / "index.mjs"
REPOSITORY = "fixture"
MANIFEST = [{"namespace": REPOSITORY}]


def backend_fragment():
    return canonicalize_fragment(analyze_django(FIXTURE_ROOT, REPOSITORY, repositories=MANIFEST))


class NuxtDjangoBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = backend_fragment().snapshot
        cls.urls = {
            node.metadata["endpointId"]: node
            for node in cls.snapshot.nodes
            if node.kind == "django_url_pattern"
        }

    def test_decorator_views_emit_one_canonical_node_per_method(self) -> None:
        self.assertTrue({
            "GET /api/items/", "GET /api/orders/", "POST /api/orders/",
            "GET /api/orders/{p0}/", "POST /api/orders/{p0}/cancel/",
        } <= set(self.urls))
        for node in self.urls.values():
            self.assertEqual(set(node.metadata), {"declaredPath", "normalizedPath", "endpointId", "converters"})
            self.assertNotIn("methods", node.metadata)
            self.assertNotIn("path", node.metadata)
            self.assertNotIn("endpointIds", node.metadata)


class NuxtDjangoCrossLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        if not NODE_BIN.exists():
            self.skipTest("local node toolchain is not bootstrapped")

    def test_nuxt_literal_calls_link_and_dynamic_route_values_remain_unresolved(self) -> None:
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
            ("GET /api/items/", "GET /api/items/"),
            ("POST /api/orders/", "POST /api/orders/"),
        } <= pairs)

        dynamic_calls = [
            node for node in nodes.values()
            if node.kind == "http_call" and node.metadata.get("urlResolution") == "unbounded"
        ]
        self.assertTrue(dynamic_calls)
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
