from __future__ import annotations

import json
import shutil
import ssl
import tempfile
import threading
import unittest
from http.client import HTTPSConnection
from pathlib import Path

from kg_debugger.app import build_server
from kg_debugger.config import DebuggerConfig


class ServerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        workspace = Path(self.tmp.name)
        (workspace / "web").mkdir()
        (workspace / "web" / "index.html").write_text("<!doctype html><title>t</title>")
        pem = workspace / "pem"
        pem.mkdir()
        repository = Path(__file__).resolve().parents[2]
        shutil.copyfile(repository / "pem" / "cert.pem", pem / "cert.pem")
        shutil.copyfile(repository / "pem" / "key.pem", pem / "key.pem")
        config = DebuggerConfig.from_dict(
            workspace,
            {"project": "security", "repoRoots": ["."], "bindHost": "127.0.0.1", "bindPort": 0},
        )
        self.server = build_server(config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.context = ssl.create_default_context(cafile=str(repository / "pem" / "cert.pem"))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPSConnection("127.0.0.1", self.port, timeout=5, context=self.context)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def headers(self, **extra: str) -> dict[str, str]:
        return {"Host": f"127.0.0.1:{self.port}", **extra}

    def test_server_requires_tls_1_2_or_newer(self) -> None:
        self.assertEqual(self.server.socket.context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_https_loopback_health_requires_exact_authority(self) -> None:
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            status, headers, body = self.request("GET", "/api/health", headers={"Host": host})
            self.assertEqual(status, 200, host)
            self.assertEqual(json.loads(body), {"ok": True, "status": "ready"})
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(headers["x-content-type-options"], "nosniff")
            self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])

    def test_host_and_query_rejections_use_strict_error_envelopes(self) -> None:
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1"):
            status, _, body = self.request("GET", "/api/health", headers={"Host": host})
            self.assertEqual(status, 421, host)
            self.assertEqual(json.loads(body), {"error": "misdirected_request"})
        status, _, body = self.request("GET", "/api/health?project=other", headers=self.headers())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_selector"})

    def test_mutations_require_same_https_origin_and_capability(self) -> None:
        content_headers = {"Content-Type": "application/json"}
        for origin in ("https://evil.example", f"http://127.0.0.1:{self.port}", "null"):
            status, _, body = self.request(
                "POST",
                "/api/analyze",
                headers=self.headers(Origin=origin, **content_headers),
                body="{}",
            )
            self.assertEqual(status, 403, origin)
            self.assertEqual(json.loads(body), {"error": "origin_forbidden"})
        status, _, body = self.request(
            "POST",
            "/api/analyze",
            headers=self.headers(Origin=f"https://localhost:{self.port}", **content_headers),
            body="{}",
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "mutation_forbidden"})

    def test_valid_mutation_guards_precede_unknown_endpoint(self) -> None:
        capability = self.server.capability
        for origin in (None, f"https://127.0.0.1:{self.port}"):
            headers = self.headers(**{"X-KG-Debugger-Capability": capability})
            if origin is not None:
                headers["Origin"] = origin
            status, _, body = self.request("POST", "/api/not-found", headers=headers, body="{}")
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(body), {"error": "not_found"})


if __name__ == "__main__":
    unittest.main()
