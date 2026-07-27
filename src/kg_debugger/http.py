from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class EndpointConfig:
    first_party_origins: dict[str, str] = field(default_factory=dict)
    base_paths: tuple[str, ...] = ()
    trusted_proxy_prefixes: tuple[str, ...] = ()


_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_SEPARATOR = re.compile(r"%(?:2[fF]|5[cC])")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")


def _strict_path(path: str) -> str:
    """Return an origin-form path suitable for an endpoint identity.

    Runtime identity is deliberately not URL normalization: equivalent-looking
    spellings must be rejected instead of being silently collapsed.
    """
    if not isinstance(path, str) or not 1 <= len(path) <= 2048 or not path.startswith("/"):
        raise ValueError("endpoint path must be an origin-form path")
    if path.startswith("//") or "?" in path or "#" in path or "\\" in path or _CONTROL_OR_SPACE.search(path):
        raise ValueError("endpoint path contains disallowed material")
    if _PERCENT.search(path) or _ENCODED_SEPARATOR.search(path):
        raise ValueError("endpoint path contains invalid encoding")
    for segment in path.split("/"):
        if segment in {".", ".."}:
            raise ValueError("endpoint path contains dot segment")
        if "%" in segment:
            try:
                # Validate UTF-8 percent octets without changing their spelling.
                raw = bytearray()
                index = 0
                while index < len(segment):
                    if segment[index] == "%":
                        raw.append(int(segment[index + 1:index + 3], 16))
                        index += 3
                    else:
                        raw.extend(segment[index].encode("utf-8"))
                        index += 1
                decoded = bytes(raw).decode("utf-8")
            except (UnicodeDecodeError, ValueError, IndexError) as exc:
                raise ValueError("endpoint path contains invalid encoding") from exc
            if decoded in {".", ".."} or "\\" in decoded or _CONTROL_OR_SPACE.search(decoded):
                raise ValueError("endpoint path contains disallowed segment")
    return path


def _strip_prefix(path: str, prefixes: tuple[str, ...]) -> tuple[str, str | None]:
    for prefix in sorted(prefixes, key=len, reverse=True):
        normalized = _strict_path(prefix)
        if path == normalized:
            return "/", normalized
        base = normalized.rstrip("/")
        if path.startswith(base + "/"):
            return path[len(base):], normalized
    return path, None


def normalize_endpoint(
    method: str,
    path: str,
    config: EndpointConfig | None = None,
    proxy_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the closed endpoint identity used by static and runtime contracts.

    URL, proxy, query, and raw-request metadata are intentionally excluded.
    """
    if not isinstance(method, str) or method != method.upper() or not method:
        raise ValueError("endpoint method must be uppercase")
    if proxy_metadata:
        raise ValueError("proxy metadata is not part of endpoint identity")
    if path.startswith(("http://", "https://")):
        parsed = urlsplit(path)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.netloc:
            raise ValueError("endpoint URL contains disallowed material")
        path = parsed.path or "/"
    normalized_path = _strict_path(path)
    config = config or EndpointConfig()
    normalized_path, base_path = _strip_prefix(normalized_path, config.base_paths)
    return {
        "id": f"{method} {normalized_path}",
        "method": method,
        "path": normalized_path,
        "basePath": base_path,
    }
