"""Deterministic digests for quarantined SQLite values."""

from __future__ import annotations

import hashlib
import struct
from typing import Final

_PREFIX: Final = b"kg-debugger:qv2\x00"
_SOURCE_KEY_FIELDS: Final[dict[str, tuple[str, str]]] = {
    "runtime_events": ("source-key/runtime_events", "id"),
    "graph_snapshots": ("source-key/graph_snapshots", "project"),
    "analysis_runs": ("source-key/analysis_runs", "id"),
}
_PAYLOAD_FIELDS: Final[dict[str, tuple[str, str]]] = {
    "runtime_events": ("payload/runtime_events", "payload"),
    "graph_snapshots": ("payload/graph_snapshots", "payload"),
    "analysis_runs": ("payload/analysis_runs", "diagnostics"),
}
_SQLITE_TAGS: Final[dict[str, bytes]] = {
    "null": b"N",
    "integer": b"I",
    "real": b"R",
    "text": b"T",
    "blob": b"B",
}
_SQLITE_INTEGER_MIN: Final = -(2**63)
_SQLITE_INTEGER_MAX: Final = 2**63 - 1


def _frame(tag: bytes, payload: bytes) -> bytes:
    if len(tag) != 1 or tag not in {b"K", b"N", b"I", b"R", b"T", b"B"}:
        raise ValueError("frame tag must be a supported single-byte tag")
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("frame payload exceeds the qv2 size limit")
    return tag + f"{len(payload):08x}".encode("ascii") + b":" + payload


def _semantic_text(value: str, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    return value.encode("utf-8")


def _sqlite_value_frame(sqlite_type: str, value: object) -> bytes:
    try:
        tag = _SQLITE_TAGS[sqlite_type]
    except (KeyError, TypeError) as exc:
        raise ValueError("sqlite_type must be null, integer, real, text, or blob") from exc

    if sqlite_type == "null":
        if value is not None:
            raise TypeError("NULL values must be None")
        payload = b""
    elif sqlite_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("INTEGER values must be integers")
        if not _SQLITE_INTEGER_MIN <= value <= _SQLITE_INTEGER_MAX:
            raise ValueError("INTEGER value is outside SQLite's signed 64-bit range")
        payload = str(value).encode("ascii")
    elif sqlite_type == "real":
        if not isinstance(value, float):
            raise TypeError("REAL values must be floats")
        payload = struct.pack(">d", value)
    else:
        if not isinstance(value, bytes):
            raise TypeError(f"{sqlite_type.upper()} values must be exact bytes")
        payload = value
    return _frame(tag, payload)


def encode_quarantine_frames(
    domain: str, column_name: str, sqlite_type: str, value: object
) -> bytes:
    """Encode the qv2 preimage for one original SQLite table value.

    TEXT and BLOB values must be supplied as exact bytes, typically from
    ``CAST(column AS BLOB)`` in the migration query.
    """
    return b"".join(
        (
            _PREFIX,
            _frame(b"K", _semantic_text(domain, "domain")),
            _frame(b"K", _semantic_text(column_name, "column_name")),
            _sqlite_value_frame(sqlite_type, value),
        )
    )


def _digest(field_map: dict[str, tuple[str, str]], source_table: str, sqlite_type: str, value: object) -> str:
    try:
        domain, column_name = field_map[source_table]
    except (KeyError, TypeError) as exc:
        raise ValueError("source_table is not a quarantinable table") from exc
    return hashlib.sha256(
        encode_quarantine_frames(domain, column_name, sqlite_type, value)
    ).hexdigest()


def source_key_sha256(source_table: str, sqlite_type: str, value: object) -> str:
    """Return the qv2 digest of a quarantined row's original key field."""
    return _digest(_SOURCE_KEY_FIELDS, source_table, sqlite_type, value)


def payload_sha256(source_table: str, sqlite_type: str, value: object) -> str:
    """Return the qv2 digest of a quarantined row's original payload field."""
    return _digest(_PAYLOAD_FIELDS, source_table, sqlite_type, value)
