from __future__ import annotations

import ipaddress
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, NoReturn
from urllib.parse import quote, unquote

SAFE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
NAMESPACE_RE = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
NODE_ID_RE = re.compile(r"n_[0-9a-f]{64}\Z")
EDGE_ID_RE = re.compile(r"e_[0-9a-f]{64}\Z")
ROUTE_ID_RE = re.compile(r"r_[0-9a-f]{64}\Z")
DIAGNOSTIC_ID_RE = re.compile(r"d_[0-9a-f]{64}\Z")
CANDIDATE_ID_RE = NODE_ID_RE
PYTHON_QUALIFIED_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
SOURCE_SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
CONVERTER_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
PLACEHOLDER_RE = re.compile(r"\{([pu])([0-9]|[12][0-9]|3[01])\}\Z")
HTTP_METHOD_RE = re.compile(r"[A-Z][A-Z0-9!#$%&'*+.^_`|~-]{0,31}\Z")
SERVER_EVENT_ID_RE = re.compile(
    r"(?:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|legacy-[0-9a-f]{64})\Z"
)
MILLISECOND_UTC_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z\Z")

ROUTE_SORT_KEY_FIELDS = ("browserBackendGroup", "path", "repository", "framework", "nodeId", "id")
FRAMEWORKS = frozenset({"react", "vue", "nuxt", "django"})
FRONTEND_FRAMEWORKS = frozenset({"react", "vue", "nuxt"})
PAYLOAD_KINDS = frozenset({"body", "query", "form"})

NODE_METADATA_SCHEMAS: dict[str, frozenset[str]] = {
    "frontend_route": frozenset({"framework", "declaredPath"}),
    "page": frozenset({"frameworkOwners"}),
    "component": frozenset({"frameworkOwners"}),
    "ui_event": frozenset({"frameworkOwners", "eventKind", "elementKind", "modifiers"}),
    "function": frozenset({"frameworkOwners", "pythonQualifiedName"}),
    "http_call": frozenset({"method", "urlResolution", "normalizedPath", "endpointId", "queryFieldCount", "hasSensitiveQuery", "targetRepository"}),
    "request_payload": frozenset({"payloadKinds", "bodyShape", "bodyFieldCount", "queryFieldCount", "hasSensitiveFields"}),
    "django_url_pattern": frozenset({"declaredPath", "normalizedPath", "endpointId", "converters"}),
    "django_view": frozenset({"pythonQualifiedName"}),
    "model": frozenset({"pythonQualifiedName"}),
    "query_boundary": frozenset({"operation", "modelQualifiedName"}),
    "external_service": frozenset({"method", "scheme", "host", "port", "pathPresent", "queryFieldCount", "hasSensitiveQuery", "boundaryOnly"}),
    "unresolved_target": frozenset({"reasonCode", "candidateIds"}),
}
EDGE_METADATA_SCHEMAS: dict[str, frozenset[str]] = {
    **{kind: frozenset() for kind in ("renders", "contains", "handles", "navigates_to", "calls", "invokes", "accesses", "branches_to")},
    "carries": frozenset({"payloadKinds"}),
    "resolves_to": frozenset({"resolutionTier", "targetRepository"}),
}

EVIDENCE_REASON_CATALOG = {
    "inferred": {
        "ast_route_declaration": "Static route declaration.", "ast_symbol_declaration": "Static symbol declaration.",
        "ast_call": "Static call expression.", "ast_handler_binding": "Static handler binding.",
        "ast_import_binding": "Static import binding.", "literal_url": "Literal URL shape.",
        "finite_url_domain": "Finite URL domain proof.", "request_payload_shape": "Static request payload shape.",
        "django_url_declaration": "Static Django URL declaration.", "django_view_binding": "Static Django view binding.",
        "django_query_call": "Static Django query call.", "external_boundary": "External interface boundary.",
        "exact_endpoint": "Exact endpoint identity.", "declared_path": "Exact declared path.",
        "configured_base": "Configured base-path match.", "dynamic_converter": "Finite values accepted by the target converter.",
    },
    "unresolved": {
        "dynamic_target_unproven": "Dynamic target was not proven.", "referenced_target_missing": "Referenced target was not declared.",
        "python_module_unproven": "Python import module was not proven.", "python_module_ambiguous": "Python import module was ambiguous.",
        "url_target_unmatched": "No URL target matched.", "url_target_ambiguous": "Multiple URL targets matched.", "unsupported_syntax": "Syntax is unsupported by the bounded analyzer.",
    },
    "observed": {
        "runtime_coherent_endpoint": "Runtime endpoint identity was coherent.", "runtime_coherent_view": "Runtime view identity was coherent.", "runtime_coherent_resolution": "Runtime resolution identity was coherent.",
    },
}

