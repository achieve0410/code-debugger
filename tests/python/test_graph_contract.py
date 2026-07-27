from __future__ import annotations

import json
import unittest

from kg_debugger.graph.contracts import (
    DIAGNOSTIC_CATALOG,
    EDGE_METADATA_SCHEMAS,
    EVIDENCE_REASON_CATALOG,
    NODE_METADATA_SCHEMAS,
    format_external_authority,
    is_secret_key,
    is_secret_value,
    route_sort_key,
    validate_diagnostic,
    validate_evidence_reason,
    validate_metadata,
)
from kg_debugger.graph.identity import (
    diagnostic_identity,
    edge_identity,
    node_identity,
    route_identity,
)
from kg_debugger.graph.proof import BoundedUrlProofError, parse_bounded_url_proofs
from kg_debugger.graph.schema import (
    LEGAL_EDGE_TUPLES,
    Edge,
    Evidence,
    GraphSnapshotV2,
    Node,
    SourceLocation,
)


class GraphV2ContractTests(unittest.TestCase):
    def test_identity_fixed_vectors(self) -> None:
        self.assertEqual(
            route_identity("repo", "react", "/api/items", "n_" + "0" * 64),
            "r_62c751cd3f141fc87755d793d61c91f1f286203fad72126a6e474905b54e8ccd",
        )
        self.assertEqual(
            diagnostic_identity(
                "unsupported_syntax", "warning", "Unsupported syntax was left unresolved.",
                '"repo"', "null", '"n_' + "0" * 64 + '"', "null", "null", "null",
            ),
            "d_0fc25e269c29904540f35d6d6da97dbbf9134b0c923452887e79999be56723f9",
        )
    def test_every_node_metadata_schema_accepts_its_minimal_record(self) -> None:
        records = {
            "frontend_route": ("frontend", {"framework": "react", "declaredPath": "/users/:id"}),
            "page": ("frontend", {"frameworkOwners": ["react"]}),
            "component": ("frontend", {"frameworkOwners": ["nuxt"]}),
            "ui_event": ("frontend", {"frameworkOwners": ["react"], "eventKind": "click", "elementKind": "button", "modifiers": []}),
            "function": ("backend", {}),
            "http_call": ("http", {"method": "GET", "urlResolution": "literal", "normalizedPath": "/api/items", "queryFieldCount": 0, "hasSensitiveQuery": False}),
            "request_payload": ("http", {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False}),
            "django_url_pattern": ("backend", {"declaredPath": "/api/<int:item>/", "normalizedPath": "/api/{p0}/", "endpointId": "GET /api/{p0}/", "converters": [{"name": "item", "kind": "int", "segmentIndex": 1}]}),
            "django_view": ("backend", {}), "model": ("data", {}),
            "query_boundary": ("data", {"operation": "filter"}),
            "external_service": ("external", {"method": "GET", "scheme": "https", "host": "example.com", "pathPresent": True, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True}),
            "unresolved_target": ("unresolved", {"reasonCode": "url_target_unmatched"}),
        }
        self.assertEqual(set(records), set(NODE_METADATA_SCHEMAS))
        for kind, (layer, metadata) in records.items():
            with self.subTest(kind=kind):
                validate_metadata(kind, layer, metadata, phase="persistable")

    def test_every_edge_metadata_schema_accepts_its_minimal_record(self) -> None:
        values = {kind: {} for kind in EDGE_METADATA_SCHEMAS}
        values["carries"] = {"payloadKinds": ["body"]}
        values["resolves_to"] = {"resolutionTier": "exact_endpoint"}
        for kind, metadata in values.items():
            with self.subTest(kind=kind):
                validate_metadata(kind, "", metadata, phase="persistable")
    def test_metadata_required_optional_and_extra_field_matrix(self) -> None:
        records = {
            "frontend_route": ("frontend", {"framework": "react", "declaredPath": "/users/:id"}, {"framework", "declaredPath"}, ()),
            "page": ("frontend", {"frameworkOwners": ["react"]}, {"frameworkOwners"}, ()),
            "component": ("frontend", {"frameworkOwners": ["nuxt"]}, {"frameworkOwners"}, ()),
            "ui_event": ("frontend", {"frameworkOwners": ["react"], "eventKind": "click", "elementKind": "button", "modifiers": []}, {"frameworkOwners", "eventKind", "elementKind", "modifiers"}, ()),
            "function": ("frontend", {"frameworkOwners": ["react"]}, {"frameworkOwners"}, ("pythonQualifiedName",)),
            "http_call": ("http", {"method": "GET", "urlResolution": "literal", "normalizedPath": "/items", "queryFieldCount": 0, "hasSensitiveQuery": False}, {"method", "urlResolution", "normalizedPath", "queryFieldCount", "hasSensitiveQuery"}, ("endpointId", "targetRepository")),
            "request_payload": ("http", {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False}, {"payloadKinds", "bodyShape", "bodyFieldCount", "queryFieldCount", "hasSensitiveFields"}, ()),
            "django_url_pattern": ("backend", {"declaredPath": "/items/<int:item>", "normalizedPath": "/items/{p0}", "endpointId": "GET /items/{p0}", "converters": [{"name": "item", "kind": "int", "segmentIndex": 1}]}, {"declaredPath", "normalizedPath", "endpointId", "converters"}, ()),
            "django_view": ("backend", {}, set(), ("pythonQualifiedName",)),
            "model": ("data", {}, set(), ("pythonQualifiedName",)),
            "query_boundary": ("data", {"operation": "get"}, {"operation"}, ("modelQualifiedName",)),
            "external_service": ("external", {"method": "GET", "scheme": "https", "host": "example.com", "pathPresent": True, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True}, {"method", "scheme", "host", "pathPresent", "queryFieldCount", "hasSensitiveQuery", "boundaryOnly"}, ("port",)),
            "unresolved_target": ("unresolved", {"reasonCode": "url_target_unmatched"}, {"reasonCode"}, ("candidateIds",)),
        }
        self.assertEqual(set(records), set(NODE_METADATA_SCHEMAS))
        for kind, (layer, metadata, required, optional) in records.items():
            with self.subTest(kind=kind, shape="valid"):
                validate_metadata(kind, layer, metadata, phase="persistable")
            for field in required:
                with self.subTest(kind=kind, shape="missing", field=field), self.assertRaises(ValueError):
                    validate_metadata(kind, layer, {key: value for key, value in metadata.items() if key != field}, phase="persistable")
            for field in optional:
                with self.subTest(kind=kind, shape="optional-wrong-type", field=field), self.assertRaises(ValueError):
                    validate_metadata(kind, layer, {**metadata, field: False}, phase="persistable")
            with self.subTest(kind=kind, shape="extra"), self.assertRaises(ValueError):
                validate_metadata(kind, layer, {**metadata, "extra": None}, phase="persistable")

        edge_records = {
            **{kind: ({}, set(), ()) for kind in EDGE_METADATA_SCHEMAS if kind not in {"carries", "resolves_to"}},
            "carries": ({"payloadKinds": ["body"]}, set(), ("payloadKinds",)),
            "resolves_to": ({"resolutionTier": "exact_endpoint"}, {"resolutionTier"}, ("targetRepository",)),
        }
        self.assertEqual(set(edge_records), set(EDGE_METADATA_SCHEMAS))
        for kind, (metadata, required, optional) in edge_records.items():
            with self.subTest(kind=kind, shape="valid"):
                validate_metadata(kind, "", metadata, phase="persistable")
            for field in required:
                with self.subTest(kind=kind, shape="missing", field=field), self.assertRaises(ValueError):
                    validate_metadata(kind, "", {key: value for key, value in metadata.items() if key != field}, phase="persistable")
            for field in optional:
                with self.subTest(kind=kind, shape="optional-wrong-type", field=field), self.assertRaises(ValueError):
                    validate_metadata(kind, "", {**metadata, field: False}, phase="persistable")
            with self.subTest(kind=kind, shape="extra"), self.assertRaises(ValueError):
                validate_metadata(kind, "", {**metadata, "extra": None}, phase="persistable")

    def test_metadata_enums_collections_and_numeric_boundaries(self) -> None:
        for kind, layer, metadata in (
            ("frontend_route", "frontend", {"framework": "svelte", "declaredPath": "/items"}),
            ("request_payload", "http", {"payloadKinds": ["body"], "bodyShape": "map", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False}),
            ("query_boundary", "data", {"operation": "merge"}),
            ("external_service", "external", {"method": "GET", "scheme": "ftp", "host": "example.com", "pathPresent": True, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True}),
            ("resolves_to", "", {"resolutionTier": "guess"}),
        ):
            with self.subTest(kind=kind, metadata=metadata), self.assertRaises(ValueError):
                validate_metadata(kind, layer, metadata, phase="persistable")

        for field, value in (("queryFieldCount", True), ("bodyFieldCount", True)):
            metadata = {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_metadata("request_payload", "http", metadata, phase="persistable")
        for payload_kinds in (["query", "body"], ["body", "body"], [], ["body", "form", "query", "body"]):
            with self.subTest(payload_kinds=payload_kinds), self.assertRaises(ValueError):
                validate_metadata("request_payload", "http", {"payloadKinds": payload_kinds, "bodyShape": "object", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False}, phase="persistable")

        base_external = {"method": "GET", "scheme": "https", "host": "example.com", "pathPresent": True, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True}
        for port in (1, 65535):
            with self.subTest(port=port):
                validate_metadata("external_service", "external", {**base_external, "port": port}, phase="persistable")
        for port in (0, 65536, True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                validate_metadata("external_service", "external", {**base_external, "port": port}, phase="persistable")

        candidate_ids = [f"n_{index:064x}" for index in range(100)]
        validate_metadata("unresolved_target", "unresolved", {"reasonCode": "url_target_unmatched", "candidateIds": candidate_ids}, phase="persistable")
        for candidates in (candidate_ids + ["n_" + "f" * 64], candidate_ids[::-1], ["n_" + "0" * 64, "n_" + "0" * 64]):
            with self.subTest(candidates=candidates), self.assertRaises(ValueError):
                validate_metadata("unresolved_target", "unresolved", {"reasonCode": "url_target_unmatched", "candidateIds": candidates}, phase="persistable")

    def test_catalogs_fail_closed(self) -> None:
        for kind, catalog in EVIDENCE_REASON_CATALOG.items():
            for reason in catalog:
                validate_evidence_reason(kind, reason, persistable=False)
        with self.assertRaises(ValueError):
            validate_evidence_reason("inferred", "free prose", persistable=False)
        for code, spec in DIAGNOSTIC_CATALOG.items():
            diagnostic = {"id": "d_" + "0" * 64, "code": code, "severity": spec.severity, "message": spec.message}
            for reference in spec.allowed_references:
                if reference == "repository":
                    diagnostic[reference] = "repo"
                elif reference == "eventId":
                    diagnostic[reference] = "22222222-2222-4222-8222-222222222222"
                elif reference == "candidateIds":
                    diagnostic[reference] = ["n_" + "0" * 64]
                elif reference == "nodeId":
                    diagnostic[reference] = "n_" + "0" * 64
                elif reference == "edgeId":
                    diagnostic[reference] = "e_" + "0" * 64
                elif reference == "source":
                    diagnostic[reference] = {"repository": "repo", "path": "a.py"}
            fields = tuple(
                json.dumps(diagnostic.get(key), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for key in ("code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId")
            )
            diagnostic["id"] = diagnostic_identity(*fields)
            validate_diagnostic(diagnostic, persistable=False)

    def test_route_order_is_total(self) -> None:
        routes = [
            {"id": "r_2", "repository": "repo", "framework": "django", "path": "/same", "nodeId": "n_2"},
            {"id": "r_1", "repository": "repo", "framework": "vue", "path": "/same", "nodeId": "n_1"},
            {"id": "r_0", "repository": "repo", "framework": "react", "path": "/same", "nodeId": "n_0"},
        ]
        self.assertEqual([route["id"] for route in sorted(routes, key=route_sort_key)], ["r_0", "r_1", "r_2"])

    def test_legal_tuple_table_is_exposed(self) -> None:
        self.assertEqual(set(LEGAL_EDGE_TUPLES), set(EDGE_METADATA_SCHEMAS))
        self.assertTrue(all(pairs for pairs in LEGAL_EDGE_TUPLES.values()))

    def test_secret_detector_rejects_patterns_and_preserves_near_misses(self) -> None:
        for key in ("accessToken", "client-secret", "password"):
            self.assertTrue(is_secret_key(key))
        for value in (
            "Bearer abcdefgh",
            "AKIA1234567890ABCDEF",
            "ghp_12345678901234567890",
            "token=not-safe",
            "abcdefghijklmnopqrstuvwxyzABCDEF123456",
        ):
            self.assertTrue(is_secret_value(value))
        self.assertFalse(is_secret_key("hasSensitiveQuery"))
        for value in (
            "route-name-123",
            "src/components/network/NetworkConfigurationPanel.vue",
        ):
            self.assertFalse(is_secret_value(value))
        validate_metadata(
            "django_view",
            "backend",
            {"pythonQualifiedName": "module." + "a" * 32},
            phase="persistable",
        )

    def test_boolean_confidence_is_not_a_number(self) -> None:
        source = SourceLocation("repo", "views.py", symbol="view")
        identifier = node_identity("repo", "views.py", "django_view", "view")
        with self.assertRaises(ValueError):
            Node(identifier, "django_view", "view", "view", "backend", source, [Evidence("inferred", "adapter", "1", "django_view_binding")], True, {})

    def test_unknown_metadata_and_runtime_material_are_rejected_for_persistence(self) -> None:
        with self.assertRaises(ValueError):
            validate_metadata("page", "frontend", {"frameworkOwners": ["react"], "token": "x"}, phase="persistable")
        source = SourceLocation("repo", "views.py", symbol="view")
        unresolved_source = SourceLocation("repo", "missing.py", symbol="missing")
        view_id = node_identity("repo", "views.py", "django_view", "view")
        unresolved_id = node_identity("repo", "missing.py", "unresolved_target", "missing")
        observed = Evidence("observed", "runtime", "1", "runtime_coherent_view", "22222222-2222-4222-8222-222222222222", "2026-01-01T00:00:00.000Z")
        view = Node(view_id, "django_view", "view", "view", "backend", source, [observed], 1.0, {})
        unresolved = Node(unresolved_id, "unresolved_target", "missing", "Unresolved", "unresolved", unresolved_source, [Evidence("unresolved", "adapter", "1", "url_target_unmatched")], 1.0, {"reasonCode": "url_target_unmatched"})
        edge = Edge(edge_identity(view_id, unresolved_id, "invokes"), view_id, unresolved_id, "invokes", [Evidence("inferred", "adapter", "1", "ast_call")], 1.0, {})
        snapshot = GraphSnapshotV2("project", "0" * 64, [{"namespace": "repo"}], [], sorted([unresolved, view], key=lambda node: node.id), [edge], [])
        with self.assertRaises(ValueError):
            snapshot.validate_persistable()

    def _node(self, kind: str, identity: str) -> Node:
        metadata = {
            "frontend_route": {"framework": "react", "declaredPath": "/route"},
            "page": {"frameworkOwners": ["react"]},
            "component": {"frameworkOwners": ["react"]},
            "ui_event": {"frameworkOwners": ["react"], "eventKind": "click", "elementKind": "button", "modifiers": []},
            "function": {},
            "http_call": {"method": "GET", "urlResolution": "literal", "normalizedPath": "/call", "queryFieldCount": 0, "hasSensitiveQuery": False},
            "request_payload": {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 0, "queryFieldCount": 0, "hasSensitiveFields": False},
            "django_url_pattern": {"declaredPath": "/target", "normalizedPath": "/target", "endpointId": "GET /target", "converters": []},
            "django_view": {},
            "model": {},
            "query_boundary": {"operation": "get"},
            "external_service": {"method": "GET", "scheme": "https", "host": "example.com", "pathPresent": True, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True},
            "unresolved_target": {"reasonCode": "url_target_unmatched"},
        }[kind]
        layer = {
            "frontend_route": "frontend", "page": "frontend", "component": "frontend",
            "ui_event": "frontend", "function": "backend", "http_call": "http",
            "request_payload": "http", "django_url_pattern": "backend",
            "django_view": "backend", "model": "data", "query_boundary": "data",
            "external_service": "external", "unresolved_target": "unresolved",
        }[kind]
        source = SourceLocation("repo", f"{kind}_{identity}.py", symbol="symbol")
        identifier = node_identity("repo", source.path, kind, identity)
        evidence_kind = "unresolved" if kind == "unresolved_target" else "inferred"
        reason = "url_target_unmatched" if evidence_kind == "unresolved" else "ast_symbol_declaration"
        label = {
            "http_call": "GET /call",
            "request_payload": "Request payload",
            "external_service": "GET https://example.com",
            "unresolved_target": "Unresolved",
        }.get(kind, identity)
        return Node(identifier, kind, identity, label, layer, source, [Evidence(evidence_kind, "adapter", "1", reason)], 1.0, metadata)

    def _edge(self, source: Node, target: Node, kind: str, metadata: dict[str, object] | None = None) -> Edge:
        metadata = metadata if metadata is not None else (
            {"resolutionTier": "declared_path"} if kind == "resolves_to" and source.kind == "django_url_pattern" and target.kind == "django_view"
            else {"resolutionTier": "external_boundary"} if kind == "resolves_to" and target.kind == "external_service"
            else {"resolutionTier": "unbounded"} if kind == "resolves_to" and target.kind == "unresolved_target"
            else {"resolutionTier": "exact_endpoint"} if kind == "resolves_to" else {}
        )
        return Edge(edge_identity(source.id, target.id, kind), source.id, target.id, kind, [Evidence("inferred", "adapter", "1", "ast_call")], 1.0, metadata)

    def _snapshot(self, nodes: list[Node], edges: list[Edge]) -> GraphSnapshotV2:
        return GraphSnapshotV2("project", "0" * 64, [{"namespace": "repo"}], [], sorted(nodes, key=lambda item: item.id), sorted(edges, key=lambda item: item.id), [])

    def test_every_legal_tuple_constructs_a_valid_whole_graph(self) -> None:
        for edge_kind, tuples in LEGAL_EDGE_TUPLES.items():
            for source_kind, target_kind in tuples:
                with self.subTest(edge_kind=edge_kind, source_kind=source_kind, target_kind=target_kind):
                    source, target = self._node(source_kind, "source"), self._node(target_kind, "target")
                    nodes, edges = [source, target], [self._edge(source, target, edge_kind)]
                    if edge_kind == "carries":
                        source = self._node("http_call", "source")
                        source = Node(source.id, source.kind, source.identityKey, "GET /call/{u0}", source.layer, source.source, source.evidence, source.confidence, {**source.metadata, "urlResolution": "unbounded", "normalizedPath": "/call/{u0}"})
                        nodes[0] = source
                        edges[0] = self._edge(source, target, edge_kind)
                        unresolved = self._node("unresolved_target", "resolved")
                        nodes.append(unresolved)
                        edges.append(self._edge(target, unresolved, "resolves_to"))
                    elif edge_kind == "resolves_to" and source_kind == "request_payload":
                        call = self._node("http_call", "call")
                        if target_kind == "unresolved_target":
                            call = Node(call.id, call.kind, call.identityKey, "GET /call/{u0}", call.layer, call.source, call.evidence, call.confidence, {**call.metadata, "urlResolution": "unbounded", "normalizedPath": "/call/{u0}"})
                        elif target_kind == "django_url_pattern":
                            call = Node(call.id, call.kind, call.identityKey, "GET /target", call.layer, call.source, call.evidence, call.confidence, {**call.metadata, "normalizedPath": "/target", "endpointId": "GET /target"})
                        nodes.append(call)
                        edges.append(self._edge(call, source, "carries"))
                    elif edge_kind == "resolves_to" and source_kind == "django_url_pattern":
                        pass
                    elif edge_kind == "resolves_to" and source_kind == "http_call":
                        if target_kind == "unresolved_target":
                            source = Node(source.id, source.kind, source.identityKey, "GET /call/{u0}", source.layer, source.source, source.evidence, source.confidence, {**source.metadata, "urlResolution": "unbounded", "normalizedPath": "/call/{u0}"})
                        elif target_kind == "django_url_pattern":
                            source = Node(source.id, source.kind, source.identityKey, "GET /target", source.layer, source.source, source.evidence, source.confidence, {**source.metadata, "normalizedPath": "/target", "endpointId": "GET /target"})
                        nodes[0] = source
                        edges[0] = self._edge(source, target, edge_kind)
                    outgoing = {
                        node.id: [edge for edge in edges if edge.source == node.id]
                        for node in nodes
                    }
                    for owner in list(nodes):
                        needs_terminal = (
                            owner.kind == "http_call"
                            and not any(edge.kind in {"carries", "resolves_to"} for edge in outgoing[owner.id])
                        ) or (
                            owner.kind == "django_url_pattern"
                            and not any(edge.kind == "resolves_to" for edge in outgoing[owner.id])
                        )
                        if needs_terminal:
                            terminal = self._node("unresolved_target", f"t{len(nodes)}")
                            nodes.append(terminal)
                            edges.append(self._edge(owner, terminal, "resolves_to"))
                    self._snapshot(nodes, edges)

    def test_representative_illegal_pair_fails_for_every_edge_kind(self) -> None:
        for edge_kind in LEGAL_EDGE_TUPLES:
            source, target = self._node("model", f"{edge_kind}_source"), self._node("page", f"{edge_kind}_target")
            with self.subTest(edge_kind=edge_kind), self.assertRaises(ValueError):
                self._snapshot([source, target], [self._edge(source, target, edge_kind)])

    def test_identity_and_shape_boundaries_fail_closed(self) -> None:
        page = self._node("page", "page")
        route = self._node("frontend_route", "route")
        edge = self._edge(page, route, "navigates_to")
        snapshot = self._snapshot([page, route], [edge])
        route_record = {"id": route_identity("repo", "react", "/route", route.id), "repository": "repo", "framework": "react", "path": "/route", "nodeId": route.id}
        GraphSnapshotV2("project", "0" * 64, [{"namespace": "repo"}], [route_record], snapshot.nodes, snapshot.edges, [])
        with self.assertRaises(ValueError):
            Edge(edge.id, edge.source, edge.source, edge.kind, edge.evidence, edge.confidence, edge.metadata)
        with self.assertRaises(ValueError):
            Edge("e_" + "0" * 64, edge.source, edge.target, edge.kind, edge.evidence, edge.confidence, edge.metadata)
        duplicate_route = {**route_record, "nodeId": page.id, "id": route_identity("repo", "react", "/route", page.id)}
        with self.assertRaises(ValueError):
            GraphSnapshotV2("project", "0" * 64, [{"namespace": "repo"}], [route_record, duplicate_route], snapshot.nodes, snapshot.edges, [])
        for confidence in (True, float("nan"), float("inf")):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                Node(page.id, page.kind, page.identityKey, page.label, page.layer, page.source, page.evidence, confidence, page.metadata)
        for source_path in (
            "C:/src/app.py",
            "src/%2e%2e/app.py",
            "src/%252e%252e/app.py",
            "src/%252fapp.py",
            "src/%2fapp.py",
            "src/%zz/app.py",
            "src/%c3%a9.py",
        ):
            with self.subTest(source_path=source_path), self.assertRaises(ValueError):
                SourceLocation.from_dict({"repository": "repo", "path": source_path})

    def test_minimization_and_payload_negative_space(self) -> None:
        for field in (
            {"url": "https://user:password@example.com/path"},
            {"expression": "fetch(`/api/${token}`)"},
            {"requestBody": "secret=value"},
            {"hasSensitiveQuery": "false"},
        ):
            metadata = {"method": "GET", "urlResolution": "literal", "normalizedPath": "/safe", "queryFieldCount": 0, "hasSensitiveQuery": False, **field}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_metadata("http_call", "http", metadata, phase="persistable")
        validate_metadata("request_payload", "http", {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 3, "queryFieldCount": 2, "hasSensitiveFields": True}, phase="persistable")
        count_cases = (
            ("http_call", "http", {"method": "GET", "urlResolution": "literal", "normalizedPath": "/safe", "queryFieldCount": 1000, "hasSensitiveQuery": False}, "queryFieldCount"),
            ("request_payload", "http", {"payloadKinds": ["body"], "bodyShape": "object", "bodyFieldCount": 1000, "queryFieldCount": 1000, "hasSensitiveFields": False}, "bodyFieldCount"),
            ("request_payload", "http", {"payloadKinds": ["query"], "bodyShape": "none", "bodyFieldCount": 0, "queryFieldCount": 1000, "hasSensitiveFields": False}, "queryFieldCount"),
            ("external_service", "external", {"method": "GET", "scheme": "https", "host": "example.com", "pathPresent": True, "queryFieldCount": 1000, "hasSensitiveQuery": False, "boundaryOnly": True}, "queryFieldCount"),
        )
        for kind, layer, metadata, field in count_cases:
            with self.subTest(kind=kind, field=field):
                validate_metadata(kind, layer, metadata, phase="persistable")
                with self.assertRaises(ValueError):
                    validate_metadata(kind, layer, {**metadata, field: 1001}, phase="persistable")
        observed = Evidence("observed", "runtime", "1", "runtime_coherent_view", "22222222-2222-4222-8222-222222222222", "2026-01-01T00:00:00.000Z")
        runtime_source = SourceLocation("repo", "runtime.py", symbol="runtime")
        runtime = Node(node_identity("repo", "runtime.py", "django_view", "runtime"), "django_view", "runtime", "runtime", "backend", runtime_source, [observed], 1.0, {})
        with self.assertRaises(ValueError):
            self._snapshot([runtime], []).validate_persistable()
        for event_id, timestamp in (
            ("event-1", "2026-01-01T00:00:00.000Z"),
            ("22222222-2222-2222-8222-222222222222", "2026-01-01T00:00:00.000Z"),
            ("22222222-2222-4222-8222-222222222222", "2026-02-30T00:00:00.000Z"),
        ):
            with self.subTest(event_id=event_id, timestamp=timestamp), self.assertRaises(ValueError):
                Evidence("observed", "runtime", "1", "runtime_coherent_view", event_id, timestamp)
    def test_django_converter_segment_index_bounds_and_ordinals(self) -> None:
        def pattern_with_converter_at(position: int, segment_index: object) -> dict[str, object]:
            declared_segments = ["x."] * position + ["<int:item>"]
            normalized_segments = ["x."] * position + ["{p0}"]
            normalized_path = "/" + "/".join(normalized_segments)
            return {
                "declaredPath": "/" + "/".join(declared_segments),
                "normalizedPath": normalized_path,
                "endpointId": "GET " + normalized_path,
                "converters": [{"name": "item", "kind": "int", "segmentIndex": segment_index}],
            }

        validate_metadata(
            "django_url_pattern",
            "backend",
            pattern_with_converter_at(255, 255),
            phase="persistable",
        )
        for invalid_index in (256, True, 255.0, "255"):
            with self.subTest(case="segment-index-type-or-bound"), self.assertRaisesRegex(
                ValueError, "invalid segmentIndex"
            ):
                validate_metadata(
                    "django_url_pattern",
                    "backend",
                    pattern_with_converter_at(256, invalid_index),
                    phase="persistable",
                )
        with self.assertRaises(ValueError):
            validate_metadata(
                "django_url_pattern",
                "backend",
                pattern_with_converter_at(255, 254),
                phase="persistable",
            )


        declared_segments = [f"<int:item{index}>" for index in range(32)]
        converters = [
            {"name": f"item{index}", "kind": "int", "segmentIndex": index}
            for index in range(32)
        ]
        normalized_path = "/" + "/".join(f"{{p{index}}}" for index in range(32))
        validate_metadata(
            "django_url_pattern",
            "backend",
            {
                "declaredPath": "/" + "/".join(declared_segments),
                "normalizedPath": normalized_path,
                "endpointId": "GET " + normalized_path,
                "converters": converters,
            },
            phase="persistable",
        )
        invalid_ordinal_path = "/" + "/".join(
            [f"{{p{index}}}" for index in range(31)] + ["{p30}"]
        )
        with self.assertRaises(ValueError):
            validate_metadata(
                "django_url_pattern",
                "backend",
                {
                    "declaredPath": "/" + "/".join(declared_segments),
                    "normalizedPath": invalid_ordinal_path,
                    "endpointId": "GET " + invalid_ordinal_path,
                    "converters": converters,
                },
                phase="persistable",
            )
        with self.assertRaises(ValueError):
            validate_metadata(
                "django_url_pattern",
                "backend",
                {
                    "declaredPath": "/" + "/".join(declared_segments + ["<int:extra>"]),
                    "normalizedPath": normalized_path + "/{p32}",
                    "endpointId": "GET " + normalized_path + "/{p32}",
                    "converters": converters + [{"name": "extra", "kind": "int", "segmentIndex": 32}],
                },
                phase="persistable",
            )
    def test_collection_and_metadata_boundaries_fail_closed(self) -> None:
        page, component = self._node("page", "ordered-page"), self._node("component", "ordered-component")
        with self.assertRaises(ValueError):
            GraphSnapshotV2("project", "0" * 64, [{"namespace": "repo"}], [], sorted([page, component], key=lambda item: item.id, reverse=True), [], [])
        with self.assertRaises(ValueError):
            self._snapshot([page, page], [])
        for normalized_path in ("/items/{id}", "/items/{p1}", "/items/%2f", "/items/../x"):
            with self.subTest(path=normalized_path), self.assertRaises(ValueError):
                validate_metadata("http_call", "http", {"method": "GET", "urlResolution": "bounded_template", "normalizedPath": normalized_path, "queryFieldCount": 0, "hasSensitiveQuery": False}, phase="persistable")
        for host in ("EXAMPLE.com", "example.com.", "bücher.example", "2001:0db8::1", "fe80::1%eth0"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_metadata(
                    "external_service", "external",
                    {"method": "GET", "scheme": "https", "host": host, "pathPresent": False, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True},
                    phase="persistable",
                )
        for path in ("/a/%2f", "/a/%2F", "/a/%41", "/a/%4a", "/a/%252F", "/a/\u0080"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_metadata(
                    "http_call", "http",
                    {"method": "GET", "urlResolution": "literal", "normalizedPath": path, "queryFieldCount": 0, "hasSensitiveQuery": False},
                    phase="persistable",
                )
        for converters in (
            [{"name": "item", "kind": "int", "segmentIndex": 0}],
            [{"name": "item", "kind": "custom", "segmentIndex": 1}],
        ):
            with self.subTest(converters=converters), self.assertRaises(ValueError):
                validate_metadata("django_url_pattern", "backend", {"declaredPath": "/api/<int:item>/", "normalizedPath": "/api/{p0}/", "endpointId": "GET /api/{p0}/", "converters": converters}, phase="persistable")
        with self.assertRaises(ValueError):
            validate_metadata("unresolved_target", "unresolved", {"reasonCode": "url_target_unmatched", "candidateIds": ["not-a-node-id"]}, phase="persistable")
        with self.assertRaises(ValueError):
            Node(page.id, page.kind, page.identityKey, "x" * 257, page.layer, page.source, page.evidence, 1.0, page.metadata)
        payload = self._node("request_payload", "orphan")
        with self.assertRaises(ValueError):
            self._snapshot([payload], [])
        route_a, route_b = self._node("frontend_route", "route-a"), self._node("frontend_route", "route-b")
        routes = [
            {"id": route_identity("repo", "react", "/b", route_b.id), "repository": "repo", "framework": "react", "path": "/b", "nodeId": route_b.id},
            {"id": route_identity("repo", "react", "/a", route_a.id), "repository": "repo", "framework": "react", "path": "/a", "nodeId": route_a.id},
        ]
        with self.assertRaises(ValueError):
            GraphSnapshotV2("project", "0" * 64, [{"namespace": "repo"}], routes, sorted([route_a, route_b], key=lambda item: item.id), [], [])
    def test_diagnostic_source_and_ipv6_label_boundaries(self) -> None:
        def diagnostic(source: dict[str, object]) -> dict[str, object]:
            record = {"code": "source_read_failed", "severity": "warning", "message": "A source file could not be read.", "repository": "repo", "source": source}
            fields = tuple(json.dumps(record.get(key), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for key in ("code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"))
            return {"id": diagnostic_identity(*fields), **record}
        validate_diagnostic(diagnostic({"repository": "repo", "path": "views.py", "line": 10_000_000, "endLine": 10_000_000, "symbol": "Class.method"}), persistable=False)
        for source in ({"repository": "repo", "path": "views.py", "line": 10_000_001}, {"repository": "repo", "path": "views.py", "symbol": "bad!"}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_diagnostic(diagnostic(source), persistable=False)
        self.assertEqual(format_external_authority("192.0.2.1", 443), "192.0.2.1:443")
        self.assertEqual(format_external_authority("api.example", None), "api.example")
        self.assertEqual(format_external_authority("2001:db8::1", 443), "[2001:db8::1]:443")
        source = SourceLocation("repo", "external.py", symbol="call")
        metadata = {"method": "GET", "scheme": "https", "host": "2001:db8::1", "port": 443, "pathPresent": False, "queryFieldCount": 0, "hasSensitiveQuery": False, "boundaryOnly": True}
        Node(node_identity("repo", "external.py", "external_service", "call"), "external_service", "call", "GET https://[2001:db8::1]:443", "external", source, [Evidence("inferred", "adapter", "1", "external_boundary")], 1.0, metadata)
    def test_wire_object_families_reject_missing_extra_and_wrong_types(self) -> None:
        page = self._node("page", "wire-page")
        route = self._node("frontend_route", "wire-route")
        edge = self._edge(page, route, "navigates_to")
        snapshot = self._snapshot([page, route], [edge])
        source = page.source.to_dict()
        evidence = page.evidence[0].to_dict()
        node = page.to_dict()
        edge_record = edge.to_dict()
        snapshot_record = snapshot.to_dict()

        exact_cases = (
            ("source", SourceLocation.from_dict, source, {"repository", "path"}),
            ("evidence", Evidence.from_dict, evidence, {"kind", "adapter", "adapterVersion"}),
            ("node", Node.from_dict, node, set(node)),
            ("edge", Edge.from_dict, edge_record, set(edge_record)),
            ("snapshot", GraphSnapshotV2.from_dict, snapshot_record, set(snapshot_record)),
        )
        for name, parser, record, required_fields in exact_cases:
            for field in required_fields:
                with self.subTest(family=name, shape="missing", field=field), self.assertRaises(ValueError):
                    parser({key: value for key, value in record.items() if key != field})
            with self.subTest(family=name, shape="extra"), self.assertRaises(ValueError):
                parser({**record, "unexpected": None})

        for source_record in (
            {"repository": True, "path": "page.py"},
            {"repository": "repo", "path": True},
            {"repository": "repo", "path": "page.py", "line": True},
            {"repository": "repo", "path": "page.py", "line": 2, "endLine": 1},
        ):
            with self.subTest(source=source_record), self.assertRaises(ValueError):
                SourceLocation.from_dict(source_record)
        for evidence_record in (
            {"kind": True, "adapter": "adapter", "adapterVersion": "1"},
            {"kind": "inferred", "adapter": True, "adapterVersion": "1"},
            {"kind": "inferred", "adapter": "adapter", "adapterVersion": True},
            {"kind": "inferred", "adapter": "adapter", "adapterVersion": "1", "eventId": "legacy-" + "0" * 64},
        ):
            with self.subTest(evidence=evidence_record), self.assertRaises(ValueError):
                Evidence.from_dict(evidence_record)
        for field in ("line", "endLine", "symbol"):
            with self.subTest(source_optional_field=field), self.assertRaises(ValueError):
                SourceLocation.from_dict({**source, field: False})
        for field in ("reason",):
            with self.subTest(evidence_optional_field=field), self.assertRaises(ValueError):
                Evidence.from_dict({**evidence, field: False})
        observed_evidence = Evidence(
            "observed",
            "runtime",
            "1",
            "runtime_coherent_view",
            "22222222-2222-4222-8222-222222222222",
            "2026-01-01T00:00:00.000Z",
        ).to_dict()
        for field in ("eventId", "timestamp"):
            with self.subTest(observed_evidence_field=field), self.assertRaises(ValueError):
                Evidence.from_dict({**observed_evidence, field: False})
        with self.assertRaises(ValueError):
            GraphSnapshotV2.from_dict({**snapshot_record, "schemaVersion": True})

    def test_phase_catalog_and_cross_field_matrices_fail_closed(self) -> None:
        runtime_code = "runtime_capture_empty"
        runtime_spec = DIAGNOSTIC_CATALOG[runtime_code]
        runtime = {
            "id": diagnostic_identity(
                json.dumps(runtime_code),
                json.dumps(runtime_spec.severity),
                json.dumps(runtime_spec.message),
                "null", "null", "null", "null", "null", "null",
            ),
            "code": runtime_code,
            "severity": runtime_spec.severity,
            "message": runtime_spec.message,
        }
        validate_diagnostic(runtime, persistable=False)
        with self.assertRaises(ValueError):
            validate_diagnostic(runtime, persistable=True)

        for kind, reason in (
            ("inferred", "runtime_coherent_view"),
            ("observed", "ast_call"),
            ("unresolved", "finite_url_domain"),
        ):
            with self.subTest(kind=kind, reason=reason), self.assertRaises(ValueError):
                validate_evidence_reason(kind, reason, persistable=False)

        payload = self._node("request_payload", "p")
        django = self._node("django_url_pattern", "d")
        view = self._node("django_view", "v")
        resolves = self._edge(payload, django, "resolves_to")
        django_binding = self._edge(django, view, "resolves_to")
        with self.assertRaises(ValueError):
            self._snapshot([payload, django, view], [resolves, django_binding])

        for metadata in (
            {"method": "GET", "urlResolution": "unbounded", "normalizedPath": "/{u0}", "queryFieldCount": 0, "hasSensitiveQuery": False, "endpointId": "GET /{u0}"},
            {"method": "GET", "urlResolution": "literal", "normalizedPath": "/x", "queryFieldCount": True, "hasSensitiveQuery": False},
            {"method": "GET", "urlResolution": "literal", "normalizedPath": "/x", "queryFieldCount": 0, "hasSensitiveQuery": 0},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                validate_metadata("http_call", "http", metadata, phase="persistable")

    def test_bounded_url_proof_permutations_fail_closed(self) -> None:
        call_id = "n_" + "1" * 64
        fragment = {
            "nodes": [{
                "key": "call",
                "kind": "http_call",
                "metadata": {"urlResolution": "bounded_template", "normalizedPath": "/items/{p0}"},
            }],
            "boundedUrlProofs": [{
                "version": 1,
                "callKey": "call",
                "normalizedPath": "/items/{p0}",
                "placeholders": [{
                    "token": "p0",
                    "segmentIndex": 1,
                    "memberCount": 1,
                    "acceptedConverters": ["int"],
                }],
            }],
        }
        self.assertEqual(set(parse_bounded_url_proofs(fragment, {"call": call_id})), {call_id})
        proof = fragment["boundedUrlProofs"][0]
        for field in proof:
            with self.subTest(proof_field=field):
                invalid = {**fragment, "boundedUrlProofs": [{key: value for key, value in proof.items() if key != field}]}
                with self.assertRaises(BoundedUrlProofError):
                    parse_bounded_url_proofs(invalid, {"call": call_id})
        for field in proof["placeholders"][0]:
            with self.subTest(placeholder_field=field):
                invalid_placeholder = {
                    key: value for key, value in proof["placeholders"][0].items() if key != field
                }
                invalid = {**fragment, "boundedUrlProofs": [{**proof, "placeholders": [invalid_placeholder]}]}
                with self.assertRaises(BoundedUrlProofError):
                    parse_bounded_url_proofs(invalid, {"call": call_id})

        invalid_proofs = (
            {**fragment, "boundedUrlProofs": []},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "extra": None}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "version": True}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "callKey": "missing"}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "normalizedPath": "/items/{p1}"}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "placeholders": []}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "placeholders": [{**fragment["boundedUrlProofs"][0]["placeholders"][0], "segmentIndex": True}]}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "placeholders": [{**fragment["boundedUrlProofs"][0]["placeholders"][0], "memberCount": 257}]}]},
            {**fragment, "boundedUrlProofs": [{**fragment["boundedUrlProofs"][0], "placeholders": [{**fragment["boundedUrlProofs"][0]["placeholders"][0], "acceptedConverters": ["uuid", "int"]}]}]},
        )
        for invalid in invalid_proofs:
            with self.subTest(proof=invalid["boundedUrlProofs"]):
                with self.assertRaises(BoundedUrlProofError):
                    parse_bounded_url_proofs(invalid, {"call": call_id})
if __name__ == "__main__":
    unittest.main()
