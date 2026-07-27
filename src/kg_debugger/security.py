from __future__ import annotations

import ipaddress
import logging
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

SECRET_KEY_RE = re.compile(r"(token|cookie|password|secret|credential|private[_-]?key|session|request[_-]?body|^body$)", re.I)
SAFE_TRACE_HEADERS = {"traceparent", "tracestate"}
SENSITIVE_HEADER_RE = re.compile(r"(cookie|authorization|token|password|session|secret|baggage)", re.I)
TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


class SecurityError(ValueError):
    pass


def resolve_repo_path(repo_root: str | Path, candidate: str | Path) -> Path:
    root = Path(repo_root).resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SecurityError("path escapes configured repository root") from exc
    return resolved


def resolve_analysis_root(workspace_root: str | Path, candidate: str | Path) -> Path:
    """Resolve an analysis directory without accepting unsafe path provenance."""
    if not isinstance(candidate, (str, Path)) or not str(candidate):
        raise SecurityError("analysis root must not be empty")

    raw = str(candidate)
    decoded = _fully_unquote(raw)
    if "\\" in raw or "\\" in decoded:
        raise SecurityError("analysis root must not contain backslashes")
    if re.search(r"%(?:2f|5c)", raw, re.I):
        raise SecurityError("analysis root must not contain encoded separators")

    requested_input = Path(raw).expanduser()
    decoded_input = Path(decoded).expanduser()
    if ".." in requested_input.parts or ".." in decoded_input.parts:
        raise SecurityError("analysis root must not contain parent traversal")

    workspace = Path(workspace_root).resolve(strict=True)
    requested = requested_input if requested_input.is_absolute() else workspace / requested_input
    if requested == Path(requested.anchor):
        raise SecurityError("analysis root must not be a filesystem root")
    if _is_credential_path(requested.absolute()):
        raise SecurityError("credential-bearing path is not an allowed analysis root")
    _reject_symlink_components(requested)

    try:
        resolved = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SecurityError("analysis root does not exist") from exc
    if not resolved.is_dir():
        raise SecurityError("analysis root must be a directory")

    if not requested_input.is_absolute():
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise SecurityError(
                "relative analysis roots must remain inside the debugger workspace; "
                "use an explicit absolute path for an external repository"
            ) from exc
    if _is_credential_path(resolved):
        raise SecurityError("credential-bearing path is not an allowed analysis root")
    return resolved


def _fully_unquote(value: str) -> str:
    decoded = value
    for _ in range(8):
        next_value = unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    return decoded


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise SecurityError("analysis root must not traverse a symlink")


def _is_credential_path(path: Path) -> bool:
    home = Path.home().absolute()
    return any(path == sensitive or sensitive in path.parents for sensitive in (home / ".ssh", home / ".aws", home / ".config"))


def require_loopback_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError("runtime target scheme must be http or https")
    host = parsed.hostname
    if not host:
        raise SecurityError("runtime target must include a host")
    if host in {"localhost"}:
        return url
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise SecurityError("runtime target host must be loopback by default") from exc
    if not address.is_loopback:
        raise SecurityError("runtime target host must be loopback by default")
    return url


def reject_sensitive_config(config: dict[str, object], approved_sensitive: bool = False) -> None:
    if approved_sensitive:
        return
    for key, value in config.items():
        if SECRET_KEY_RE.search(str(key)) and value not in (None, "", False):
            raise SecurityError(f"sensitive config field is not allowed: {key}")


def sanitize_trace_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower not in SAFE_TRACE_HEADERS or SENSITIVE_HEADER_RE.search(lower):
            raise SecurityError(f"trace header is not allowed: {key}")
        if len(value) > 512 or "\n" in value or "\r" in value:
            raise SecurityError(f"trace header has invalid value: {key}")
        if lower == "traceparent":
            if not TRACEPARENT_RE.fullmatch(value):
                raise SecurityError("traceparent must match W3C version 00 shape")
            _, trace_id, parent_id, _ = value.split("-")
            if trace_id == "0" * 32 or parent_id == "0" * 16:
                raise SecurityError("traceparent IDs must be non-zero")
        if lower == "tracestate" and ("@" in value or SECRET_KEY_RE.search(value)):
            raise SecurityError("tracestate must not contain identifying or sensitive values")
        sanitized[lower] = value
    return sanitized


def redact_sensitive(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)) or SENSITIVE_HEADER_RE.search(str(key)):
                continue
            redacted[str(key)] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def cert_paths(root: str | Path) -> tuple[Path, Path]:
    cert = resolve_repo_path(root, "pem/cert.pem")
    key = resolve_repo_path(root, "pem/key.pem")
    return cert, key


def configure_safe_logging() -> logging.Logger:
    logger = logging.getLogger("kg_debugger")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(os.environ.get("KG_DEBUGGER_LOG_LEVEL", "INFO"))
    return logger
