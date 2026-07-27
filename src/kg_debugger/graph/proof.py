from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import PLACEHOLDER_RE
from .schema import GraphSnapshotV2


class BoundedUrlProofError(ValueError):
    """Raised when an adapter proof sidecar is not an exact bounded-url proof."""


@dataclass(frozen=True)
class BoundedUrlPlaceholderProof:
    token: str
    segmentIndex: int
    memberCount: int
    acceptedConverters: tuple[str, ...]


@dataclass(frozen=True)
class BoundedUrlProof:
    version: int
    callId: str
    normalizedPath: str
    placeholders: tuple[BoundedUrlPlaceholderProof, ...]


@dataclass(frozen=True)
class CanonicalFragment:
    """An intentionally non-wire envelope consumed exactly once by graph merge."""

    snapshot: GraphSnapshotV2
    bounded_url_proofs: Mapping[str, BoundedUrlProof]


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BoundedUrlProofError(f"invalid {name}")
    return value


def parse_bounded_url_proofs(fragment: Mapping[str, Any], key_to_id: Mapping[str, str]) -> dict[str, BoundedUrlProof]:
    """Parse and bind the value-free adapter sidecar to recomputed HTTP node IDs."""
    proofs = fragment.get("boundedUrlProofs", [])
    if not isinstance(proofs, list) or len(proofs) > 10_000:
        raise BoundedUrlProofError("invalid boundedUrlProofs")
    calls: dict[str, Mapping[str, Any]] = {}
    for raw in fragment.get("nodes", []):
        if not isinstance(raw, Mapping):
            raise BoundedUrlProofError("invalid node")
        key = raw.get("key")
        if not isinstance(key, str) or (
            key in calls and raw.get("kind") == "http_call"
        ):
            raise BoundedUrlProofError("invalid callKey")
        if raw.get("kind") == "http_call":
            calls[key] = raw
    parsed: dict[str, BoundedUrlProof] = {}
    previous_sort_key: tuple[str, str] | None = None
    for raw in proofs:
        if not isinstance(raw, Mapping) or set(raw) != {"version", "callKey", "normalizedPath", "placeholders"}:
            raise BoundedUrlProofError("invalid bounded URL proof")
        _integer(raw.get("version"), "version", 1, 1)
        call_key = raw.get("callKey")
        path = raw.get("normalizedPath")
        if not isinstance(call_key, str) or not 1 <= len(call_key) <= 512 or re.search(r"[\x00-\x1f\x7f]", call_key):
            raise BoundedUrlProofError("invalid callKey")
        if not isinstance(path, str):
            raise BoundedUrlProofError("proof path does not bind call")
        sort_key = (call_key, path)
        if previous_sort_key is not None and sort_key < previous_sort_key:
            raise BoundedUrlProofError("boundedUrlProofs are not canonically sorted")
        previous_sort_key = sort_key
        if call_key not in calls or call_key not in key_to_id:
            raise BoundedUrlProofError("invalid callKey")
        call = calls[call_key]
        metadata = call.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("urlResolution") != "bounded_template" or path != metadata.get("normalizedPath"):
            raise BoundedUrlProofError("proof path does not bind call")
        placeholders = raw.get("placeholders")
        if not isinstance(placeholders, list) or not 1 <= len(placeholders) <= 32:
            raise BoundedUrlProofError("invalid placeholders")
        segments = path.split("/")[1:]
        expected = [(segment[1:-1], index) for index, segment in enumerate(segments) if PLACEHOLDER_RE.fullmatch(segment) and segment[1] == "p"]
        if (
            len(expected) != len(placeholders)
            or any("{u" in segment for segment in segments)
            or any(token != f"p{ordinal}" for ordinal, (token, _) in enumerate(expected))
        ):
            raise BoundedUrlProofError("proof placeholders do not cover template")
        entries: list[BoundedUrlPlaceholderProof] = []
        product = 1
        for ordinal, item in enumerate(placeholders):
            if not isinstance(item, Mapping) or set(item) != {"token", "segmentIndex", "memberCount", "acceptedConverters"}:
                raise BoundedUrlProofError("invalid placeholder")
            token = item.get("token")
            segment_index = _integer(item.get("segmentIndex"), "segmentIndex", 0, 255)
            count = _integer(item.get("memberCount"), "memberCount", 1, 256)
            converters = item.get("acceptedConverters")
            if not isinstance(token, str) or (token, segment_index) != expected[ordinal] or not isinstance(converters, list) or not converters or converters != sorted(set(converters)) or any(not isinstance(value, str) or value not in {"int", "str", "slug", "uuid"} for value in converters):
                raise BoundedUrlProofError("invalid placeholder")
            product *= count
            entries.append(BoundedUrlPlaceholderProof(token, segment_index, count, tuple(converters)))
        if product > 4096:
            raise BoundedUrlProofError("proof domain too large")
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > 8192:
            raise BoundedUrlProofError("proof too large")
        call_id = key_to_id[call_key]
        if call_id in parsed:
            raise BoundedUrlProofError("duplicate proof")
        parsed[call_id] = BoundedUrlProof(1, call_id, path, tuple(entries))
    bounded_call_ids = {key_to_id[key] for key, call in calls.items() if call.get("metadata", {}).get("urlResolution") == "bounded_template"}
    if set(parsed) != bounded_call_ids:
        raise BoundedUrlProofError("missing or extra bounded URL proof")
    return parsed
