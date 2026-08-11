from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import InitVar, dataclass, field
from typing import Any, NoReturn

from .contracts import (
    EDGE_ID_RE,
    EDGE_METADATA_SCHEMAS,
    FRAMEWORKS,
    NAMESPACE_RE,
    NODE_ID_RE,
    ROUTE_ID_RE,
    SAFE_TOKEN_RE,
    format_external_authority,
    is_secret_value,
    reject_secret_material,
    validate_diagnostic,
    validate_evidence_reason,
    validate_metadata,
    validate_millisecond_utc,
    validate_route_order,
    validate_server_event_id,
)
from .identity import edge_identity, node_identity, normalize_repo_path, route_identity

EVIDENCE_KINDS = frozenset({"observed", "inferred", "unresolved"})
NODE_KINDS = frozenset({"frontend_route", "page", "component", "ui_event", "function", "http_call", "request_payload", "django_url_pattern", "django_view", "model", "query_boundary", "external_service", "unresolved_target"})
EDGE_KINDS = frozenset(EDGE_METADATA_SCHEMAS)
LAYERS = frozenset({"frontend", "http", "backend", "data", "external", "unresolved"})
DEFAULT_LAYER_BY_KIND = {"frontend_route": "frontend", "page": "frontend", "component": "frontend", "ui_event": "frontend", "function": "backend", "http_call": "http", "request_payload": "http", "django_url_pattern": "backend", "django_view": "backend", "model": "data", "query_boundary": "data", "external_service": "external", "unresolved_target": "unresolved"}
LEGAL_LAYERS_BY_KIND = {**{kind: frozenset({layer}) for kind, layer in DEFAULT_LAYER_BY_KIND.items()}, "function": frozenset({"frontend", "backend"})}
LEGAL_EDGE_TUPLES = {
    "renders": frozenset({("frontend_route", "page"), ("frontend_route", "component"), ("page", "component"), ("component", "component")} ),
    "contains": frozenset({("page", "ui_event"), ("component", "ui_event"), ("page", "function"), ("component", "function")} ),
    "handles": frozenset({("ui_event", "function"), ("ui_event", "unresolved_target")} ),
    "navigates_to": frozenset({("page", "frontend_route"), ("component", "frontend_route"), ("function", "frontend_route"), ("page", "unresolved_target"), ("component", "unresolved_target"), ("function", "unresolved_target")} ),
    "calls": frozenset({("page", "http_call"), ("component", "http_call"), ("function", "http_call"), ("page", "function"), ("component", "function"), ("function", "function"), ("django_view", "function"), ("function", "external_service"), ("django_view", "external_service"), ("page", "unresolved_target"), ("component", "unresolved_target"), ("function", "unresolved_target"), ("django_view", "unresolved_target")} ),
    "carries": frozenset({("http_call", "request_payload")} ),
    "resolves_to": frozenset({("http_call", "django_url_pattern"), ("http_call", "external_service"), ("http_call", "unresolved_target"), ("request_payload", "django_url_pattern"), ("request_payload", "external_service"), ("request_payload", "unresolved_target"), ("django_url_pattern", "django_view"), ("django_url_pattern", "unresolved_target")} ),
    "invokes": frozenset({("django_view", "function"), ("function", "function"), ("django_view", "unresolved_target"), ("function", "unresolved_target")} ),
    "accesses": frozenset({("django_view", "query_boundary"), ("function", "query_boundary"), ("query_boundary", "model"), ("query_boundary", "unresolved_target")} ),
    "branches_to": frozenset({("page", "unresolved_target"), ("component", "unresolved_target"), ("function", "unresolved_target"), ("django_view", "unresolved_target")} ),
}


def _fail(message: str) -> NoReturn:
    raise ValueError(message)