@dataclass(frozen=True)
class DiagnosticSpec:
    severity: str
    message: str
    runtime_only: bool
    allowed_references: frozenset[str]

DIAGNOSTIC_CATALOG: dict[str, DiagnosticSpec] = {
    "frontend_analyzer_unavailable": DiagnosticSpec("warning", "Frontend analyzer is unavailable.", False, frozenset({"repository"})),
    "frontend_analyzer_failed": DiagnosticSpec("error", "Frontend analyzer failed.", False, frozenset({"repository"})),
    "frontend_analyzer_invalid_output": DiagnosticSpec("error", "Frontend analyzer returned invalid output.", False, frozenset({"repository"})),
    "source_read_failed": DiagnosticSpec("warning", "A source file could not be read.", False, frozenset({"repository", "source"})),
    "unsupported_syntax": DiagnosticSpec("warning", "Unsupported syntax was left unresolved.", False, frozenset({"repository", "source", "nodeId"})),
    "unresolved_dynamic_target": DiagnosticSpec("warning", "A dynamic target could not be proven.", False, frozenset({"repository", "source", "nodeId"})),
    "unresolved_referenced_target": DiagnosticSpec("warning", "A referenced target was not declared in the analyzed graph.", False, frozenset({"repository", "source", "nodeId"})),
    "unresolved_django_url": DiagnosticSpec("warning", "A Django URL declaration could not be resolved statically.", False, frozenset({"repository", "source"})),
    "python_import_module_unresolved": DiagnosticSpec("warning", "A Python import module could not be proven.", False, frozenset({"repository", "source", "nodeId"})),
    "python_import_module_ambiguous": DiagnosticSpec("warning", "A Python import module has multiple valid candidates.", False, frozenset({"repository", "source", "nodeId"})),
    "bounded_url_proof_invalid": DiagnosticSpec("error", "A bounded URL proof was invalid.", False, frozenset({"repository"})),
    "url_target_unmatched": DiagnosticSpec("warning", "No unique Django URL target matched the request shape.", False, frozenset({"repository", "nodeId"})),
    "url_target_ambiguous": DiagnosticSpec("warning", "Multiple Django URL targets matched the request shape.", False, frozenset({"repository", "nodeId", "candidateIds"})),
    "runtime_capture_empty": DiagnosticSpec("info", "The selected runtime capture contains no events.", True, frozenset()),
    "runtime_event_unmatched": DiagnosticSpec("warning", "The runtime event did not match one canonical flow.", True, frozenset({"eventId"})),
    "runtime_event_ambiguous": DiagnosticSpec("warning", "The runtime event matched multiple canonical flows.", True, frozenset({"eventId", "candidateIds"})),
    "runtime_identity_conflict": DiagnosticSpec("warning", "The runtime event identities do not describe one canonical flow.", True, frozenset({"eventId", "candidateIds"})),
}
SNAPSHOT_INCOMPATIBILITY_CATALOG = {
    "legacy_snapshot_incompatible": "Legacy snapshot cannot be proven compatible.",
    "snapshot_schema_unsupported": "Snapshot schema version is unsupported.",
    "snapshot_repository_set_mismatch": "Snapshot repository set does not match the active configuration.",
    "snapshot_manifest_mismatch": "Snapshot repository manifest does not match the active configuration.",
}

