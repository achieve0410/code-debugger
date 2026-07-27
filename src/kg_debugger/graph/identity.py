from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import quote, unquote

UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def normalize_repo_path(path: str) -> str:
    """Return a canonical repository-relative RFC 3986 path."""
    if (
        not isinstance(path, str)
        or not path
        or unicodedata.normalize("NFC", path) != path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or "\\" in path
        or path.startswith(("/", "//"))
    ):
        raise ValueError("invalid repository path")
    parts: list[str] = []
    for index, raw in enumerate(path.split("/")):
        if not raw or ("%" in raw and re.search(r"%(?![0-9A-Fa-f]{2})", raw)):
            raise ValueError("invalid repository path")
        decoded = unquote(raw)
        twice_decoded = unquote(decoded)
        if (
            decoded in {".", ".."}
            or twice_decoded in {".", ".."}
            or any(separator in decoded or separator in twice_decoded for separator in ("/", "\\"))
            or unicodedata.normalize("NFC", decoded) != decoded
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            or index == 0
            and re.fullmatch(r"[A-Za-z]:.*", decoded)
        ):
            raise ValueError("invalid repository path")
        parts.append(quote(decoded, safe=UNRESERVED))
    return "/".join(parts)


def stable_id(*parts: object, prefix: str = "") -> str:
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return f"{prefix}{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def node_identity(repository: str, source_path: str, kind: str, identity_key: str) -> str:
    return stable_id("node", repository, normalize_repo_path(source_path), kind, identity_key, prefix="n_")


def edge_identity(source: str, target: str, kind: str) -> str:
    return stable_id("edge", source, target, kind, prefix="e_")


def route_identity(repository: str, framework: str, normalized_path: str, node_id: str) -> str:
    return stable_id(repository, framework, normalized_path, node_id, prefix="r_")


def diagnostic_identity(*canonical_fields: object) -> str:
    return stable_id(*canonical_fields, prefix="d_")