def _exact(data: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != keys:
        _fail(f"invalid {name}")
    return data

def _safe_string(
    value: Any,
    name: str,
    minimum: int,
    maximum: int,
    *,
    allow_generic_token: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or unicodedata.normalize("NFC", value) != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        _fail(f"invalid {name}")
    if allow_generic_token:
        if is_secret_value(value, include_generic=False):
            _fail("secret-like value")
    else:
        reject_secret_material(value)
    return value

def _finite(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        _fail("invalid confidence")
    return float(value)
def _expected_label(kind: str, metadata: dict[str, Any]) -> str | None:
    if kind == "http_call":
        return f"{metadata['method']} {metadata['normalizedPath']}"
    if kind == "request_payload":
        return "Request payload"
    if kind == "external_service":
        authority = format_external_authority(
            metadata["host"], metadata.get("port")
        )
        return f"{metadata['method']} {metadata['scheme']}://{authority}"
    if kind == "unresolved_target":
        return "Unresolved"
    return None


@dataclass(frozen=True)
class SourceLocation:
    repository: str
    path: str
    line: int | None = None
    endLine: int | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.repository, str) or not NAMESPACE_RE.fullmatch(
            self.repository
        ):
            _fail("invalid source repository")
        normalized = normalize_repo_path(self.path)
        if not 1 <= len(normalized) <= 2048:
            _fail("invalid source path")
        reject_secret_material(normalized)
        object.__setattr__(self, "path", normalized)
        for name, value in (("line", self.line), ("endLine", self.endLine)):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 10_000_000
            ):
                _fail(f"invalid source {name}")
        if self.endLine is not None and self.line is not None and self.endLine < self.line:
            _fail("invalid source endLine")
        if self.symbol is not None:
            _safe_string(
                self.symbol,
                "source symbol",
                1,
                512,
                allow_generic_token=True,
            )
            if not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
                self.symbol,
            ):
                _fail("invalid source symbol")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"repository": self.repository, "path": self.path}
        if self.line is not None:
            result["line"] = self.line
        if self.endLine is not None:
            result["endLine"] = self.endLine
        if self.symbol is not None:
            result["symbol"] = self.symbol
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceLocation:
        allowed = {"repository", "path", "line", "endLine", "symbol"}
        if (
            not isinstance(data, dict)
            or set(data) - allowed
            or not {"repository", "path"} <= set(data)
        ):
            _fail("invalid source")
        if data["path"] != normalize_repo_path(data["path"]):
            _fail("source path is not canonical")
        return cls(data["repository"], data["path"], data.get("line"), data.get("endLine"), data.get("symbol"))

@dataclass(frozen=True)
class Evidence:
    kind: str
    adapter: str
    adapterVersion: str
    reason: str | None = None
    eventId: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        if (
            self.kind not in EVIDENCE_KINDS
            or not isinstance(self.adapter, str)
            or not SAFE_TOKEN_RE.fullmatch(self.adapter)
            or not isinstance(self.adapterVersion, str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", self.adapterVersion
            )
        ):
            _fail("invalid evidence")
        if self.reason is not None:
            validate_evidence_reason(self.kind, self.reason, persistable=False)
        if self.kind == "observed":
            if (
                self.eventId is None
                or self.timestamp is None
            ):
                _fail("invalid observed evidence")
            validate_server_event_id(self.eventId)
            validate_millisecond_utc(self.timestamp)
        elif self.eventId is not None or self.timestamp is not None:
            _fail("runtime fields require observed evidence")

    def to_dict(self) -> dict[str, Any]:
        result = {"kind": self.kind, "adapter": self.adapter, "adapterVersion": self.adapterVersion}
        if self.reason is not None:
            result["reason"] = self.reason
        if self.kind == "observed":
            event_id = self.eventId
            timestamp = self.timestamp
            if not isinstance(event_id, str) or not isinstance(timestamp, str):
                _fail("invalid observed evidence")
            result.update(eventId=event_id, timestamp=timestamp)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        if not isinstance(data, dict):
            _fail("invalid evidence")
        required = {"kind", "adapter", "adapterVersion"}
        allowed = required | {"reason", "eventId", "timestamp"}
        if not required <= set(data) or set(data) - allowed:
            _fail("invalid evidence")
        return cls(data["kind"], data["adapter"], data["adapterVersion"], data.get("reason"), data.get("eventId"), data.get("timestamp"))