SECRET_KEY_TERMS = frozenset({"authorization", "cookie", "set_cookie", "password", "passwd", "pwd", "secret", "token", "access_token", "refresh_token", "api_key", "apikey", "credential", "credentials", "private_key", "client_secret", "session", "sessionid", "csrf", "xsrf", "baggage", "request_body", "response_body"})
SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", re.I), re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}\b", re.I),
    re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.I), re.compile(r"\bxox[baprs]-[^\s]{10,}\b", re.I),
    re.compile(r"\b(?:authorization|cookie|set[ _-]?cookie|password|passwd|pwd|secret|token|access[ _-]?token|refresh[ _-]?token|api[ _-]?key|apikey|credential|credentials|private[ _-]?key|client[ _-]?secret|session|sessionid|csrf|xsrf|baggage)\s*[:=]\s*\S+", re.I),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+_-]{32,}(?![A-Za-z0-9+_-])"),
)

@dataclass(frozen=True)
class FieldSpec:
    required: bool
    validator: Callable[[Any], None] | None = None

@dataclass(frozen=True)
class ObjectSpec:
    fields: dict[str, FieldSpec]

def _fail(message: str) -> NoReturn:
    raise ValueError(message)

def _string(value: Any, name: str, minimum: int = 1, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not (minimum <= len(value) <= maximum) or unicodedata.normalize("NFC", value) != value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(f"invalid {name}")
    return value
def validate_server_event_id(value: Any) -> str:
    if not isinstance(value, str) or not SERVER_EVENT_ID_RE.fullmatch(value):
        _fail("invalid eventId")
    return value


def validate_millisecond_utc(value: Any) -> str:
    text = _string(value, "timestamp", 24, 24)
    if not MILLISECOND_UTC_RE.fullmatch(text):
        _fail("invalid timestamp")
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _fail("invalid timestamp")
    return text

def _integer(value: Any, name: str, minimum: int = 0, maximum: int = 1_000_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(f"invalid {name}")
    return value

def _count(value: Any, name: str) -> int:
    return _integer(value, name, 0, 1000)

def _sorted_unique(values: Any, name: str, allowed: frozenset[str], minimum: int, maximum: int) -> list[str]:
    if not isinstance(values, list) or not minimum <= len(values) <= maximum or any(not isinstance(item, str) or item not in allowed for item in values) or values != sorted(set(values)):
        _fail(f"invalid {name}")
    return values

def _path(value: Any, name: str = "path", *, placeholders: str | None = None) -> str:
    text = _string(value, name, 1, 2048)
    if not text.startswith("/") or any(item in text for item in ("?", "#", "\\")):
        _fail(f"invalid {name}")
    kinds: list[str] = []
    indices: list[int] = []
    for index, segment in enumerate(text.split("/")[1:]):
        if (
            segment in (".", "..")
            or (not segment and text != "/" and index != len(text.split("/")) - 2)
            or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in segment)
        ):
            _fail(f"invalid {name}")
        if not segment:
            continue
        match = PLACEHOLDER_RE.fullmatch(segment)
        if match:
            kinds.append(match.group(1))
            indices.append(int(match.group(2)))
            continue
        if "{" in segment or "}" in segment or "@" in segment:
            _fail(f"invalid {name}")
        if re.search(r"%(?![0-9A-F]{2})", segment):
            _fail(f"invalid {name}")
        decoded = unquote(segment)
        twice_decoded = unquote(decoded)
        if (
            decoded in {".", ".."}
            or twice_decoded in {".", ".."}
            or any(separator in decoded or separator in twice_decoded for separator in ("/", "\\"))
            or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in decoded)
            or quote(decoded, safe="!$&'()*+,;=:@-._~") != segment
        ):
            _fail(f"invalid {name}")
    if indices and indices != list(range(len(indices))) or len(set(indices)) != len(indices):
        _fail(f"invalid {name}")
    if (
        placeholders == "literal"
        and kinds
        or placeholders == "bounded"
        and (not kinds or set(kinds) != {"p"})
        or placeholders == "unbounded"
        and (not kinds or set(kinds) != {"u"})
    ):
        _fail(f"invalid {name}")
    return text

