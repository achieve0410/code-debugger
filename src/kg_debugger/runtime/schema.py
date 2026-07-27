from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from ..http import EndpointConfig, normalize_endpoint
from ..security import SECRET_KEY_RE, SecurityError, sanitize_trace_headers

_CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ENCODED_SEPARATOR_RE = re.compile(r"%(?:2[fF]|5[cC])")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TRACE_KEY_RE = re.compile(r"[a-z0-9][a-z0-9_*/-]{0,255}(?:@[a-z0-9][a-z0-9_*/-]{0,13})?")
_QUALIFIED_NAME_PART_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_CANONICAL_KEYS = frozenset(
    {
        "captureId",
        "method",
        "path",
        "target",
        "endpointId",
        "viewQualifiedName",
        "status",
        "durationMs",
        "traceparent",
        "tracestate",
    }
)
_LEGACY_KEYS = frozenset({"runId", "view", "trace"})
_LEGACY_COMMON_KEYS = frozenset({"runId", "method", "path", "target", "status", "durationMs"})
_LEGACY_MAPPED_KEYS = frozenset({"view", "trace"})
_LEGACY_ALLOWED_KEYS = _LEGACY_COMMON_KEYS | _LEGACY_MAPPED_KEYS


@dataclass(frozen=True)
class RuntimeEvent:
    """Canonical client event plus any one-release compatibility warning."""

    payload: dict[str, object]
    warnings: tuple[str, ...] = ()


class RuntimeEventValidationError(SecurityError):
    """Raised when a runtime event is outside the closed client contract."""


def validate_capture_id(value: object) -> str:
    if not isinstance(value, str) or not _CAPTURE_ID_RE.fullmatch(value):
        raise RuntimeEventValidationError("captureId must be a 1-128 character ASCII identifier")
    return value


def validate_runtime_event(
    payload: object, *, endpoint_config: EndpointConfig | None = None
) -> RuntimeEvent:
    """Normalize the one supported legacy shape and validate a canonical event."""
    if not isinstance(payload, Mapping) or isinstance(payload, dict) is False:
        raise RuntimeEventValidationError("runtime event must be an object")
    if any(not isinstance(key, str) for key in payload):
        raise RuntimeEventValidationError("runtime event keys must be strings")

    keys = set(payload)
    unknown = keys - _CANONICAL_KEYS - _LEGACY_KEYS
    if unknown:
        raise RuntimeEventValidationError("runtime event contains a disallowed field")
    if not keys <= _CANONICAL_KEYS | _LEGACY_KEYS:
        raise RuntimeEventValidationError("runtime event contains a disallowed field")

    legacy = bool(keys & _LEGACY_KEYS)
    if legacy:
        _validate_legacy_shape(payload)
    normalized = {key: value for key, value in payload.items() if key in _CANONICAL_KEYS}

    if "runId" in payload:
        normalized["captureId"] = payload["runId"]
    if "view" in payload and payload["view"] is not None:
        normalized["viewQualifiedName"] = payload["view"]
    if "trace" in payload:
        trace = payload["trace"]
        if not isinstance(trace, dict):
            raise RuntimeEventValidationError("legacy trace must be an object")
        if set(trace) - {"traceparent", "tracestate"} or any(
            not isinstance(key, str) for key in trace
        ):
            raise RuntimeEventValidationError("legacy trace contains a disallowed field")
        normalized.update(trace)

    _validate_required_strings(normalized, endpoint_config)
    _validate_optional_fields(normalized, endpoint_config)
    canonical = {key: normalized[key] for key in _CANONICAL_KEYS if key in normalized}
    return RuntimeEvent(canonical, ("legacy_runtime_event_v1",) if legacy else ())


def _validate_legacy_shape(payload: Mapping[str, object]) -> None:
    keys = set(payload)
    if keys - _LEGACY_ALLOWED_KEYS:
        raise RuntimeEventValidationError("legacy runtime event contains a disallowed field")
    if ("view" in payload or "trace" in payload) and "runId" not in payload:
        raise RuntimeEventValidationError("legacy view and trace fields require runId")


def _validate_required_strings(
    event: dict[str, object], endpoint_config: EndpointConfig | None
) -> None:
    event["captureId"] = validate_capture_id(event.get("captureId"))
    method = event.get("method")
    if not isinstance(method, str) or method not in _METHODS:
        raise RuntimeEventValidationError("method must be an allowed uppercase HTTP method")
    path = event.get("path")
    if not isinstance(path, str):
        raise RuntimeEventValidationError("path must be a string")
    _validate_path(path)
    try:
        normalize_endpoint(method, path, endpoint_config)
    except ValueError as exc:
        raise RuntimeEventValidationError("method and path do not form an endpoint identity") from exc