@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    identityKey: str
    label: str
    layer: str
    source: SourceLocation
    evidence: list[Evidence]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            self.kind not in NODE_KINDS
            or self.layer not in LEGAL_LAYERS_BY_KIND[self.kind]
        ):
            _fail("invalid node kind or layer")
        _safe_string(
            self.identityKey,
            "identityKey",
            1,
            512,
            allow_generic_token=True,
        )
        _safe_string(
            self.label,
            "label",
            1,
            256,
            allow_generic_token=(
                self.source.symbol is not None
                and self.label == self.source.symbol.rsplit(".", 1)[-1]
            ),
        )
        if (
            self.id
            != node_identity(
                self.source.repository, self.source.path, self.kind, self.identityKey
            )
            or not NODE_ID_RE.fullmatch(self.id)
        ):
            _fail("node id does not match identity")
        _finite(self.confidence)
        if (
            not isinstance(self.evidence, list)
            or not 1 <= len(self.evidence) <= 32
            or self.evidence
            != sorted(self.evidence, key=lambda item: tuple(item.to_dict().items()))
            or len({tuple(item.to_dict().items()) for item in self.evidence})
            != len(self.evidence)
        ):
            _fail("invalid node evidence order")
        validate_metadata(self.kind, self.layer, self.metadata, phase="transient")
        expected_label = _expected_label(self.kind, self.metadata)
        if expected_label is not None and self.label != expected_label:
            _fail("node label does not match metadata")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "identityKey": self.identityKey,
            "label": self.label,
            "layer": self.layer,
            "source": self.source.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence": self.confidence,
            "metadata": self.metadata,
        }
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        _exact(data, {"id", "kind", "identityKey", "label", "layer", "source", "evidence", "confidence", "metadata"}, "node")
        if not isinstance(data["evidence"], list):
            _fail("invalid node")
        return cls(data["id"], data["kind"], data["identityKey"], data["label"], data["layer"], SourceLocation.from_dict(data["source"]), [Evidence.from_dict(item) for item in data["evidence"]], data["confidence"], data["metadata"])

@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    kind: str
    evidence: list[Evidence]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            self.kind not in EDGE_KINDS
            or not isinstance(self.source, str)
            or not isinstance(self.target, str)
            or self.source == self.target
            or self.id != edge_identity(self.source, self.target, self.kind)
            or not EDGE_ID_RE.fullmatch(self.id)
        ):
            _fail("invalid edge")
        _finite(self.confidence)
        if (
            not isinstance(self.evidence, list)
            or not 1 <= len(self.evidence) <= 32
            or self.evidence
            != sorted(self.evidence, key=lambda item: tuple(item.to_dict().items()))
            or len({tuple(item.to_dict().items()) for item in self.evidence})
            != len(self.evidence)
        ):
            _fail("invalid edge evidence order")
        validate_metadata(self.kind, "", self.metadata, phase="transient")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence": self.confidence,
            "metadata": self.metadata,
        }
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        _exact(data, {"id", "source", "target", "kind", "evidence", "confidence", "metadata"}, "edge")
        if not isinstance(data["evidence"], list):
            _fail("invalid edge")
        return cls(data["id"], data["source"], data["target"], data["kind"], [Evidence.from_dict(item) for item in data["evidence"]], data["confidence"], data["metadata"])