def _declared_path(value: Any, framework: str) -> str:
    text = _string(value, "declaredPath", 1, 2048)
    if framework not in FRAMEWORKS:
        _fail("invalid framework")
    _path(re.sub(r"/(?:\:[A-Za-z_][A-Za-z0-9_]*|<(?:[A-Za-z_][A-Za-z0-9_]*:)?[A-Za-z_][A-Za-z0-9_]*>)", "/segment", text), "declaredPath")
    for segment in text.split("/")[1:]:
        if segment.startswith(":") and (
            framework == "django" or not CONVERTER_NAME_RE.fullmatch(segment[1:])
        ):
            _fail("invalid declaredPath")
        if segment.startswith("<") and (
            framework != "django"
            or not re.fullmatch(
                r"<(?:[A-Za-z_][A-Za-z0-9_]{0,63}:)?[A-Za-z_][A-Za-z0-9_]{0,63}>",
                segment,
            )
        ):
            _fail("invalid declaredPath")
    return text

def _qualified(value: Any, name: str) -> None:
    text = _string(value, name, 3, 512)
    if not PYTHON_QUALIFIED_NAME_RE.fullmatch(text):
        _fail(f"invalid {name}")

def _metadata_size(value: Any) -> None:
    def validate(item: Any, depth: int) -> None:
        if depth > 8:
            _fail("metadata nesting is too deep")
        if isinstance(item, dict):
            if len(item) > 1000 or any(
                not isinstance(key, str) or len(key) > 64 for key in item
            ):
                _fail("invalid metadata")
            for child in item.values():
                validate(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 1000:
                _fail("metadata array is too large")
            for child in item:
                validate(child, depth + 1)
        elif isinstance(item, str):
            _string(item, "metadata string", 0, 4096)
        elif isinstance(item, bool) or item is None:
            return
        elif not isinstance(item, (int, float)) or not math.isfinite(item):
            _fail("invalid metadata value")
    validate(value, 0)
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError):
        _fail("invalid metadata")
    if len(encoded) > 16 * 1024:
        _fail("metadata too large")

def is_secret_key(key: str) -> bool:
    normalized = unicodedata.normalize("NFKC", key)
    normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", normalized).casefold()
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    joined = "_".join(tokens)
    return joined in SECRET_KEY_TERMS or any("_".join(tokens[index:end]) in SECRET_KEY_TERMS for index in range(len(tokens)) for end in range(index + 1, len(tokens) + 1))

def is_secret_value(value: str, *, include_generic: bool = True) -> bool:
    patterns = SECRET_VALUE_PATTERNS if include_generic else SECRET_VALUE_PATTERNS[:-1]
    return any(pattern.search(unicodedata.normalize("NFKC", value)) for pattern in patterns)

def reject_secret_material(value: Any, *, key: str | None = None) -> None:
    if key is not None and is_secret_key(key) and key not in {
        "hasSensitiveQuery",
        "hasSensitiveFields",
    }:
        _fail("secret-like key")
    if isinstance(value, str) and is_secret_value(value):
        _fail("secret-like value")
    if isinstance(value, dict):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                _fail("invalid object key")
            reject_secret_material(child, key=child_key)
    elif isinstance(value, list):
        for child in value:
            reject_secret_material(child)