def _validate_optional_fields(event: dict[str, object], endpoint_config: EndpointConfig | None) -> None:
    for key, value in event.items():
        if value is None:
            raise RuntimeEventValidationError(f"{key} must not be null")
        if isinstance(value, (dict, list, tuple, set)):
            raise RuntimeEventValidationError(f"{key} must be a scalar")
    method = event["method"]
    path = event["path"]
    assert isinstance(method, str)
    assert isinstance(path, str)

    target = event.get("target")
    if target is not None:
        if not isinstance(target, str):
            raise RuntimeEventValidationError("target must be a string")
        target_path = _validate_target(target)
        if target_path != path:
            raise RuntimeEventValidationError("target path must equal path")

    endpoint_id = event.get("endpointId")
    if endpoint_id is not None:
        if not isinstance(endpoint_id, str) or len(endpoint_id) > 4096:
            raise RuntimeEventValidationError("endpointId must be a string up to 4096 characters")
        expected = normalize_endpoint(method, path, endpoint_config)["id"]
        if endpoint_id != expected:
            raise RuntimeEventValidationError("endpointId does not match method and path")

    qualified = event.get("viewQualifiedName")
    if qualified is not None:
        if (
            not isinstance(qualified, str)
            or not 3 <= len(qualified) <= 512
            or not _is_qualified_name(qualified)
        ):
            raise RuntimeEventValidationError("viewQualifiedName must be an ASCII dotted Python qualified name")

    status = event.get("status")
    if status is not None and (isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599):
        raise RuntimeEventValidationError("status must be an integer from 100 to 599")

    duration = event.get("durationMs")
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 <= duration <= 86_400_000:
            raise RuntimeEventValidationError("durationMs must be a finite number from 0 to 86400000")

    traceparent = event.get("traceparent")
    tracestate = event.get("tracestate")
    if traceparent is not None:
        if not isinstance(traceparent, str):
            raise RuntimeEventValidationError("traceparent must be a string")
        try:
            sanitize_trace_headers({"traceparent": traceparent})
        except SecurityError as exc:
            raise RuntimeEventValidationError(str(exc)) from exc
    if tracestate is not None:
        if not isinstance(tracestate, str) or not traceparent or not _valid_tracestate(tracestate):
            raise RuntimeEventValidationError("tracestate requires a valid traceparent")


def _validate_path(path: str) -> None:
    if not 1 <= len(path) <= 2048 or not path.startswith("/") or path.startswith("//"):
        raise RuntimeEventValidationError("path must be an origin-form path")
    if "?" in path or "#" in path or "\\" in path or _CONTROL_RE.search(path) or "//" in path:
        raise RuntimeEventValidationError("path contains a disallowed character")
    if _ENCODED_SEPARATOR_RE.search(path) or _invalid_percent_escape(path):
        raise RuntimeEventValidationError("path contains an invalid encoded separator")
    decoded_segments = _decode_path_segments(path)
    if any(segment in {".", ".."} or _CONTROL_RE.search(segment) or "\\" in segment for segment in decoded_segments):
        raise RuntimeEventValidationError("path contains a disallowed segment")


def _validate_target(target: str) -> str:
    if not target or len(target) > 2048 or _CONTROL_RE.search(target):
        raise RuntimeEventValidationError("target must be a bounded absolute URL")
    try:
        parsed = urlsplit(target)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeEventValidationError("target has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeEventValidationError("target must be an http(s) URL without userinfo")
    if "?" in target or "#" in target or not parsed.hostname or port is None and parsed.netloc.endswith(":"):
        raise RuntimeEventValidationError("target must not contain query, fragment, or invalid authority")
    if parsed.hostname != "localhost":
        try:
            if not ipaddress.ip_address(parsed.hostname).is_loopback:
                raise RuntimeEventValidationError("target host must be loopback")
        except ValueError as exc:
            raise RuntimeEventValidationError("target host must be localhost or a loopback IP") from exc
    target_path = parsed.path or "/"
    _validate_path(target_path)
    return str(normalize_endpoint("GET", target)["path"])


def _invalid_percent_escape(value: str) -> bool:
    return re.search(r"%(?![0-9A-Fa-f]{2})", value) is not None


def _decode_path_segments(path: str) -> list[str]:
    return [bytes(segment, "utf-8").decode("utf-8") if "%" not in segment else _percent_decode(segment) for segment in path.split("/")]


def _percent_decode(value: str) -> str:
    try:
        raw = bytearray()
        index = 0
        while index < len(value):
            if value[index] == "%":
                raw.append(int(value[index + 1 : index + 3], 16))
                index += 3
            else:
                raw.extend(value[index].encode("utf-8"))
                index += 1
        return bytes(raw).decode("utf-8")
    except (IndexError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeEventValidationError("path contains invalid percent encoding") from exc


def _is_qualified_name(value: str) -> bool:
    parts = value.split(".")
    return len(parts) > 1 and all(_QUALIFIED_NAME_PART_RE.fullmatch(part) for part in parts)


def _valid_tracestate(value: str) -> bool:
    if not 1 <= len(value) <= 512 or "\r" in value or "\n" in value or "@" in value or SECRET_KEY_RE.search(value):
        return False
    members = value.split(",")
    if len(members) > 32:
        return False
    for member in members:
        if member.count("=") != 1:
            return False
        key, member_value = member.split("=", 1)
        if not _TRACE_KEY_RE.fullmatch(key) or not member_value or len(member_value) > 256:
            return False
        if any(ord(char) < 0x20 or ord(char) > 0x7E or char in {",", "="} for char in member_value):
            return False
    return True