@dataclass(frozen=True)
class GraphSnapshotV2:
    project: str
    repositorySetId: str
    repositories: list[dict[str, str]]
    routes: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    schemaVersion: int = 2
    fragment_validation: InitVar[bool] = False

    def __post_init__(self, fragment_validation: bool) -> None:
        if isinstance(self.schemaVersion, bool) or self.schemaVersion != 2:
            _fail("unsupported schemaVersion")
        _safe_string(self.project, "project", 1, 128)
        if not isinstance(self.repositorySetId, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.repositorySetId
        ):
            _fail("invalid repositorySetId")
        if (
            not isinstance(self.repositories, list)
            or not 1 <= len(self.repositories) <= 64
            or self.repositories
            != sorted(self.repositories, key=lambda item: item.get("namespace", ""))
        ):
            _fail("invalid repositories")
        namespaces = []
        for repository in self.repositories:
            _exact(repository, {"namespace"}, "repository")
            namespace = repository["namespace"]
            if not isinstance(namespace, str) or not NAMESPACE_RE.fullmatch(namespace):
                _fail("invalid namespace")
            namespaces.append(namespace)
        if len({item.casefold() for item in namespaces}) != len(namespaces):
            _fail("duplicate repository namespace")
        if not isinstance(fragment_validation, bool):
            _fail("invalid fragment validation phase")
        self._validate_collections(set(namespaces), fragment_validation)

    def _validate_collections(
        self, namespaces: set[str], fragment_validation: bool
    ) -> None:
        for collection, maximum, name in (
            (self.routes, 10_000, "routes"),
            (self.nodes, 50_000, "nodes"),
            (self.edges, 200_000, "edges"),
            (self.diagnostics, 10_000, "diagnostics"),
        ):
            if not isinstance(collection, list) or len(collection) > maximum:
                _fail(f"invalid {name}")
        node_by_id = {node.id: node for node in self.nodes}
        if len(node_by_id) != len(self.nodes) or self.nodes != sorted(
            self.nodes, key=lambda node: node.id
        ):
            _fail("nodes are not canonically sorted")
        for node in self.nodes:
            if node.source.repository not in namespaces:
                _fail("node repository is absent from manifest")
        edge_by_id = {edge.id: edge for edge in self.edges}
        if len(edge_by_id) != len(self.edges) or self.edges != sorted(
            self.edges, key=lambda edge: edge.id
        ):
            _fail("edges are not canonically sorted")
        incoming: dict[str, list[Edge]] = {}
        outgoing: dict[str, list[Edge]] = {}
        for edge in self.edges:
            incoming.setdefault(edge.target, []).append(edge)
            outgoing.setdefault(edge.source, []).append(edge)
            if (
                edge.source not in node_by_id
                or edge.target not in node_by_id
                or (node_by_id[edge.source].kind, node_by_id[edge.target].kind)
                not in LEGAL_EDGE_TUPLES[edge.kind]
            ):
                _fail("illegal edge tuple")
        self._validate_edge_cross_fields(
            namespaces, node_by_id, incoming, fragment_validation
        )
        self._validate_payload_topology(node_by_id, incoming, outgoing)
        route_keys: set[tuple[str, str, str]] = set()
        route_ids: set[str] = set()
        for route in self.routes:
            _exact(route, {"id", "repository", "framework", "path", "nodeId"}, "route")
            if (
                route["repository"] not in namespaces
                or route.get("framework") not in FRAMEWORKS
                or not isinstance(route.get("path"), str)
                or not route["path"].startswith("/")
                or route["nodeId"] not in node_by_id
            ):
                _fail("invalid route")
            from .contracts import _path
            _path(route["path"])
            expected = route_identity(route["repository"], route["framework"], route["path"], route["nodeId"])
            if route["id"] != expected or not ROUTE_ID_RE.fullmatch(route["id"]):
                _fail("invalid route id")
            expected_kind = "django_url_pattern" if route["framework"] == "django" else "frontend_route"
            if (
                node_by_id[route["nodeId"]].kind != expected_kind
                or node_by_id[route["nodeId"]].source.repository
                != route["repository"]
                or route["id"] in route_ids
                or (
                    route["repository"],
                    route["framework"],
                    route["path"],
                )
                in route_keys
            ):
                _fail("duplicate or invalid route")
            route_ids.add(route["id"])
            route_keys.add((route["repository"], route["framework"], route["path"]))
        validate_route_order(self.routes)
        if (
            len({item.get("id") for item in self.diagnostics}) != len(self.diagnostics)
            or self.diagnostics
            != sorted(self.diagnostics, key=lambda item: item.get("id", ""))
        ):
            _fail("diagnostics are not canonically sorted")
        for diagnostic in self.diagnostics:
            validate_diagnostic(diagnostic, persistable=False)
            if (
                diagnostic.get("repository") is not None
                and diagnostic["repository"] not in namespaces
            ):
                _fail("diagnostic repository is absent from manifest")
            if "source" in diagnostic:
                source = SourceLocation.from_dict(diagnostic["source"])
                if source.repository not in namespaces:
                    _fail("diagnostic source is absent from manifest")
            if "nodeId" in diagnostic and diagnostic["nodeId"] not in node_by_id:
                _fail("diagnostic node reference is absent")
            if "edgeId" in diagnostic and diagnostic["edgeId"] not in edge_by_id:
                _fail("diagnostic edge reference is absent")
            if "candidateIds" in diagnostic and any(
                identifier not in node_by_id for identifier in diagnostic["candidateIds"]
            ):
                _fail("diagnostic candidate reference is absent")
        if (
            len(
                json.dumps(
                    self.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            )
            > 32 * 1024 * 1024
        ):
            _fail("snapshot too large")

    def _validate_edge_cross_fields(
        self,
        namespaces: set[str],
        node_by_id: dict[str, Node],
        incoming: dict[str, list[Edge]],
        fragment_validation: bool,
    ) -> None:
        for node in self.nodes:
            metadata = node.metadata
            if (
                node.kind == "http_call"
                and "targetRepository" in metadata
                and metadata["targetRepository"] not in namespaces
            ):
                _fail("http target repository is absent from manifest")
            if node.kind == "http_call" and "endpointId" in metadata:
                expected = f"{metadata['method']} {metadata['normalizedPath']}"
                if "targetRepository" in metadata:
                    expected = f"{metadata['method']} {metadata['targetRepository']}:{metadata['normalizedPath']}"
                if metadata["endpointId"] != expected:
                    _fail("http endpointId does not match metadata")
            if node.kind == "django_url_pattern":
                endpoint_path = metadata["endpointId"].split(" ", 1)
                if (
                    len(endpoint_path) != 2
                    or not re.fullmatch(r"[A-Z][A-Z0-9!#$%&'*+.^_`|~-]{0,31}", endpoint_path[0])
                    or endpoint_path[1] != metadata["normalizedPath"]
                ):
                    _fail("django endpointId does not match metadata")
        for edge in self.edges:
            if edge.kind != "resolves_to":
                continue
            tier = edge.metadata["resolutionTier"]
            target = node_by_id[edge.target]
            if (
                "targetRepository" in edge.metadata
                and edge.metadata["targetRepository"] not in namespaces
            ):
                _fail("edge target repository is absent from manifest")
            if "targetRepository" in edge.metadata:
                if target.kind == "unresolved_target":
                    _fail("unresolved resolution has target repository")
                if edge.metadata["targetRepository"] != target.source.repository:
                    _fail("edge target repository does not match target")
            owner = node_by_id[edge.source]
            if owner.kind == "django_url_pattern":
                if tier == "declared_path" and target.kind == "django_view":
                    continue
                if tier == "unbounded" and target.kind == "unresolved_target":
                    continue
                _fail("django URL resolution is invalid")
            if owner.kind == "request_payload":
                carries = [
                    candidate
                    for candidate in incoming.get(owner.id, [])
                    if candidate.kind == "carries"
                ]
                if len(carries) != 1:
                    _fail("dynamic payload owner is invalid")
                owner = node_by_id[carries[0].source]
            if owner.kind != "http_call":
                _fail("resolution owner is invalid")
            call_metadata = owner.metadata
            resolution = call_metadata["urlResolution"]
            if resolution == "unbounded":
                if (
                    tier != "unbounded"
                    or target.kind != "unresolved_target"
                    or "endpointId" in call_metadata
                    or "targetRepository" in call_metadata
                    or "targetRepository" in edge.metadata
                ):
                    _fail("unbounded resolution is invalid")
                continue
            if target.kind == "unresolved_target":
                if tier != "unbounded" or "targetRepository" in edge.metadata:
                    _fail("unresolved resolution is invalid")
                if "endpointId" in call_metadata and not (
                    fragment_validation
                    and call_metadata["urlResolution"] == "literal"
                ):
                    _fail("unresolved resolution retains endpointId")
                continue
            if target.kind == "external_service":
                if resolution != "literal" or tier != "external_boundary":
                    _fail("external resolution is invalid")
                continue
            if target.kind != "django_url_pattern":
                _fail("resolution target is invalid")
            if (
                "targetRepository" in call_metadata
                and call_metadata["targetRepository"] != target.source.repository
            ):
                _fail("http target repository does not match target")
            if resolution == "bounded_template":
                if tier != "dynamic_converter":
                    _fail("bounded resolution tier is invalid")
                if (
                    call_metadata.get("targetRepository") != target.source.repository
                    or edge.metadata.get("targetRepository") != target.source.repository
                    or call_metadata.get("endpointId")
                    != f"{call_metadata['method']} {target.source.repository}:{target.metadata['normalizedPath']}"
                    or call_metadata["method"]
                    != target.metadata["endpointId"].split(" ", 1)[0]
                    or call_metadata["normalizedPath"]
                    != target.metadata["normalizedPath"]
                ):
                    _fail("dynamic converter resolution is incoherent")
                converters = target.metadata["converters"]
                target_segments = target.metadata["normalizedPath"].split("/")[1:]
                if any(
                    converter["kind"] not in {"int", "str", "slug", "uuid"}
                    or target_segments[converter["segmentIndex"]] != f"{{p{ordinal}}}"
                    for ordinal, converter in enumerate(converters)
                ):
                    _fail("dynamic converter target is incoherent")
                continue
            if tier not in {"exact_endpoint", "configured_base"}:
                _fail("literal resolution tier is invalid")
            if call_metadata["method"] != target.metadata["endpointId"].split(" ", 1)[0]:
                _fail("http method does not match target")
            if tier == "exact_endpoint" and (
                call_metadata["normalizedPath"] != target.metadata["normalizedPath"]
            ):
                _fail("http path does not match target")
            if "endpointId" in call_metadata:
                call_method, call_endpoint = call_metadata["endpointId"].split(" ", 1)
                if "targetRepository" in call_metadata:
                    prefix = f"{call_metadata['targetRepository']}:"
                    if not call_endpoint.startswith(prefix):
                        _fail("http endpointId does not match target")
                    call_endpoint = call_endpoint[len(prefix):]
                target_method, target_endpoint = target.metadata["endpointId"].split(" ", 1)
                if call_method != target_method or call_endpoint != target_endpoint:
                    _fail("http endpointId does not match target")

    def _validate_payload_topology(self, node_by_id: dict[str, Node], incoming: dict[str, list[Edge]], outgoing: dict[str, list[Edge]]) -> None:
        for node in self.nodes:
            if node.kind == "request_payload":
                carries = [edge for edge in incoming.get(node.id, []) if edge.kind == "carries"]
                resolves = [edge for edge in outgoing.get(node.id, []) if edge.kind == "resolves_to"]
                if len(carries) != 1 or len(resolves) != 1:
                    _fail("invalid payload topology")
                if (
                    carries[0].metadata
                    and carries[0].metadata["payloadKinds"]
                    != node.metadata["payloadKinds"]
                ):
                    _fail("carries payloadKinds mismatch")
            if node.kind == "http_call":
                carries = [edge for edge in outgoing.get(node.id, []) if edge.kind == "carries"]
                resolves = [edge for edge in outgoing.get(node.id, []) if edge.kind == "resolves_to"]
                if carries and resolves or not carries and len(resolves) != 1:
                    _fail("invalid http payload topology")
                if node.metadata["urlResolution"] == "unbounded":
                    terminal_edges = resolves or [edge for carry in carries for edge in outgoing.get(carry.target, []) if edge.kind == "resolves_to"]
                    if any(
                        node_by_id[edge.target].kind != "unresolved_target"
                        for edge in terminal_edges
                    ):
                        _fail("unbounded call resolved to a concrete target")
            if node.kind == "django_url_pattern":
                resolves = [
                    edge for edge in outgoing.get(node.id, []) if edge.kind == "resolves_to"
                ]
                if len(resolves) != 1:
                    _fail("invalid django URL topology")

    def validate_persistable(self) -> None:
        records: list[Node | Edge] = [*self.nodes, *self.edges]
        for record in records:
            for evidence in record.evidence:
                if (
                    evidence.kind == "observed"
                    or evidence.eventId is not None
                    or evidence.timestamp is not None
                ):
                    _fail("runtime material is not persistable")
            validate_metadata(
                record.kind,
                getattr(record, "layer", ""),
                record.metadata,
                phase="persistable",
            )
        for diagnostic in self.diagnostics:
            validate_diagnostic(diagnostic, persistable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 2,
            "project": self.project,
            "repositorySetId": self.repositorySetId,
            "repositories": self.repositories,
            "routes": self.routes,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "diagnostics": self.diagnostics,
        }
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphSnapshotV2:
        _exact(data, {"schemaVersion", "project", "repositorySetId", "repositories", "routes", "nodes", "edges", "diagnostics"}, "snapshot")
        if any(not isinstance(data[key], list) for key in ("repositories", "routes", "nodes", "edges", "diagnostics")):
            _fail("invalid snapshot")
        return cls(data["project"], data["repositorySetId"], data["repositories"], data["routes"], [Node.from_dict(item) for item in data["nodes"]], [Edge.from_dict(item) for item in data["edges"]], data["diagnostics"], data["schemaVersion"])

GraphSnapshot = GraphSnapshotV2