def validate_metadata(kind: str, layer: str, value: Any, *, phase: str) -> None:
    if (
        kind not in NODE_METADATA_SCHEMAS
        and kind not in EDGE_METADATA_SCHEMAS
        or not isinstance(value, dict)
    ):
        _fail("invalid metadata")
    allowed = NODE_METADATA_SCHEMAS.get(kind, EDGE_METADATA_SCHEMAS.get(kind, frozenset()))
    if set(value) - allowed or any(
        not isinstance(key, str) or len(key) > 64 for key in value
    ):
        _fail("unknown metadata key")
    _metadata_size(value)
    for key, item in value.items():
        if key not in {"candidateIds", "pythonQualifiedName"}:
            reject_secret_material(item, key=key)
    if kind in {"page", "component"}:
        _sorted_unique(
            value.get("frameworkOwners"), "frameworkOwners", FRONTEND_FRAMEWORKS, 1, 3
        )
    elif kind == "frontend_route":
        framework = value.get("framework")
        if framework not in FRAMEWORKS:
            _fail("invalid framework")
        _declared_path(value.get("declaredPath"), framework)
    elif kind == "ui_event":
        _sorted_unique(
            value.get("frameworkOwners"), "frameworkOwners", FRONTEND_FRAMEWORKS, 1, 3
        )
        for key in ("eventKind", "elementKind"):
            text = _string(value.get(key), key, 1, 64)
            if not SAFE_TOKEN_RE.fullmatch(text):
                _fail(f"invalid {key}")
        modifiers = value.get("modifiers")
        if (
            not isinstance(modifiers, list)
            or len(modifiers) > 16
            or modifiers != sorted(set(modifiers))
            or any(
                not isinstance(item, str)
                or len(item) > 64
                or not SAFE_TOKEN_RE.fullmatch(item)
                for item in modifiers
            )
        ):
            _fail("invalid modifiers")
    elif kind == "function":
        owners = value.get("frameworkOwners")
        if layer == "frontend" and owners is None:
            _fail("frontend function requires frameworkOwners")
        if layer == "backend" and owners is not None:
            _fail("backend function forbids frameworkOwners")
        if owners is not None:
            _sorted_unique(owners, "frameworkOwners", FRONTEND_FRAMEWORKS, 1, 3)
        if "pythonQualifiedName" in value:
            _qualified(value["pythonQualifiedName"], "pythonQualifiedName")
    elif kind == "http_call":
        method = value.get("method")
        resolution = value.get("urlResolution")
        if (
            not isinstance(method, str)
            or not HTTP_METHOD_RE.fullmatch(method)
            or resolution not in {"literal", "bounded_template", "unbounded"}
        ):
            _fail("invalid http_call metadata")
        _path(value.get("normalizedPath"), "normalizedPath", placeholders={"literal": "literal", "bounded_template": "bounded", "unbounded": "unbounded"}[resolution])
        for key in ("queryFieldCount",):
            _count(value.get(key), key)
        if not isinstance(value.get("hasSensitiveQuery"), bool):
            _fail("invalid hasSensitiveQuery")
        if "targetRepository" in value and (
            not isinstance(value["targetRepository"], str)
            or not NAMESPACE_RE.fullmatch(value["targetRepository"])
        ):
            _fail("invalid targetRepository")
        if resolution == "unbounded" and "endpointId" in value:
            _fail("unbounded endpointId")
        if "endpointId" in value:
            _string(value["endpointId"], "endpointId", 3, 2300)
    elif kind == "request_payload":
        _sorted_unique(value.get("payloadKinds"), "payloadKinds", PAYLOAD_KINDS, 1, 3)
        if value.get("bodyShape") not in {
            "none",
            "object",
            "array",
            "scalar",
            "unknown",
        }:
            _fail("invalid bodyShape")
        _count(value.get("bodyFieldCount"), "bodyFieldCount")
        _count(value.get("queryFieldCount"), "queryFieldCount")
        if not isinstance(value.get("hasSensitiveFields"), bool):
            _fail("invalid hasSensitiveFields")
    elif kind == "django_url_pattern":
        declared_path = _declared_path(value.get("declaredPath"), "django")
        normalized_path = _path(value.get("normalizedPath"), "normalizedPath")
        _string(value.get("endpointId"), "endpointId", 3, 2300)
        converters = value.get("converters")
        if not isinstance(converters, list) or len(converters) > 32:
            _fail("invalid converters")
        declared_segments = [
            (index, segment)
            for index, segment in enumerate(declared_path.split("/")[1:])
            if segment.startswith("<")
        ]
        normalized_segments = normalized_path.split("/")[1:]
        if len(converters) != len(declared_segments):
            _fail("invalid converters")
        expected_segments: list[str] = []
        placeholder_ordinal = 0
        for segment in declared_path.split("/")[1:]:
            if segment.startswith("<"):
                expected_segments.append(f"{{p{placeholder_ordinal}}}")
                placeholder_ordinal += 1
            else:
                expected_segments.append(segment)
        if normalized_segments != expected_segments:
            _fail("invalid converters")
        names: set[str] = set()
        for ordinal, (converter, (segment_index, declared)) in enumerate(
            zip(converters, declared_segments, strict=True)
        ):
            if not isinstance(converter, dict):
                _fail("invalid converters")
            _integer(converter.get("segmentIndex"), "segmentIndex", 0, 255)
            if (
                set(converter) != {"name", "kind", "segmentIndex"}
                or not CONVERTER_NAME_RE.fullmatch(converter.get("name", ""))
                or converter.get("kind")
                not in {"int", "str", "slug", "uuid", "path", "custom"}
                or converter["segmentIndex"] != segment_index
                or converter["name"] in names
                or normalized_segments[segment_index] != f"{{p{ordinal}}}"
            ):
                _fail("invalid converters")
            declared_converter, declared_name = (
                declared[1:-1].split(":", 1)
                if ":" in declared
                else ("str", declared[1:-1])
            )
            if (
                converter["name"] != declared_name
                or converter["kind"]
                != (
                    declared_converter
                    if declared_converter in {"int", "str", "slug", "uuid", "path"}
                    else "custom"
                )
            ):
                _fail("invalid converters")
            names.add(converter["name"])
    elif kind in {"django_view", "model"}:
        if "pythonQualifiedName" in value:
            _qualified(value["pythonQualifiedName"], "pythonQualifiedName")
    elif kind == "query_boundary":
        if value.get("operation") not in {
            "all",
            "filter",
            "get",
            "create",
            "update",
            "delete",
            "aggregate",
            "other",
        }:
            _fail("invalid operation")
        if "modelQualifiedName" in value:
            _qualified(value["modelQualifiedName"], "modelQualifiedName")
    elif kind == "external_service":
        if (
            not isinstance(value.get("method"), str)
            or not HTTP_METHOD_RE.fullmatch(value["method"])
            or value.get("scheme") not in {"http", "https"}
            or value.get("boundaryOnly") is not True
        ):
            _fail("invalid external_service metadata")
        host = value.get("host")
        if not isinstance(host, str) or not host or len(host) > 253 or host != host.lower() or "@" in host or host.endswith("."):
            _fail("invalid external_service metadata")
        try:
            parsed_host = ipaddress.ip_address(host)
        except ValueError:
            try:
                alabel = host.encode("idna").decode("ascii")
            except UnicodeError:
                _fail("invalid external_service metadata")
            labels = host.split(".")
            if host != alabel or any(not 1 <= len(label) <= 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                _fail("invalid external_service metadata")
        else:
            if parsed_host.compressed != host or "%" in host:
                _fail("invalid external_service metadata")
        if "port" in value:
            _integer(value["port"], "port", 1, 65535)
        _count(value.get("queryFieldCount"), "queryFieldCount")
        if not isinstance(value.get("pathPresent"), bool) or not isinstance(
            value.get("hasSensitiveQuery"), bool
        ):
            _fail("invalid external_service metadata")
    elif kind == "unresolved_target":
        validate_evidence_reason("unresolved", value.get("reasonCode"), persistable=phase == "persistable")
        candidates = value.get("candidateIds")
        if candidates is not None and (
            not isinstance(candidates, list)
            or not 1 <= len(candidates) <= 100
            or candidates != sorted(set(candidates))
            or any(
                not isinstance(item, str) or not NODE_ID_RE.fullmatch(item)
                for item in candidates
            )
        ):
            _fail("invalid candidateIds")
    elif kind == "carries":
        if value and "payloadKinds" not in value:
            _fail("invalid carries metadata")
        if value:
            _sorted_unique(value["payloadKinds"], "payloadKinds", PAYLOAD_KINDS, 1, 3)
    elif kind == "resolves_to":
        if value.get("resolutionTier") not in {
            "exact_endpoint",
            "declared_path",
            "configured_base",
            "dynamic_converter",
            "external_boundary",
            "unbounded",
        }:
            _fail("invalid resolutionTier")
        if "targetRepository" in value and (
            not isinstance(value["targetRepository"], str)
            or not NAMESPACE_RE.fullmatch(value["targetRepository"])
        ):
            _fail("invalid targetRepository")

def validate_evidence_reason(kind: str, reason: Any, *, persistable: bool) -> None:
    if (
        kind not in EVIDENCE_REASON_CATALOG
        or not isinstance(reason, str)
        or reason not in EVIDENCE_REASON_CATALOG[kind]
        or len(reason) > 128
    ):
        _fail("invalid evidence reason")
    if persistable and kind == "observed":
        _fail("observed evidence is transient")

def format_external_authority(host: str, port: int | None = None) -> str:
    authority = f"[{host}]" if ":" in host else host
    return f"{authority}:{port}" if port is not None else authority

def validate_diagnostic_source(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or not {"repository", "path"} <= set(value)
        or set(value) - {"repository", "path", "line", "endLine", "symbol"}
        or not isinstance(value.get("repository"), str)
        or not NAMESPACE_RE.fullmatch(value["repository"])
    ):
        _fail("invalid diagnostic source")
    from .identity import normalize_repo_path
    path = value.get("path")
    if not isinstance(path, str) or path != normalize_repo_path(path):
        _fail("invalid diagnostic source")
    line = value.get("line")
    end_line = value.get("endLine")
    if (
        ("line" in value and (isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= 10_000_000))
        or ("endLine" in value and (isinstance(end_line, bool) or not isinstance(end_line, int) or not 1 <= end_line <= 10_000_000))
        or (end_line is not None and line is not None and end_line < line)
    ):
        _fail("invalid diagnostic source")
    if "symbol" in value:
        symbol = value["symbol"]
        if (
            not isinstance(symbol, str)
            or _string(symbol, "symbol", 1, 512) != symbol
            or not SOURCE_SYMBOL_RE.fullmatch(symbol)
        ):
            _fail("invalid diagnostic source")
    reject_secret_material(value)

def validate_diagnostic(value: Any, *, persistable: bool) -> None:
    required = {"id", "code", "severity", "message"}
    allowed = required | {
        "repository",
        "source",
        "nodeId",
        "edgeId",
        "candidateIds",
        "eventId",
    }
    if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
        _fail("invalid diagnostic")
    code = value.get("code")
    spec = DIAGNOSTIC_CATALOG.get(code) if isinstance(code, str) else None
    if (
        spec is None
        or value.get("severity") != spec.severity
        or value.get("message") != spec.message
    ):
        _fail("invalid diagnostic")
    if persistable and (spec.runtime_only or "eventId" in value):
        _fail("runtime diagnostic is transient")
    if set(value) - {"id", "code", "severity", "message"} - spec.allowed_references:
        _fail("invalid diagnostic references")
    from .identity import diagnostic_identity
    fields = tuple(
        json.dumps(value.get(key), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for key in ("code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId")
    )
    if (
        not isinstance(value["id"], str)
        or not DIAGNOSTIC_ID_RE.fullmatch(value["id"])
        or value["id"] != diagnostic_identity(*fields)
    ):
        _fail("invalid diagnostic id")
    if "candidateIds" in value and (
        not isinstance(value["candidateIds"], list)
        or value["candidateIds"] != sorted(set(value["candidateIds"]))
        or not 1 <= len(value["candidateIds"]) <= 100
        or any(
            not isinstance(item, str) or not NODE_ID_RE.fullmatch(item)
            for item in value["candidateIds"]
        )
    ):
        _fail("invalid candidateIds")
    if "repository" in value and (
        not isinstance(value["repository"], str)
        or not NAMESPACE_RE.fullmatch(value["repository"])
    ):
        _fail("invalid diagnostic repository")
    if "nodeId" in value and (
        not isinstance(value["nodeId"], str)
        or not NODE_ID_RE.fullmatch(value["nodeId"])
    ):
        _fail("invalid diagnostic nodeId")
    if "edgeId" in value and (
        not isinstance(value["edgeId"], str)
        or not EDGE_ID_RE.fullmatch(value["edgeId"])
    ):
        _fail("invalid diagnostic edgeId")
    if "eventId" in value:
        validate_server_event_id(value["eventId"])
    if "source" in value:
        validate_diagnostic_source(value["source"])
    if "repository" in value:
        reject_secret_material(value["repository"], key="repository")
    if len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ) > 4096:
        _fail("diagnostic too large")

def route_sort_key(route: dict[str, Any]) -> tuple[Any, ...]:
    return (route["framework"] == "django", route["path"], route["repository"], route["framework"], route["nodeId"], route["id"])

def validate_route_order(routes: list[dict[str, Any]]) -> None:
    if routes != sorted(routes, key=route_sort_key):
        _fail("routes are not canonically sorted")
