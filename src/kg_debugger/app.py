from __future__ import annotations

import argparse
import http.client
import io
import json
import re
import secrets
import socket
import ssl
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit

from .config import DebuggerConfig
from .graph.store import SnapshotIncompatible, SnapshotInvalid, SnapshotNotFound
from .orchestrator import Orchestrator
from .runtime.schema import (
    RuntimeEventValidationError,
    validate_capture_id,
    validate_runtime_event,
)
from .security import cert_paths, configure_safe_logging

LOGGER = configure_safe_logging()
_MAX_BODY = 1_048_576
_REG_NAME_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_FORBIDDEN_AUTHORITY_CHARS = frozenset("%[]@,/?#\\")


def _read_raw_headers(stream: Any) -> list[bytes]:
    lines: list[bytes] = []
    while True:
        line = stream.readline(65_537)
        if len(line) > 65_536:
            raise http.client.LineTooLong("header line")
        lines.append(line)
        if len(lines) > 100:
            raise http.client.HTTPException("too many headers")
        if line in {b"\r\n", b"\n", b""}:
            return lines


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, code: str, extra: dict[str, Any] | None = None) -> None:
        self.status, self.code, self.extra = status, code, extra or {}


class DebuggerHandler(SimpleHTTPRequestHandler):
    server: "DebuggerServer"
    protocol_version = "HTTP/1.1"
    def end_headers(self) -> None:
        self.send_header("content-security-policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        route = "api" if self.path.split("?", 1)[0].split("#", 1)[0].startswith("/api/") else "static"
        status = str(args[1]) if len(args) > 1 and str(args[1]).isdecimal() else "unknown"
        method = self.command if self.command in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "TRACE", "CONNECT"} else "other"
        LOGGER.info("request method=%s route=%s status=%s", method, route, status)

    def translate_path(self, path: str) -> str:
        web_dist = self.server.workspace_root / "web" / "dist"
        web_root = web_dist if web_dist.exists() else self.server.workspace_root / "web"
        rel = urlsplit(path).path.lstrip("/") or "index.html"
        candidate = (web_root / rel).resolve()
        try:
            candidate.relative_to(web_root.resolve())
        except ValueError:
            return str(web_root / "index.html")
        if not candidate.exists() and "." not in Path(rel).name:
            return str(web_root / "index.html")
        return str(candidate / "index.html" if candidate.is_dir() else candidate)
    def handle_one_request(self) -> None:
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = self.request_version = self.command = ""
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            words = self.raw_requestline.decode("iso-8859-1").rstrip("\r\n").split()
            if len(words) == 3:
                try:
                    raw_headers = _read_raw_headers(self.rfile)
                except (http.client.LineTooLong, http.client.HTTPException):
                    self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
                    return
                original_rfile = self.rfile
                self.rfile = io.BytesIO(b"".join(raw_headers))
                try:
                    parsed = self.parse_request()
                finally:
                    self.rfile = original_rfile
                self._raw_header_lines = tuple(raw_headers)
            else:
                parsed = self.parse_request()
                self._raw_header_lines = ()
            if not parsed:
                return
            method = getattr(self, f"do_{self.command}", None)
            if method is None:
                self._method_not_allowed(mutation=True)
            else:
                method()
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, TimeoutError):
            self.close_connection = True

    def do_GET(self) -> None:
        try:
            self._host()
            parsed = self._request_target()
            self._require_api_no_query(parsed)
            if parsed.path == "/api/health":
                self._json({"ok": True, "status": "ready"})
            elif parsed.path == "/api/config":
                payload = self.server.config.to_dict()
                payload["mutationCapability"] = self.server.capability
                self._json(payload)
            elif parsed.path == "/api/graph":
                try:
                    snapshot = self.server.orchestrator.store.load_snapshot()
                    self._json(snapshot.to_dict())
                except SnapshotNotFound:
                    self._json({"error": "snapshot_not_found", "action": "analyze"}, HTTPStatus.NOT_FOUND)
                except SnapshotIncompatible as exc:
                    self._json(
                        {"error": "snapshot_incompatible", "code": exc.code, "action": "reanalyze"},
                        HTTPStatus.CONFLICT,
                    )
                except SnapshotInvalid:
                    self._json({"error": "snapshot_invalid", "action": "delete_or_reanalyze"}, HTTPStatus.UNPROCESSABLE_ENTITY)
            elif parsed.path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "not_found")
            else:
                super().do_GET()
        except RequestError as exc:
            self._error(exc.status, exc.code, extra=exc.extra)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.close_connection = True
        except Exception:
            LOGGER.error("GET handler failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error")

    def do_OPTIONS(self) -> None:
        self._method_not_allowed()

    def do_POST(self) -> None:
        # Every mutation, including a rejected one, is a single close-delimited exchange.
        try:
            self._host()
            self._origin()
            self._capability()
            parsed = self._request_target()
            self._require_api_no_query(parsed)
            if parsed.path not in {"/api/analyze", "/api/runtime"}:
                raise RequestError(HTTPStatus.NOT_FOUND, "not_found")
            if parsed.path == "/api/runtime" and not self.server.config.runtime_enabled:
                raise RequestError(HTTPStatus.FORBIDDEN, "runtime_disabled")
            payload = self._read_json()
            if parsed.path == "/api/analyze":
                if (
                    set(payload) - {"runtimeCaptureId"}
                    or "runtimeCaptureId" in payload
                    and (payload["runtimeCaptureId"] is None or not isinstance(payload["runtimeCaptureId"], str))
                ):
                    raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_request")
                capture = validate_capture_id(payload["runtimeCaptureId"]) if "runtimeCaptureId" in payload else None
                if capture is not None and not self.server.config.runtime_enabled:
                    raise RequestError(
                        HTTPStatus.CONFLICT,
                        "runtime_unavailable",
                        {"action": "analyze_without_capture"},
                    )
                snapshot = self.server.orchestrator.analyze(capture)
                self._json(snapshot.to_dict(), close=True)
            else:
                event = validate_runtime_event(payload, endpoint_config=self.server.config.endpoint)
                event_id, received_at = self.server.orchestrator.record_runtime_event(event.payload)
                self._json(
                    {
                        "ok": True,
                        "eventId": event_id,
                        "captureId": event.payload["captureId"],
                        "receivedAt": received_at,
                        "warnings": list(event.warnings),
                    },
                    HTTPStatus.ACCEPTED,
                    close=True,
                )
        except RequestError as exc:
            self._error(exc.status, exc.code, close=True, extra=exc.extra)
        except RuntimeEventValidationError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", close=True)
        except socket.timeout:
            self._error(HTTPStatus.REQUEST_TIMEOUT, "request_timeout", close=True)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.close_connection = True
        except Exception:
            LOGGER.error("POST handler failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", close=True)

    def do_PUT(self) -> None:
        self._method_not_allowed(mutation=True)

    def do_PATCH(self) -> None:
        self._method_not_allowed(mutation=True)

    def do_DELETE(self) -> None:
        self._method_not_allowed(mutation=True)

    def do_HEAD(self) -> None:
        self._method_not_allowed()
    def do_TRACE(self) -> None:
        self._method_not_allowed(mutation=True)

    def do_CONNECT(self) -> None:
        self._method_not_allowed(mutation=True)

    def _method_not_allowed(self, *, mutation: bool = False) -> None:
        try:
            self._host()
            if mutation:
                self._origin()
                self._capability()
            self._require_api_no_query(self._request_target())
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", close=mutation)
        except RequestError as exc:
            self._error(exc.status, exc.code, close=mutation, extra=exc.extra)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.close_connection = True
        except Exception:
            LOGGER.error("method handler failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", close=mutation)

    def _host(self) -> str:
        values = self.headers.get_all("Host") or []
        if len(values) != 1:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_host_header")
        value = values[0]
        host, port = self._authority(value, "invalid_host_header")
        if host not in {"127.0.0.1", "localhost"} or port != str(self.server.server_address[1]):
            raise RequestError(HTTPStatus.MISDIRECTED_REQUEST, "misdirected_request")
        return value

    def _origin(self) -> None:
        values = self.headers.get_all("Origin") or []
        if not values:
            return
        if len(values) != 1:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_origin_header")
        value = values[0]
        if value == "null":
            raise RequestError(HTTPStatus.FORBIDDEN, "origin_forbidden")
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_origin_header")
        scheme, separator, authority = value.partition("://")
        if not separator or not _SCHEME_RE.fullmatch(scheme):
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_origin_header")
        host, port = self._authority(authority, "invalid_origin_header")
        accepted = {
            f"{self.server.scheme}://127.0.0.1:{self.server.server_address[1]}",
            f"{self.server.scheme}://localhost:{self.server.server_address[1]}",
        }
        if host not in {"127.0.0.1", "localhost"} or port != str(self.server.server_address[1]) or value not in accepted:
            raise RequestError(HTTPStatus.FORBIDDEN, "origin_forbidden")

    @staticmethod
    def _authority(value: str, error: str) -> tuple[str, str | None]:
        if (
            not value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in _FORBIDDEN_AUTHORITY_CHARS for char in value)
            or value.count(":") > 1
        ):
            raise RequestError(HTTPStatus.BAD_REQUEST, error)
        host, separator, port = value.partition(":")
        if not _REG_NAME_RE.fullmatch(host):
            raise RequestError(HTTPStatus.BAD_REQUEST, error)
        if not separator:
            return host, None
        if (
            not port.isascii()
            or not port.isdecimal()
            or port.startswith("0")
            or int(port) > 65535
        ):
            raise RequestError(HTTPStatus.BAD_REQUEST, error)
        return host, port

    def _capability(self) -> None:
        values = self.headers.get_all("X-KG-Debugger-Capability") or []
        if len(values) > 1:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_capability_header")
        if len(values) != 1 or not secrets.compare_digest(values[0], self.server.capability):
            raise RequestError(HTTPStatus.FORBIDDEN, "mutation_forbidden")

    def _request_target(self) -> SplitResult:
        try:
            return urlsplit(self.path)
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_selector") from exc

    def _require_api_no_query(self, parsed: Any) -> None:
        if parsed.path.startswith("/api/") and ("?" in self.path or "#" in self.path):
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_selector")

    def _read_json(self) -> dict[str, Any]:
        transfer = self.headers.get_all("Transfer-Encoding") or []
        if transfer:
            raise RequestError(HTTPStatus.BAD_REQUEST, "unsupported_transfer_encoding")
        lengths = [
            value.rstrip(b"\r\n")
            for line in self._raw_header_lines
            for name, separator, value in [line.partition(b":")]
            if separator and name.lower() == b"content-length"
        ]
        if not lengths:
            raise RequestError(HTTPStatus.LENGTH_REQUIRED, "length_required")
        if (
            len(lengths) != 1
            or not re.fullmatch(rb" [1-9][0-9]*", lengths[0])
            or len(lengths[0]) > len(str(_MAX_BODY)) + 1
        ):
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_content_length")
        digits = lengths[0][1:]
        if len(digits) > len(str(_MAX_BODY)) or (
            len(digits) == len(str(_MAX_BODY))
            and digits > str(_MAX_BODY).encode("ascii")
        ):
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large")
        length = int(digits)
        media = self.headers.get_all("Content-Type") or []
        if len(media) != 1 or not self._is_json_media_type(media[0]):
            raise RequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type")
        original_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
        finally:
            self.connection.settimeout(original_timeout)
        if len(raw) != length:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_json_body")
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid_json_body") from exc
        return data
    @staticmethod
    def _is_json_media_type(value: str) -> bool:
        parts = value.split(";")
        if parts[0].strip(" \t").casefold() != "application/json":
            return False
        if len(parts) == 1:
            return True
        if len(parts) != 2:
            return False
        name, separator, charset = parts[1].partition("=")
        return (
            separator == "="
            and name.strip(" \t").casefold() == "charset"
            and charset.strip(" \t").casefold() == "utf-8"
        )

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, *, close: bool = False) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        if close:
            self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)
        if close:
            self.close_connection = True

    def _error(
        self,
        status: HTTPStatus,
        code: str,
        *,
        close: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._json({"error": code, **(extra or {})}, status, close=close)


class DebuggerServer(ThreadingHTTPServer):
    def __init__(self, config: DebuggerConfig) -> None:
        self.config, self.workspace_root = config, config.workspace_root
        self.capability = secrets.token_urlsafe(32)
        self.runtime_scope_id = str(uuid.uuid4())
        self.orchestrator = Orchestrator(config, self.runtime_scope_id)
        self.scheme = "https"
        super().__init__(("127.0.0.1", config.bind_port), DebuggerHandler)


def build_server(config: DebuggerConfig) -> DebuggerServer:
    server = DebuggerServer(config)
    cert, key = cert_paths(config.workspace_root)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--repo", action="append", default=None)
    parser.add_argument("--base-path", action="append", default=None)
    parser.add_argument("--runtime", action="store_true")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace, workspace: Path) -> DebuggerConfig:
    roots = [args.project] if args.project else (args.repo or ["."])
    project = Path(args.project).name if args.project else workspace.name
    return DebuggerConfig.from_dict(workspace, {"project": project, "repoRoots": roots, "endpoint": {"basePaths": args.base_path or []}, "runtimeEnabled": args.runtime, "bindHost": args.host, "bindPort": args.port if args.port is not None else 8443})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_args(args, Path.cwd())
    server = build_server(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
