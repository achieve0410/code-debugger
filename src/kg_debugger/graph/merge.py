from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .contracts import (
    DIAGNOSTIC_ID_RE,
    EDGE_ID_RE,
    NODE_ID_RE,
    PYTHON_QUALIFIED_NAME_RE,
    ROUTE_ID_RE,
    SERVER_EVENT_ID_RE,
    is_secret_key,
    is_secret_value,
    route_sort_key,
)
from .identity import diagnostic_identity, edge_identity, node_identity, route_identity
from .proof import BoundedUrlProofError, CanonicalFragment, parse_bounded_url_proofs
from .schema import (
    DEFAULT_LAYER_BY_KIND,
    Edge,
    Evidence,
    GraphSnapshotV2,
    Node,
    SourceLocation,
)


class FragmentValidationError(ValueError):
    pass
class GraphIdentityConflict(ValueError):
    """Raised when equal canonical IDs carry different canonical values."""


def _deduplicate_by_id(items: Iterable[Any], *, name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        previous = result.setdefault(item.id, item)
        if previous != item:
            raise GraphIdentityConflict(f"canonical {name} conflict")
    return result


def _deduplicate_mapping_items(items: Iterable[dict[str, Any]], *, name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        previous = result.setdefault(item["id"], item)
        if previous != item:
            raise GraphIdentityConflict(f"canonical {name} conflict")
    return result


def _endpoint_id(method: str, path: str, repository: str | None = None) -> str:
    return f"{method} {repository + ':' if repository else ''}{path}"


def _target_method(target: Node) -> str | None:
    endpoint_id = target.metadata["endpointId"]
    method, separator, path = endpoint_id.partition(" ")
    return method if separator and path == target.metadata["normalizedPath"] else None


def _evidence(adapter: str, version: str, kind: str, reason: str | None = None) -> Evidence:
    return Evidence(kind=kind, adapter=adapter, adapterVersion=version, reason=reason)


def _diagnostic(code: str, *, repository: str, node_id: str | None = None, candidate_ids: list[str] | None = None) -> dict[str, Any]:
    from .contracts import DIAGNOSTIC_CATALOG
    spec = DIAGNOSTIC_CATALOG[code]
    result: dict[str, Any] = {"code": code, "severity": spec.severity, "message": spec.message, "repository": repository}
    if node_id is not None:
        result["nodeId"] = node_id
    if candidate_ids is not None:
        result["candidateIds"] = candidate_ids
    fields = tuple(__import__("json").dumps(result.get(key), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for key in ("code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"))
    return {"id": diagnostic_identity(*fields), **result}


def _exact_fragment_object(
    value: Any, required: set[str], optional: set[str], name: str
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or not required <= set(value)
        or set(value) - required - optional
    ):
        raise ValueError(f"invalid fragment {name}")
    return dict(value)


def _reject_fragment_secret_material(value: Any, *, key: str | None = None) -> None:
    if key is not None and is_secret_key(key) and key not in {"hasSensitiveQuery", "hasSensitiveFields", "token"}:
        raise ValueError("secret-like key")
    if isinstance(value, str):
        if (
            key == "pythonQualifiedName"
            and PYTHON_QUALIFIED_NAME_RE.fullmatch(value)
            or key == "repositorySetId"
            and re.fullmatch(r"[0-9a-f]{64}", value)
            or key == "id"
            and any(pattern.fullmatch(value) for pattern in (NODE_ID_RE, EDGE_ID_RE, ROUTE_ID_RE, DIAGNOSTIC_ID_RE))
            or key == "nodeId"
            and NODE_ID_RE.fullmatch(value)
            or key == "edgeId"
            and EDGE_ID_RE.fullmatch(value)
            or key == "eventId"
            and SERVER_EVENT_ID_RE.fullmatch(value)
        ):
            return
        if is_secret_value(value):
            raise ValueError("secret-like value")
        return
    if key == "candidateIds" and isinstance(value, list) and value and all(
        isinstance(item, str) and NODE_ID_RE.fullmatch(item) for item in value
    ):
        return
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                raise ValueError("invalid object key")
            _reject_fragment_secret_material(child, key=child_key)
    elif isinstance(value, list):
        for child in value:
            _reject_fragment_secret_material(child)

def _validate_fragment_shape(fragment: Mapping[str, Any]) -> None:
    _exact_fragment_object(
        fragment,
        {"adapter", "adapterVersion", "repository", "repositorySetId", "repositories", "nodes", "edges"},
        {"project", "evidenceKind", "routes", "diagnostics", "boundedUrlProofs"},
        "header",
    )
    _reject_fragment_secret_material(fragment)
    collections = {
        "nodes": ({"key", "kind", "source", "metadata"}, {"identity", "label", "layer", "evidenceKind", "reason", "confidence"}),
        "edges": ({"source", "target", "kind"}, {"metadata", "evidenceKind", "reason", "confidence"}),
        "routes": ({"framework", "path"}, {"id", "key", "nodeId", "repository"}),
    }
    for name, (required, optional) in collections.items():
        values = fragment.get(name, [])
        if not isinstance(values, list):
            raise ValueError(f"invalid fragment {name}")
        for value in values:
            _exact_fragment_object(value, required, optional, name[:-1])
    if not isinstance(fragment.get("diagnostics", []), list):
        raise ValueError("invalid fragment diagnostics")

def canonicalize_fragment(fragment: Mapping[str, Any]) -> CanonicalFragment:
    """Canonicalize an adapter fragment while retaining only its ephemeral proof envelope."""
    try:
        _validate_fragment_shape(fragment)
        adapter = fragment["adapter"]
        version = fragment["adapterVersion"]
        repository = fragment["repository"]
        project = fragment.get("project", repository)
        repository_set_id = fragment["repositorySetId"]
        repositories = fragment["repositories"]
        evidence_kind = fragment.get("evidenceKind", "inferred")
        if not all(isinstance(value, str) for value in (adapter, version, repository, project)):
            raise ValueError("invalid fragment header")
        key_to_id: dict[str, str] = {}
        nodes: list[Node] = []
        for raw in fragment.get("nodes", []):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("key"), str) or raw["key"] in key_to_id:
                raise ValueError("invalid fragment node key")
            source = SourceLocation.from_dict(raw["source"])
            if source.repository != repository:
                raise ValueError("fragment node repository mismatch")
            kind = raw["kind"]
            identity = raw.get("identity", source.symbol or raw["key"])
            if not isinstance(identity, str):
                raise ValueError("invalid fragment identity")
            node_id = node_identity(repository, source.path, kind, identity)
            key_to_id[raw["key"]] = node_id
            nodes.append(Node(node_id, kind, identity, raw.get("label", identity), raw.get("layer", DEFAULT_LAYER_BY_KIND[kind]), source, [_evidence(adapter, version, raw.get("evidenceKind", evidence_kind), raw.get("reason"))], raw.get("confidence", 0.5), dict(raw.get("metadata", {}))))
        proofs = parse_bounded_url_proofs(fragment, key_to_id)
        edges: list[Edge] = []
        for raw in fragment.get("edges", []):
            if not isinstance(raw, Mapping):
                raise ValueError("invalid fragment edge")
            raw_source = raw.get("source")
            raw_target = raw.get("target")
            edge_source = key_to_id.get(raw_source, raw_source) if isinstance(raw_source, str) else raw_source
            edge_target = key_to_id.get(raw_target, raw_target) if isinstance(raw_target, str) else raw_target
            if not isinstance(edge_source, str) or not isinstance(edge_target, str):
                raise ValueError("invalid fragment edge reference")
            kind = raw["kind"]
            edges.append(Edge(edge_identity(edge_source, edge_target, kind), edge_source, edge_target, kind, [_evidence(adapter, version, raw.get("evidenceKind", evidence_kind), raw.get("reason"))], raw.get("confidence", 0.5), dict(raw.get("metadata", {}))))
        routes = []
        for raw in fragment.get("routes", []):
            raw_key = raw.get("key")
            raw_node_id = raw.get("nodeId")
            if isinstance(raw_key, str) == isinstance(raw_node_id, str):
                raise ValueError("invalid fragment route reference")
            route_node_id = key_to_id.get(raw_key, raw_node_id) if isinstance(raw_key, str) else raw_node_id
            route_repository = raw.get("repository", repository)
            framework = raw.get("framework")
            path = raw.get("path")
            if (
                not isinstance(route_node_id, str)
                or not isinstance(route_repository, str)
                or not isinstance(framework, str)
                or not isinstance(path, str)
            ):
                raise ValueError("invalid fragment route")
            route_id = route_identity(route_repository, framework, path, route_node_id)
            if "id" in raw and raw["id"] != route_id:
                raise ValueError("invalid fragment route id")
            routes.append(
                {
                    "id": route_id,
                    "repository": route_repository,
                    "framework": framework,
                    "path": path,
                    "nodeId": route_node_id,
                }
            )
        routes.sort(key=route_sort_key)
        snapshot = GraphSnapshotV2(project, repository_set_id, repositories, routes, sorted(nodes, key=lambda item: item.id), sorted(edges, key=lambda item: item.id), list(fragment.get("diagnostics", [])), fragment_validation=True)
        return CanonicalFragment(snapshot, proofs)
    except (KeyError, TypeError, ValueError, BoundedUrlProofError) as exc:
        raise FragmentValidationError("bounded_url_proof_invalid" if isinstance(exc, BoundedUrlProofError) else "fragment_invalid") from exc


def _manifest_namespaces(active_manifest: Any) -> set[str]:
    if isinstance(active_manifest, GraphSnapshotV2):
        return {item["namespace"] for item in active_manifest.repositories}
    if isinstance(active_manifest, Mapping):
        values = active_manifest.get("repositories", active_manifest.get("namespaces", []))
    else:
        values = active_manifest
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        raise ValueError("invalid active manifest")
    result = {item["namespace"] if isinstance(item, Mapping) else item for item in values}
    if not result or any(not isinstance(item, str) for item in result):
        raise ValueError("invalid active manifest")
    return result


def _unresolved(call: Node, reason: str, candidates: list[str] | None = None) -> Node:
    structural_id = ":".join(call.id[2:][index : index + 4] for index in range(0, 64, 4))
    identity = f"unresolved:{structural_id}:{reason}"
    metadata: dict[str, Any] = {"reasonCode": reason}
    if candidates:
        metadata["candidateIds"] = candidates
    return Node(node_identity(call.source.repository, call.source.path, "unresolved_target", identity), "unresolved_target", identity, "Unresolved", "unresolved", call.source, [_evidence("kg_debugger.merge", "2", "unresolved", reason)], call.confidence, metadata)


def _resolution_edge(source: Node, target: Node, tier: str) -> Edge:
    reason = {
        "dynamic_converter": "dynamic_converter",
        "exact_endpoint": "exact_endpoint",
    }.get(tier, "dynamic_target_unproven")
    metadata: dict[str, Any] = {"resolutionTier": tier}
    if tier in {"dynamic_converter", "exact_endpoint"}:
        metadata["targetRepository"] = target.source.repository
    evidence_kind = "unresolved" if tier == "unbounded" else "inferred"
    return Edge(edge_identity(source.id, target.id, "resolves_to"), source.id, target.id, "resolves_to", [_evidence("kg_debugger.merge", "2", evidence_kind, reason)], min(source.confidence, target.confidence), metadata)


def merge_canonical_fragments(*fragments: CanonicalFragment, active_manifest: Any) -> GraphSnapshotV2:
    """Consume bounded proofs once against the active Django manifest, then discard them."""
    if not fragments:
        raise ValueError("no fragments")
    manifest = _manifest_namespaces(active_manifest)
    snapshots = [fragment.snapshot for fragment in fragments]
    first = snapshots[0]
    if any(snapshot.project != first.project or snapshot.repositorySetId != first.repositorySetId or snapshot.repositories != first.repositories for snapshot in snapshots):
        raise ValueError("fragment manifests differ")
    if manifest != {item["namespace"] for item in first.repositories}:
        raise ValueError("active manifest mismatch")
    nodes = _deduplicate_by_id(
        (node for snapshot in snapshots for node in snapshot.nodes), name="node"
    )
    edges = _deduplicate_by_id(
        (edge for snapshot in snapshots for edge in snapshot.edges), name="edge"
    )
    routes = _deduplicate_mapping_items(
        (route for snapshot in snapshots for route in snapshot.routes), name="route"
    )
    diagnostics = _deduplicate_mapping_items(
        (item for snapshot in snapshots for item in snapshot.diagnostics), name="diagnostic"
    )
    proofs: dict[str, Any] = {}
    for fragment in fragments:
        for call_id, proof in fragment.bounded_url_proofs.items():
            previous = proofs.setdefault(call_id, proof)
            if previous != proof:
                raise GraphIdentityConflict("canonical bounded URL proof conflict")
    bounded_calls = [
        node
        for node in nodes.values()
        if node.kind == "http_call"
        and node.metadata["urlResolution"] == "bounded_template"
        and "endpointId" not in node.metadata
    ]
    literal_calls = [
        node
        for node in nodes.values()
        if node.kind == "http_call"
        and node.metadata["urlResolution"] == "literal"
        and "endpointId" in node.metadata
    ]
    if set(proofs) != {call.id for call in bounded_calls}:
        raise ValueError("bounded proof envelope mismatch")
    payloads: dict[str, list[Node]] = {}
    for edge in edges.values():
        target = nodes.get(edge.target)
        if edge.kind == "carries" and target is not None and target.kind == "request_payload":
            payloads.setdefault(edge.source, []).append(target)
    targets = [node for node in nodes.values() if node.kind == "django_url_pattern" and node.source.repository in manifest]
    replaced_placeholder_ids: set[str] = set()
    for call in bounded_calls + literal_calls:
        candidates: list[Node] = []
        if call.metadata["urlResolution"] == "bounded_template":
            proof = proofs[call.id]
            for target in targets:
                if call.metadata.get("targetRepository") and target.source.repository != call.metadata["targetRepository"]:
                    continue
                converters = target.metadata["converters"]
                if (
                    _target_method(target) != call.metadata["method"]
                    or target.metadata["normalizedPath"] != proof.normalizedPath
                    or len(converters) != len(proof.placeholders)
                ):
                    continue
                by_position = {item["segmentIndex"]: item for item in converters}
                if len(by_position) != len(converters):
                    continue
                if all((converter := by_position.get(item.segmentIndex)) is not None and converter["kind"] in {"int", "str", "slug", "uuid"} and converter["kind"] in item.acceptedConverters for item in proof.placeholders):
                    candidates.append(target)
            tier = "dynamic_converter"
        else:
            for target in targets:
                target_repository = call.metadata.get("targetRepository")
                if target_repository and target.source.repository != target_repository:
                    continue
                if (
                    _target_method(target) == call.metadata["method"]
                    and target.metadata["normalizedPath"] == call.metadata["normalizedPath"]
                    and call.metadata["endpointId"] == _endpoint_id(
                        call.metadata["method"], target.metadata["normalizedPath"], target_repository
                    )
                ):
                    candidates.append(target)
            tier = "exact_endpoint"
        sources = payloads.get(call.id, []) or [call]
        if len(candidates) == 1:
            target = candidates[0]
            if tier == "dynamic_converter":
                metadata = dict(call.metadata)
                metadata["targetRepository"] = target.source.repository
                metadata["endpointId"] = _endpoint_id(
                    call.metadata["method"],
                    target.metadata["normalizedPath"],
                    target.source.repository,
                )
                nodes[call.id] = replace(call, metadata=metadata)
            for source in sources:
                for identifier, existing in list(edges.items()):
                    existing_target = nodes.get(existing.target)
                    if existing.kind == "resolves_to" and existing.source == source.id and existing_target is not None and existing_target.kind == "unresolved_target":
                        replaced_placeholder_ids.add(existing_target.id)
                        del edges[identifier]
                edge = _resolution_edge(source, target, tier)
                edges[edge.id] = edge
        else:
            reason = "url_target_unmatched" if not candidates else "url_target_ambiguous"
            candidate_ids = sorted(item.id for item in candidates)
            unresolved = _unresolved(call, reason, candidate_ids or None)
            nodes[unresolved.id] = unresolved
            call_metadata = dict(call.metadata)
            call_metadata.pop("endpointId", None)
            nodes[call.id] = replace(call, metadata=call_metadata)
            for source in sources:
                for identifier, existing in list(edges.items()):
                    existing_target = nodes.get(existing.target)
                    if existing.kind == "resolves_to" and existing.source == source.id and existing_target is not None and existing_target.kind == "unresolved_target":
                        replaced_placeholder_ids.add(existing_target.id)
                        del edges[identifier]
                edge = _resolution_edge(source, unresolved, "unbounded")
                edges[edge.id] = edge
            diagnostic = _diagnostic(reason, repository=call.source.repository, node_id=call.id, candidate_ids=candidate_ids or None)
            diagnostics[diagnostic["id"]] = diagnostic
    referenced_ids = {edge.target for edge in edges.values()}
    for node_id in replaced_placeholder_ids - referenced_ids:
        del nodes[node_id]
    return GraphSnapshotV2(first.project, first.repositorySetId, first.repositories, sorted(routes.values(), key=route_sort_key), sorted(nodes.values(), key=lambda item: item.id), sorted(edges.values(), key=lambda item: item.id), sorted(diagnostics.values(), key=lambda item: item["id"]))


def merge_snapshots(*snapshots: GraphSnapshotV2) -> GraphSnapshotV2:
    """Merge already-canonical values only; it deliberately cannot create computed links."""
    if not snapshots:
        raise ValueError("no snapshots")
    first = snapshots[0]
    if any(snapshot.project != first.project or snapshot.repositorySetId != first.repositorySetId or snapshot.repositories != first.repositories for snapshot in snapshots):
        raise ValueError("snapshot manifests differ")
    nodes = _deduplicate_by_id(
        (node for snapshot in snapshots for node in snapshot.nodes), name="node"
    )
    edges = _deduplicate_by_id(
        (edge for snapshot in snapshots for edge in snapshot.edges), name="edge"
    )
    routes = _deduplicate_mapping_items(
        (route for snapshot in snapshots for route in snapshot.routes), name="route"
    )
    diagnostics = _deduplicate_mapping_items(
        (item for snapshot in snapshots for item in snapshot.diagnostics), name="diagnostic"
    )
    return GraphSnapshotV2(first.project, first.repositorySetId, first.repositories, sorted(routes.values(), key=route_sort_key), sorted(nodes.values(), key=lambda item: item.id), sorted(edges.values(), key=lambda item: item.id), sorted(diagnostics.values(), key=lambda item: item["id"]))
