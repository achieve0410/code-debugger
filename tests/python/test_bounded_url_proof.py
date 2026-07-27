from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace

from kg_debugger.graph.merge import (
    GraphIdentityConflict,
    canonicalize_fragment,
    merge_canonical_fragments,
    merge_snapshots,
)
from kg_debugger.graph.proof import (
    BoundedUrlPlaceholderProof,
    BoundedUrlProof,
    BoundedUrlProofError,
    CanonicalFragment,
    parse_bounded_url_proofs,
)
from kg_debugger.graph.schema import GraphSnapshotV2


class BoundedUrlProofTests(unittest.TestCase):
    def _fragment(self) -> tuple[dict, dict[str, str]]:
        fragment = {
            "nodes": [{"key": "request", "kind": "http_call", "metadata": {"urlResolution": "bounded_template", "normalizedPath": "/items/{p0}/"}}],
            "boundedUrlProofs": [{
                "version": 1,
                "callKey": "request",
                "normalizedPath": "/items/{p0}/",
                "placeholders": [{"token": "p0", "segmentIndex": 1, "memberCount": 2, "acceptedConverters": ["int", "str"]}],
            }],
        }
        return fragment, {"request": "n_" + "0" * 64}
    def _two_proof_fragment(self) -> tuple[dict, dict[str, str]]:
        fragment = {
            "nodes": [
                {"key": "alpha", "kind": "http_call", "metadata": {"urlResolution": "bounded_template", "normalizedPath": "/alpha/{p0}/"}},
                {"key": "bravo", "kind": "http_call", "metadata": {"urlResolution": "bounded_template", "normalizedPath": "/bravo/{p0}/"}},
            ],
            "boundedUrlProofs": [
                {"version": 1, "callKey": "alpha", "normalizedPath": "/alpha/{p0}/", "placeholders": [{"token": "p0", "segmentIndex": 1, "memberCount": 1, "acceptedConverters": ["int"]}]},
                {"version": 1, "callKey": "bravo", "normalizedPath": "/bravo/{p0}/", "placeholders": [{"token": "p0", "segmentIndex": 1, "memberCount": 1, "acceptedConverters": ["int"]}]},
            ],
        }
        return fragment, {"alpha": "n_" + "a" * 64, "bravo": "n_" + "b" * 64}

    def _assert_rejects(self, fragment: dict, keys: dict[str, str]) -> None:
        with self.assertRaises(BoundedUrlProofError):
            parse_bounded_url_proofs(fragment, keys)

    def test_binds_sidecar_to_recomputed_call_id(self) -> None:
        fragment, keys = self._fragment()
        proofs = parse_bounded_url_proofs(fragment, keys)
        self.assertEqual(set(proofs), set(keys.values()))
        self.assertEqual(proofs[keys["request"]].placeholders[0].acceptedConverters, ("int", "str"))

    def test_rejects_missing_extra_and_stale_proofs(self) -> None:
        fragment, keys = self._fragment()
        fragment["boundedUrlProofs"] = []
        with self.assertRaises(BoundedUrlProofError):
            parse_bounded_url_proofs(fragment, keys)
        fragment, keys = self._fragment()
        fragment["boundedUrlProofs"][0]["normalizedPath"] = "/other/{p0}/"
        with self.assertRaises(BoundedUrlProofError):
            parse_bounded_url_proofs(fragment, keys)
        fragment, keys = self._fragment()
        fragment["boundedUrlProofs"][0]["placeholders"][0]["acceptedConverters"] = ["path"]
        with self.assertRaises(BoundedUrlProofError):
            parse_bounded_url_proofs(fragment, keys)

    def test_rejects_ordering_boolean_and_domain_overflow(self) -> None:
        fragment, keys = self._fragment()
        fragment["boundedUrlProofs"][0]["placeholders"][0]["memberCount"] = True
        with self.assertRaises(BoundedUrlProofError):
            parse_bounded_url_proofs(fragment, keys)
        fragment, keys = self._fragment()
        fragment["boundedUrlProofs"][0]["placeholders"][0]["memberCount"] = 257
        with self.assertRaises(BoundedUrlProofError):
            parse_bounded_url_proofs(fragment, keys)

    def test_parser_negative_matrix(self) -> None:
        def mutate_proof(mutator):
            fragment, keys = self._fragment()
            mutator(fragment["boundedUrlProofs"][0])
            return fragment, keys

        cases = (
            ("top-level-not-list", lambda: ({"nodes": [], "boundedUrlProofs": {}}, {})),
            ("top-level-overflow", lambda: ({"nodes": [], "boundedUrlProofs": [None] * 10_001}, {})),
            ("proof-not-object", lambda: ({"nodes": [], "boundedUrlProofs": [None]}, {})),
            ("proof-extra-field", lambda: mutate_proof(lambda proof: proof.update(extra=True))),
            ("proof-missing-field", lambda: mutate_proof(lambda proof: proof.pop("version"))),
            ("version-boolean", lambda: mutate_proof(lambda proof: proof.update(version=True))),
            ("call-key-boolean", lambda: mutate_proof(lambda proof: proof.update(callKey=True))),
            ("path-boolean", lambda: mutate_proof(lambda proof: proof.update(normalizedPath=True))),
            ("call-key-control", lambda: mutate_proof(lambda proof: proof.update(callKey="bad\nkey"))),
            ("call-key-length", lambda: mutate_proof(lambda proof: proof.update(callKey="x" * 513))),
            ("missing-call-key", lambda: mutate_proof(lambda proof: proof.update(callKey="missing"))),
            ("wrong-kind-call-key", lambda: ({"nodes": [{"key": "request", "kind": "function", "metadata": {}}], "boundedUrlProofs": [self._fragment()[0]["boundedUrlProofs"][0]]}, {"request": "n_" + "0" * 64})),
            ("path-mismatch", lambda: mutate_proof(lambda proof: proof.update(normalizedPath="/other/{p0}/"))),
            ("empty-placeholders", lambda: mutate_proof(lambda proof: proof.update(placeholders=[]))),
            ("placeholder-overflow", lambda: mutate_proof(lambda proof: proof.update(placeholders=[{}] * 33))),
        )
        for name, build in cases:
            with self.subTest(name=name):
                self._assert_rejects(*build())

    def test_parser_placeholder_negative_matrix(self) -> None:
        def fragment_with(path: str, placeholders: list[dict]) -> tuple[dict, dict[str, str]]:
            fragment, keys = self._fragment()
            fragment["nodes"][0]["metadata"]["normalizedPath"] = path
            fragment["boundedUrlProofs"][0]["normalizedPath"] = path
            fragment["boundedUrlProofs"][0]["placeholders"] = placeholders
            return fragment, keys

        valid = {"token": "p0", "segmentIndex": 1, "memberCount": 1, "acceptedConverters": ["int"]}
        cases = (
            ("token-gap", lambda: fragment_with("/items/{p0}/{p2}/", [valid, {**valid, "token": "p2", "segmentIndex": 2}])),
            ("token-order", lambda: fragment_with("/items/{p0}/{p1}/", [{**valid, "token": "p1"}, {**valid, "token": "p0", "segmentIndex": 2}])),
            ("token-duplicate", lambda: fragment_with("/items/{p0}/{p0}/", [valid, {**valid, "segmentIndex": 2}])),
            ("segment-boolean", lambda: fragment_with("/items/{p0}/", [{**valid, "segmentIndex": True}])),
            ("segment-negative", lambda: fragment_with("/items/{p0}/", [{**valid, "segmentIndex": -1}])),
            ("segment-overflow", lambda: fragment_with("/items/{p0}/", [{**valid, "segmentIndex": 256}])),
            ("member-boolean", lambda: fragment_with("/items/{p0}/", [{**valid, "memberCount": True}])),
            ("member-zero", lambda: fragment_with("/items/{p0}/", [{**valid, "memberCount": 0}])),
            ("member-overflow", lambda: fragment_with("/items/{p0}/", [{**valid, "memberCount": 257}])),
            ("converter-empty", lambda: fragment_with("/items/{p0}/", [{**valid, "acceptedConverters": []}])),
            ("converter-unsorted", lambda: fragment_with("/items/{p0}/", [{**valid, "acceptedConverters": ["str", "int"]}])),
            ("converter-duplicate", lambda: fragment_with("/items/{p0}/", [{**valid, "acceptedConverters": ["int", "int"]}])),
            ("converter-unknown", lambda: fragment_with("/items/{p0}/", [{**valid, "acceptedConverters": ["path"]}])),
            ("converter-non-string", lambda: fragment_with("/items/{p0}/", [{**valid, "acceptedConverters": [True]}])),
        )
        for name, build in cases:
            with self.subTest(name=name):
                self._assert_rejects(*build())

    def test_parser_canonical_order_domain_size_and_coverage_matrix(self) -> None:
        fragment, keys = self._two_proof_fragment()
        proofs = parse_bounded_url_proofs(fragment, keys)
        self.assertEqual(list(proofs), [keys["alpha"], keys["bravo"]])

        reversed_fragment, reversed_keys = self._two_proof_fragment()
        reversed_fragment["boundedUrlProofs"].reverse()
        self._assert_rejects(reversed_fragment, reversed_keys)

        duplicate_fragment, duplicate_keys = self._fragment()
        duplicate_fragment["boundedUrlProofs"].append(copy.deepcopy(duplicate_fragment["boundedUrlProofs"][0]))
        self._assert_rejects(duplicate_fragment, duplicate_keys)

        missing_fragment, missing_keys = self._two_proof_fragment()
        missing_fragment["boundedUrlProofs"].pop()
        self._assert_rejects(missing_fragment, missing_keys)

        extra_fragment, extra_keys = self._fragment()
        extra_fragment["boundedUrlProofs"].append({
            "version": 1,
            "callKey": "extra",
            "normalizedPath": "/extra/{p0}/",
            "placeholders": [{"token": "p0", "segmentIndex": 1, "memberCount": 1, "acceptedConverters": ["int"]}],
        })
        self._assert_rejects(extra_fragment, extra_keys)

        def domain_fragment(member_count: int) -> tuple[dict, dict[str, str]]:
            fragment, keys = self._fragment()
            path = "/{p0}/{p1}/"
            fragment["nodes"][0]["metadata"]["normalizedPath"] = path
            fragment["boundedUrlProofs"][0]["normalizedPath"] = path
            fragment["boundedUrlProofs"][0]["placeholders"] = [
                {"token": "p0", "segmentIndex": 0, "memberCount": member_count, "acceptedConverters": ["int"]},
                {"token": "p1", "segmentIndex": 1, "memberCount": member_count, "acceptedConverters": ["int"]},
            ]
            return fragment, keys

        at_limit, limit_keys = domain_fragment(64)
        self.assertEqual(len(parse_bounded_url_proofs(at_limit, limit_keys)), 1)
        self._assert_rejects(*domain_fragment(65))

    def test_parser_proof_byte_boundary_and_value_free_acceptance(self) -> None:
        fragment, keys = self._fragment()
        proof = fragment["boundedUrlProofs"][0]
        base_path = proof["normalizedPath"]
        encoded_length = len(json.dumps(proof, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
        padding = 8192 - encoded_length
        path = base_path.replace("/items/", f"/items{'x' * padding}/")
        fragment["nodes"][0]["metadata"]["normalizedPath"] = path
        proof["normalizedPath"] = path
        self.assertEqual(len(json.dumps(proof, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()), 8192)
        self.assertEqual(len(parse_bounded_url_proofs(fragment, keys)), 1)

        oversized = copy.deepcopy(fragment)
        oversized_path = base_path.replace("/items/", f"/items{'x' * (padding + 1)}/")
        oversized["nodes"][0]["metadata"]["normalizedPath"] = oversized_path
        oversized["boundedUrlProofs"][0]["normalizedPath"] = oversized_path
        self._assert_rejects(oversized, keys)

        accepted, accepted_keys = self._fragment()
        parsed = parse_bounded_url_proofs(accepted, accepted_keys)
        self.assertEqual(parsed[accepted_keys["request"]], BoundedUrlProof(
            1,
            accepted_keys["request"],
            "/items/{p0}/",
            (BoundedUrlPlaceholderProof("p0", 1, 2, ("int", "str")),),
        ))

class LiteralMergeTests(unittest.TestCase):
    repositories = [{"namespace": "backend"}, {"namespace": "frontend"}]
    repository_set_id = "0" * 64

    def _node(self, key: str, kind: str, metadata: dict, *, identity: str | None = None, reason: str | None = None) -> dict:
        label = key
        if kind == "http_call":
            label = f"{metadata['method']} {metadata['normalizedPath']}"
        elif kind == "request_payload":
            label = "Request payload"
        elif kind == "external_service":
            port = f":{metadata['port']}" if "port" in metadata else ""
            label = f"{metadata['method']} {metadata['scheme']}://{metadata['host']}{port}"
        elif kind == "unresolved_target":
            label = "Unresolved"
        return {
            "key": key,
            "kind": kind,
            "identity": identity or key,
            "label": label,
            "source": {"repository": "frontend", "path": "src/client.ts"},
            "metadata": metadata,
            **({"reason": reason} if reason else {}),
            **({"evidenceKind": "unresolved"} if kind == "unresolved_target" else {}),
        }

    def _frontend_fragment(self, *, payload: bool = False) -> dict:
        call = self._node(
            "call",
            "http_call",
            {
                "method": "GET",
                "urlResolution": "literal",
                "normalizedPath": "/items/",
                "endpointId": "GET /items/",
                "queryFieldCount": 0,
                "hasSensitiveQuery": False,
            },
        )
        unresolved = self._node(
            "placeholder",
            "unresolved_target",
            {"reasonCode": "dynamic_target_unproven"},
            reason="dynamic_target_unproven",
        )
        nodes = [call, unresolved]
        if payload:
            nodes.append(self._node(
                "payload",
                "request_payload",
                {
                    "payloadKinds": ["body"],
                    "bodyShape": "object",
                    "bodyFieldCount": 1,
                    "queryFieldCount": 0,
                    "hasSensitiveFields": False,
                },
            ))
            edges = [
                {"source": "call", "target": "payload", "kind": "carries", "metadata": {"payloadKinds": ["body"]}},
                {"source": "payload", "target": "placeholder", "kind": "resolves_to", "metadata": {"resolutionTier": "unbounded"}, "evidenceKind": "unresolved", "reason": "dynamic_target_unproven"},
            ]
        else:
            edges = [{"source": "call", "target": "placeholder", "kind": "resolves_to", "metadata": {"resolutionTier": "unbounded"}, "evidenceKind": "unresolved", "reason": "dynamic_target_unproven"}]
        return {
            "adapter": "test_adapter",
            "adapterVersion": "1",
            "repository": "frontend",
            "project": "test",
            "repositorySetId": self.repository_set_id,
            "repositories": self.repositories,
            "nodes": nodes,
            "edges": edges,
        }

    def _django_fragment(self, count: int) -> dict:
        nodes = []
        edges = []
        for index in range(count):
            node = self._node(
                f"url{index}",
                "django_url_pattern",
                {
                    "declaredPath": "/items/",
                    "normalizedPath": "/items/",
                    "endpointId": "GET /items/",
                    "converters": [],
                },
                identity=f"url{index}",
                reason="django_url_declaration",
            )
            node["source"] = {"repository": "backend", "path": "urls.py"}
            nodes.append(node)
            unresolved = self._node(
                f"url-missing{index}",
                "unresolved_target",
                {"reasonCode": "dynamic_target_unproven"},
                identity=f"url-missing{index}",
                reason="dynamic_target_unproven",
            )
            unresolved["source"] = {"repository": "backend", "path": "urls.py"}
            nodes.append(unresolved)
            edges.append(
                {
                    "source": f"url{index}",
                    "target": f"url-missing{index}",
                    "kind": "resolves_to",
                    "metadata": {"resolutionTier": "unbounded"},
                    "evidenceKind": "unresolved",
                    "reason": "dynamic_target_unproven",
                }
            )
        return {
            "adapter": "test_adapter",
            "adapterVersion": "1",
            "repository": "backend",
            "project": "test",
            "repositorySetId": self.repository_set_id,
            "repositories": self.repositories,
            "nodes": nodes,
            "edges": edges,
        }

    def _merge(self, count: int, *, payload: bool = False):
        frontend = canonicalize_fragment(self._frontend_fragment(payload=payload))
        django = canonicalize_fragment(self._django_fragment(count))
        return merge_canonical_fragments(frontend, django, active_manifest=self.repositories)

    def test_raw_literal_fragment_canonicalizes_before_merge(self) -> None:
        fragment = canonicalize_fragment(self._frontend_fragment())
        self.assertEqual(next(node for node in fragment.snapshot.nodes if node.kind == "http_call").metadata["endpointId"], "GET /items/")
        self.assertEqual(fragment.snapshot.edges[0].metadata["resolutionTier"], "unbounded")

    def test_literal_endpoint_proof_is_rejected_from_final_wire(self) -> None:
        fragment = canonicalize_fragment(self._frontend_fragment())
        call = next(node for node in fragment.snapshot.nodes if node.kind == "http_call")
        terminal = next(
            node for node in fragment.snapshot.nodes if node.kind == "unresolved_target"
        )
        self.assertEqual(terminal.metadata["reasonCode"], "dynamic_target_unproven")
        self.assertEqual(fragment.snapshot.edges[0].evidence[0].reason, "dynamic_target_unproven")
        self.assertEqual(call.metadata["endpointId"], "GET /items/")
        with self.assertRaises(ValueError):
            GraphSnapshotV2.from_dict(fragment.snapshot.to_dict())

    def test_unique_literal_match_replaces_payload_terminal(self) -> None:
        snapshot = self._merge(1, payload=True)
        call = next(node for node in snapshot.nodes if node.kind == "http_call")
        payload = next(node for node in snapshot.nodes if node.kind == "request_payload")
        resolutions = [
            edge
            for edge in snapshot.edges
            if edge.kind == "resolves_to" and edge.source in {call.id, payload.id}
        ]
        self.assertFalse(any(edge.source == call.id for edge in resolutions))
        self.assertEqual([(edge.source, edge.metadata["resolutionTier"]) for edge in resolutions], [(payload.id, "exact_endpoint")])
        self.assertFalse(
            any(
                node.kind == "unresolved_target" and node.source.repository == "frontend"
                for node in snapshot.nodes
            )
        )

    def test_no_literal_match_becomes_one_unresolved_target_and_diagnostic(self) -> None:
        snapshot = self._merge(0)
        terminal = next(
            node
            for node in snapshot.nodes
            if node.kind == "unresolved_target" and node.source.repository == "frontend"
        )
        self.assertEqual(terminal.metadata["reasonCode"], "url_target_unmatched")
        self.assertEqual([item["code"] for item in snapshot.diagnostics], ["url_target_unmatched"])
        call = next(node for node in snapshot.nodes if node.kind == "http_call")
        self.assertNotIn("endpointId", call.metadata)
        self.assertTrue(
            all(
                "targetRepository" not in edge.metadata
                for edge in snapshot.edges
                if edge.kind == "resolves_to" and edge.target == terminal.id
            )
        )

    def test_ambiguous_literal_match_becomes_one_unresolved_target_and_diagnostic(self) -> None:
        snapshot = self._merge(2)
        terminal = next(
            node
            for node in snapshot.nodes
            if node.kind == "unresolved_target" and node.source.repository == "frontend"
        )
        self.assertEqual(terminal.metadata["reasonCode"], "url_target_ambiguous")
        self.assertEqual(len(terminal.metadata["candidateIds"]), 2)
        self.assertEqual([item["code"] for item in snapshot.diagnostics], ["url_target_ambiguous"])


    def test_literal_method_and_repository_mismatches_are_unmatched(self) -> None:
        cases = (
            ("POST", None),
            ("GET", "frontend"),
            ("GET", "backend"),
        )
        for method, target_repository in cases:
            with self.subTest(method=method, target_repository=target_repository):
                frontend = self._frontend_fragment()
                metadata = frontend["nodes"][0]["metadata"]
                metadata["method"] = method
                frontend["nodes"][0]["label"] = f"{method} /items/"
                if target_repository:
                    metadata["targetRepository"] = target_repository
                    metadata["endpointId"] = f"{method} {target_repository}:/items/"
                else:
                    metadata["endpointId"] = f"{method} /items/"
                snapshot = merge_canonical_fragments(
                    canonicalize_fragment(frontend),
                    canonicalize_fragment(self._django_fragment(1)),
                    active_manifest=self.repositories,
                )
                terminals = [
                    node for node in snapshot.nodes
                    if node.kind == "unresolved_target" and node.source.repository == "frontend"
                ]
                if target_repository == "backend" and method == "GET":
                    self.assertFalse(terminals)
                else:
                    self.assertEqual(terminals[0].metadata["reasonCode"], "url_target_unmatched")

    def test_bounded_matching_is_method_converter_and_repository_aware(self) -> None:
        frontend = self._frontend_fragment()
        call = frontend["nodes"][0]
        call["metadata"] = {
            "method": "GET",
            "urlResolution": "bounded_template",
            "normalizedPath": "/items/{p0}/",
            "queryFieldCount": 0,
            "hasSensitiveQuery": False,
            "targetRepository": "backend",
        }
        call["label"] = "GET /items/{p0}/"
        frontend["boundedUrlProofs"] = [{
            "version": 1,
            "callKey": "call",
            "normalizedPath": "/items/{p0}/",
            "placeholders": [{
                "token": "p0",
                "segmentIndex": 1,
                "memberCount": 1,
                "acceptedConverters": ["int"],
            }],
        }]
        django = self._django_fragment(1)
        django["nodes"][0]["metadata"] = {
            "declaredPath": "/items/<int:item>/",
            "normalizedPath": "/items/{p0}/",
            "endpointId": "GET /items/{p0}/",
            "converters": [{"name": "item", "kind": "int", "segmentIndex": 1}],
        }
        snapshot = merge_canonical_fragments(
            canonicalize_fragment(frontend),
            canonicalize_fragment(django),
            active_manifest=self.repositories,
        )
        call = next(node for node in snapshot.nodes if node.kind == "http_call")
        self.assertEqual(call.metadata["endpointId"], "GET backend:/items/{p0}/")
        self.assertFalse(hasattr(snapshot, "bounded_url_proofs"))
        self.assertEqual(
            sorted(edge.metadata["resolutionTier"] for edge in snapshot.edges if edge.kind == "resolves_to"),
            ["dynamic_converter", "unbounded"],
        )
    def test_producer_converter_proofs_link_active_django_endpoints_without_values(self) -> None:
        cases = (
            ("int", ("int", "slug", "str")),
            ("uuid", ("slug", "str", "uuid")),
        )
        for converter, accepted_converters in cases:
            with self.subTest(converter=converter):
                frontend = self._frontend_fragment()
                frontend["nodes"][0]["metadata"] = {
                    "method": "GET",
                    "urlResolution": "bounded_template",
                    "normalizedPath": "/items/{p0}/",
                    "queryFieldCount": 0,
                    "hasSensitiveQuery": False,
                    "targetRepository": "backend",
                }
                frontend["nodes"][0]["label"] = "GET /items/{p0}/"
                frontend["boundedUrlProofs"] = [{
                    "version": 1,
                    "callKey": "call",
                    "normalizedPath": "/items/{p0}/",
                    "placeholders": [{
                        "token": "p0",
                        "segmentIndex": 1,
                        "memberCount": 1,
                        "acceptedConverters": list(accepted_converters),
                    }],
                }]
                django = self._django_fragment(1)
                django["nodes"][0]["metadata"] = {
                    "declaredPath": f"/items/<{converter}:item>/",
                    "normalizedPath": "/items/{p0}/",
                    "endpointId": "GET /items/{p0}/",
                    "converters": [{"name": "item", "kind": converter, "segmentIndex": 1}],
                }
                snapshot = merge_canonical_fragments(
                    canonicalize_fragment(frontend),
                    canonicalize_fragment(django),
                    active_manifest=self.repositories,
                )
                call = next(node for node in snapshot.nodes if node.kind == "http_call")
                endpoint = next(node for node in snapshot.nodes if node.kind == "django_url_pattern")
                self.assertEqual(call.metadata["endpointId"], "GET backend:/items/{p0}/")
                self.assertTrue(any(
                    edge.source == call.id
                    and edge.target == endpoint.id
                    and edge.metadata["resolutionTier"] == "dynamic_converter"
                    for edge in snapshot.edges
                ))
                self.assertFalse(hasattr(snapshot, "bounded_url_proofs"))
                self.assertNotIn("values", json.dumps(frontend["boundedUrlProofs"]))

    def test_bounded_candidate_cardinality_is_table_driven(self) -> None:
        cases = (
            ("zero", 0, "int", "GET", "backend", "url_target_unmatched"),
            ("converter", 1, "slug", "GET", "backend", "url_target_unmatched"),
            ("method", 1, "int", "POST", "backend", "url_target_unmatched"),
            ("repository", 1, "int", "GET", "frontend", "url_target_unmatched"),
            ("one", 1, "int", "GET", "backend", None),
            ("many", 2, "int", "GET", "backend", "url_target_ambiguous"),
        )
        for name, count, converter, method, repository, reason in cases:
            with self.subTest(name=name):
                frontend = self._frontend_fragment()
                frontend["nodes"][0]["metadata"] = {
                    "method": "GET",
                    "urlResolution": "bounded_template",
                    "normalizedPath": "/items/{p0}/",
                    "queryFieldCount": 0,
                    "hasSensitiveQuery": False,
                    "targetRepository": repository,
                }
                frontend["nodes"][0]["label"] = "GET /items/{p0}/"
                frontend["boundedUrlProofs"] = [{
                    "version": 1,
                    "callKey": "call",
                    "normalizedPath": "/items/{p0}/",
                    "placeholders": [{
                        "token": "p0",
                        "segmentIndex": 1,
                        "memberCount": 1,
                        "acceptedConverters": ["int"],
                    }],
                }]
                django = self._django_fragment(count)
                for node in django["nodes"]:
                    if node["kind"] == "django_url_pattern":
                        node["metadata"] = {
                            "declaredPath": f"/items/<{converter}:item>/",
                            "normalizedPath": "/items/{p0}/",
                            "endpointId": f"{method} /items/{{p0}}/",
                            "converters": [{
                                "name": "item",
                                "kind": converter,
                                "segmentIndex": 1,
                            }],
                        }
                frontend_fragment = canonicalize_fragment(frontend)
                django_fragment = canonicalize_fragment(django)
                snapshot = merge_canonical_fragments(
                    frontend_fragment,
                    django_fragment,
                    active_manifest=self.repositories,
                )
                terminals = [
                    node for node in snapshot.nodes
                    if node.kind == "unresolved_target" and node.source.repository == "frontend"
                ]
                call = next(node for node in snapshot.nodes if node.kind == "http_call")
                if reason is None:
                    self.assertFalse(terminals)
                else:
                    self.assertEqual(terminals[0].metadata["reasonCode"], reason)
                    self.assertNotIn("endpointId", call.metadata)
                    forged = snapshot.to_dict()
                    next(item for item in forged["nodes"] if item["id"] == call.id)["metadata"][
                        "endpointId"
                    ] = "GET backend:/items/{p0}/"
                    with self.assertRaises(ValueError):
                        GraphSnapshotV2.from_dict(forged)
    def test_canonical_duplicates_are_commutative_and_conflicts_are_typed(self) -> None:
        snapshot = self._merge(1)
        self.assertEqual(
            merge_snapshots(snapshot, snapshot),
            merge_snapshots(snapshot, snapshot),
        )
        changed_node = replace(snapshot.nodes[0], confidence=0.25)
        conflicting = replace(snapshot, nodes=[changed_node, *snapshot.nodes[1:]])
        with self.assertRaises(GraphIdentityConflict):
            merge_snapshots(snapshot, conflicting)
        fragment = canonicalize_fragment(self._frontend_fragment())
        proof_id = "n_" + "1" * 64
        first = CanonicalFragment(
            fragment.snapshot,
            {proof_id: BoundedUrlProof(1, proof_id, "/items/{p0}/", ())},
        )
        second = CanonicalFragment(
            fragment.snapshot,
            {proof_id: BoundedUrlProof(1, proof_id, "/other/{p0}/", ())},
        )
        with self.assertRaises(GraphIdentityConflict):
            merge_canonical_fragments(first, second, active_manifest=self.repositories)

    def test_route_order_is_independent_of_fragment_and_repository_order(self) -> None:
        frontend = self._frontend_fragment()
        frontend["nodes"].append(
            self._node(
                "route",
                "frontend_route",
                {"framework": "react", "declaredPath": "/items/"},
            )
        )
        frontend["routes"] = [{
            "key": "route",
            "repository": "frontend",
            "framework": "react",
            "path": "/items/",
        }]
        django = self._django_fragment(1)
        django["routes"] = [{
            "key": "url0",
            "repository": "backend",
            "framework": "django",
            "path": "/items/",
        }]
        forward = merge_canonical_fragments(
            canonicalize_fragment(frontend),
            canonicalize_fragment(django),
            active_manifest=self.repositories,
        )
        reverse = merge_canonical_fragments(
            canonicalize_fragment(django),
            canonicalize_fragment(frontend),
            active_manifest=self.repositories,
        )
        self.assertEqual(forward.routes, reverse.routes)
        self.assertEqual([route["framework"] for route in forward.routes], ["react", "django"])
    def test_fragment_order_is_commutative(self) -> None:
        frontend = canonicalize_fragment(self._frontend_fragment(payload=True))
        django = canonicalize_fragment(self._django_fragment(1))
        forward = merge_canonical_fragments(frontend, django, active_manifest=self.repositories)
        reverse = merge_canonical_fragments(django, frontend, active_manifest=self.repositories)
        self.assertEqual(forward, reverse)
    def test_fragment_ingress_and_dynamic_converter_wire_forgery_fail_closed(self) -> None:
        frontend = self._frontend_fragment()
        for mutation in (
            lambda value: value.update({"unknown": "value"}),
            lambda value: value["nodes"][0]["metadata"].update({"nested": {"unknown": "value"}}),
            lambda value: value["nodes"][0]["metadata"].update({"token": "Bearer abcdefgh"}),
            lambda value: value["edges"][0].update({"unknown": "value"}),
        ):
            forged = copy.deepcopy(frontend)
            mutation(forged)
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                canonicalize_fragment(forged)
        frontend = self._frontend_fragment()
        call = frontend["nodes"][0]
        call["metadata"] = {"method": "GET", "urlResolution": "bounded_template", "normalizedPath": "/items/{p0}/", "queryFieldCount": 0, "hasSensitiveQuery": False, "targetRepository": "backend"}
        call["label"] = "GET /items/{p0}/"
        frontend["boundedUrlProofs"] = [{"version": 1, "callKey": "call", "normalizedPath": "/items/{p0}/", "placeholders": [{"token": "p0", "segmentIndex": 1, "memberCount": 1, "acceptedConverters": ["int"]}]}]
        django = self._django_fragment(1)
        django["nodes"][0]["metadata"] = {"declaredPath": "/items/<int:item>/", "normalizedPath": "/items/{p0}/", "endpointId": "GET /items/{p0}/", "converters": [{"name": "item", "kind": "int", "segmentIndex": 1}]}
        snapshot = merge_canonical_fragments(canonicalize_fragment(frontend), canonicalize_fragment(django), active_manifest=self.repositories)
        for field, value in (("method", "POST"), ("normalizedPath", "/other/{p0}/"), ("urlResolution", "literal"), ("endpointId", "GET backend:/other/{p0}/")):
            forged = snapshot.to_dict()
            call_record = next(item for item in forged["nodes"] if item["kind"] == "http_call")
            call_record["metadata"][field] = value
            if field in {"method", "normalizedPath"}:
                call_record["label"] = f"{call_record['metadata']['method']} {call_record['metadata']['normalizedPath']}"
            with self.subTest(field=field), self.assertRaises(ValueError):
                GraphSnapshotV2.from_dict(forged)
    def test_raw_canonical_fragment_bounded_concrete_complements_fail_closed(self) -> None:
        frontend = self._frontend_fragment()
        call = frontend["nodes"][0]
        call["metadata"] = {
            "method": "GET",
            "urlResolution": "bounded_template",
            "normalizedPath": "/items/{p0}/",
            "queryFieldCount": 0,
            "hasSensitiveQuery": False,
            "targetRepository": "backend",
        }
        call["label"] = "GET /items/{p0}/"
        frontend["boundedUrlProofs"] = [{
            "version": 1,
            "callKey": "call",
            "normalizedPath": "/items/{p0}/",
            "placeholders": [{
                "token": "p0",
                "segmentIndex": 1,
                "memberCount": 1,
                "acceptedConverters": ["int"],
            }],
        }]
        django = self._django_fragment(1)
        django["nodes"][0]["metadata"] = {
            "declaredPath": "/items/<int:item>/",
            "normalizedPath": "/items/{p0}/",
            "endpointId": "GET /items/{p0}/",
            "converters": [{"name": "item", "kind": "int", "segmentIndex": 1}],
        }
        frontend_fragment = canonicalize_fragment(frontend)
        django_fragment = canonicalize_fragment(django)
        snapshot = merge_canonical_fragments(
            frontend_fragment,
            django_fragment,
            active_manifest=self.repositories,
        )
        for name, mutate in (
            (
                "exact tier",
                lambda call_record, edge_record: edge_record["metadata"].update(
                    {"resolutionTier": "exact_endpoint"}
                ),
            ),
            (
                "configured tier",
                lambda call_record, edge_record: edge_record["metadata"].update(
                    {"resolutionTier": "configured_base"}
                ),
            ),
            (
                "wrong edge repository",
                lambda call_record, edge_record: edge_record["metadata"].update(
                    {"targetRepository": "frontend"}
                ),
            ),
            (
                "unbounded owner",
                lambda call_record, edge_record: call_record["metadata"].update(
                    {"urlResolution": "unbounded"}
                ),
            ),
        ):
            forged = copy.deepcopy(snapshot.to_dict())
            call_record = next(item for item in forged["nodes"] if item["kind"] == "http_call")
            edge_record = next(
                item
                for item in forged["edges"]
                if item["kind"] == "resolves_to"
                and item["metadata"]["resolutionTier"] == "dynamic_converter"
            )
            mutate(call_record, edge_record)
            with self.subTest(name=name), self.assertRaises(ValueError):
                GraphSnapshotV2.from_dict(forged)
if __name__ == "__main__":
    unittest.main()
