from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kg_debugger.graph.store import GraphStore, GraphStoreError
from kg_debugger.http import EndpointConfig, normalize_endpoint
from kg_debugger.runtime.middleware import RuntimeEvidenceMiddleware
from kg_debugger.runtime.schema import (
    RuntimeEventValidationError,
    validate_capture_id,
    validate_runtime_event,
)


def runtime_test_view() -> None:
    return None


class RuntimeTestView:
    pass



class RuntimeSchemaTests(unittest.TestCase):
    def event(self, **updates: object) -> dict[str, object]:
        return {"captureId": "capture-1", "method": "GET", "path": "/orders", **updates}

    def assert_rejected(self, event: object) -> None:
        with self.assertRaises(RuntimeEventValidationError):
            validate_runtime_event(event)

    def test_capture_id_boundaries_and_types(self) -> None:
        self.assertEqual(validate_capture_id("a"), "a")
        self.assertEqual(validate_capture_id("a" * 128), "a" * 128)
        for value in ("", "-capture", "a" * 129, "capture id", "café", 1, True, None):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeEventValidationError):
                    validate_capture_id(value)

    def test_required_and_optional_field_boundaries(self) -> None:
        self.assertEqual(validate_runtime_event(self.event()).payload, self.event())
        for method in ("get", "CONNECT", "", None, True):
            with self.subTest(method=method):
                self.assert_rejected(self.event(method=method))
        for path in ("/", "/" + "a" * 2047):
            with self.subTest(path=path):
                self.assertEqual(validate_runtime_event(self.event(path=path)).payload["path"], path)
        for path in ("", "orders", "//orders", "/a//b", "/a?x=1", "/a#x", "/a\\b", "/a%2fb", "/a%5Cb", "/a%", "/a%ff", "/./a", "/a/%2e%2e/b", "/a\n"):
            with self.subTest(path=path):
                self.assert_rejected(self.event(path=path))
        for status in (100, 599):
            self.assertEqual(validate_runtime_event(self.event(status=status)).payload["status"], status)
        for status in (99, 600, 200.0, True, None):
            with self.subTest(status=status):
                self.assert_rejected(self.event(status=status))
        for duration in (0, 86_400_000, 1.5):
            self.assertEqual(validate_runtime_event(self.event(durationMs=duration)).payload["durationMs"], duration)
        for duration in (-1, 86_400_001, True, math.inf, math.nan, "1", None):
            with self.subTest(duration=duration):
                self.assert_rejected(self.event(durationMs=duration))

    def test_target_endpoint_and_trace_cross_field_rules(self) -> None:
        self.assertEqual(
            validate_runtime_event(self.event(target="https://localhost:8443/orders")).payload["target"],
            "https://localhost:8443/orders",
        )
        for target in (
            "https://example.test/orders",
            "ftp://localhost/orders",
            "https://user@localhost/orders",
            "https://localhost/orders?",
            "https://localhost/orders?q=1",
            "https://localhost/orders#",
            "https://localhost/orders#part",
            "https://localhost/other",
            "https://localhost:bad/orders",
        ):
            with self.subTest(target=target):
                self.assert_rejected(self.event(target=target))
        config = EndpointConfig()
        endpoint_id = normalize_endpoint("GET", "/orders", config)["id"]
        self.assertEqual(validate_runtime_event(self.event(endpointId=endpoint_id), endpoint_config=config).payload["endpointId"], endpoint_id)
        self.assert_rejected(self.event(endpointId="GET /other"))
        traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        self.assertEqual(validate_runtime_event(self.event(traceparent=traceparent)).payload["traceparent"], traceparent)
        self.assert_rejected(self.event(tracestate="vendor=value"))
        self.assert_rejected(self.event(traceparent=traceparent, tracestate="vendor=secret-token"))
        self.assert_rejected(self.event(traceparent="00-" + "0" * 32 + "-0123456789abcdef-01"))
        self.assert_rejected(self.event(viewQualifiedName="module.<locals>.view"))
        self.assert_rejected(self.event(viewQualifiedName="module:bad"))
        self.assertEqual(validate_runtime_event(self.event(viewQualifiedName="package.View.dispatch")).payload["viewQualifiedName"], "package.View.dispatch")
        for qualified in ("a.", ".a", "a..b", "a.1"):
            with self.subTest(qualified=qualified):
                self.assert_rejected(self.event(viewQualifiedName=qualified))
        longest = "a." + "A" * 510
        self.assertEqual(
            validate_runtime_event(self.event(viewQualifiedName=longest)).payload["viewQualifiedName"],
            longest,
        )

    def test_closed_shape_and_exact_legacy_normalization(self) -> None:
        self.assert_rejected(self.event(project="x"))
        self.assert_rejected(self.event(body={"password": "secret"}))
        self.assert_rejected(self.event(trace={"baggage": "x"}))
        legacy = {"runId": "capture-1", "method": "GET", "path": "/orders", "view": None, "trace": {}}
        result = validate_runtime_event(legacy)
        self.assertEqual(result.payload, {"captureId": "capture-1", "method": "GET", "path": "/orders"})
        self.assertEqual(result.warnings, ("legacy_runtime_event_v1",))
        normalized = validate_runtime_event({"runId": "capture-1", "method": "GET", "path": "/orders", "view": "package.view", "trace": {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"}})
        self.assertEqual(normalized.payload["viewQualifiedName"], "package.view")
        self.assertIn("traceparent", normalized.payload)
        for mixed in (
            self.event(runId="capture-1"),
            self.event(viewQualifiedName="package.view", view="package.view"),
            self.event(traceparent="00-0123456789abcdef0123456789abcdef-0123456789abcdef-01", trace={}),
        ):
            self.assert_rejected(mixed)
    def test_canonical_identity_legacy_and_trace_boundaries(self) -> None:
        traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        event = self.event(captureId="capture:one", traceparent=traceparent)
        self.assertEqual(validate_runtime_event(event).payload, event)
        for path in ("/with space", "/encoded%20space", "/%09tab"):
            with self.subTest(path=path):
                self.assert_rejected(self.event(path=path))
        for event in (
            {"method": "GET", "path": "/orders", "view": "package.view"},
            {"runId": "capture-1", "method": "GET", "path": "/orders", "captureId": "capture-1"},
            {"runId": "capture-1", "method": "GET", "path": "/orders", "endpointId": "GET /orders"},
            self.event(viewQualifiedName="module"),
            self.event(viewQualifiedName="módulo.view"),
        ):
            with self.subTest(event=event):
                self.assert_rejected(event)


class RuntimeStoreAdmissionTests(unittest.TestCase):
    project = "repo"
    repository_set_id = "a" * 64
    manifest = [{"namespace": "repo"}]
    scope = "11111111-1111-4111-8111-111111111111"

    def _event(self, capture_id: str = "capture:one") -> dict[str, object]:
        return {
            "captureId": capture_id,
            "method": "GET",
            "path": "/orders",
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        }

    def _event_id(self, ordinal: int) -> str:
        return f"00000000-0000-4000-8000-{ordinal:012x}"

    def test_store_admission_uses_canonical_validator_and_exact_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GraphStore(
                Path(directory) / "graph.db",
                self.project,
                self.repository_set_id,
                self.manifest,
            )
            store.add_runtime_event(
                self._event_id(1), self.scope, "capture:one", self._event()
            )
            with self.assertRaises(GraphStoreError):
                store.add_runtime_event(
                    "legacy-" + "a" * 64,
                    self.scope,
                    "capture:one",
                    self._event(),
                )
            with self.assertRaises(GraphStoreError):
                store.add_runtime_event(
                    "00000000-0000-1000-8000-000000000002",
                    self.scope,
                    "capture:one",
                    self._event(),
                )
            with self.assertRaises(GraphStoreError):
                store.add_runtime_event(
                    self._event_id(3),
                    self.scope,
                    "capture:one",
                    {"captureId": "capture:one", "method": "GET", "path": "/orders", "body": "x"},
                )
            self.assertEqual(len(store.list_runtime_events(self.scope, "capture:one")), 1)

    def test_capture_has_no_undocumented_ceiling_and_restart_scope_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.db"
            store = GraphStore(path, self.project, self.repository_set_id, self.manifest)
            for ordinal in range(40):
                store.add_runtime_event(
                    self._event_id(ordinal), self.scope, "capture:one", self._event()
                )
            other_scope = "22222222-2222-4222-8222-222222222222"
            store.add_runtime_event(
                self._event_id(40), other_scope, "capture:one", self._event()
            )
            store.add_runtime_event(
                self._event_id(41), self.scope, "capture:two", self._event("capture:two")
            )
            restarted = GraphStore(path, self.project, self.repository_set_id, self.manifest)
            self.assertEqual(len(restarted.list_runtime_events(self.scope, "capture:one")), 40)
            self.assertEqual(len(restarted.list_runtime_events(other_scope, "capture:one")), 1)
            self.assertEqual(len(restarted.list_runtime_events(self.scope, "capture:two")), 1)


class RuntimeMiddlewareTests(unittest.TestCase):
    def test_disabled_middleware_is_untouched_pass_through(self) -> None:
        request = object()
        response = object()
        middleware = RuntimeEvidenceMiddleware(lambda received: response)
        self.assertIs(middleware(request), response)

    def test_enabled_constructor_matrix(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeEvidenceMiddleware(lambda request: request, enabled=True)
        with self.assertRaises(RuntimeEventValidationError):
            RuntimeEvidenceMiddleware(lambda request: request, collector=lambda event: None, capture_id=" bad", enabled=True)
        RuntimeEvidenceMiddleware(lambda request: request, collector=lambda event: None, capture_id="capture-1", enabled=True)

    def test_emits_stable_canonical_event_and_proven_function(self) -> None:
        events: list[dict[str, object]] = []

        request = SimpleNamespace(method="GET", path="/orders", resolver_match=SimpleNamespace(func=runtime_test_view))
        response = SimpleNamespace(status_code=201)
        middleware = RuntimeEvidenceMiddleware(lambda received: response, collector=events.append, capture_id="capture-1", enabled=True)
        with patch("kg_debugger.runtime.middleware.time.perf_counter", side_effect=(1.0, 1.25)):
            self.assertIs(middleware(request), response)
        self.assertEqual(events, [{"captureId": "capture-1", "method": "GET", "path": "/orders", "status": 201, "durationMs": 250.0, "viewQualifiedName": f"{__name__}.runtime_test_view"}])

    def test_emits_cbv_and_omits_unproven_and_forbidden_content(self) -> None:
        events: list[dict[str, object]] = []
        cbv_function = SimpleNamespace(view_class=RuntimeTestView)
        request = SimpleNamespace(method="POST", path="/orders", resolver_match=SimpleNamespace(func=cbv_function), body=b"secret", user="user", session={})
        response = SimpleNamespace(status_code=204)
        middleware = RuntimeEvidenceMiddleware(lambda received: response, collector=events.append, capture_id="capture-1", enabled=True)
        with patch("kg_debugger.runtime.middleware.time.perf_counter", side_effect=(2.0, 2.0)):
            middleware(request)
        self.assertEqual(events[0]["viewQualifiedName"], f"{__name__}.RuntimeTestView")
        self.assertTrue({"body", "headers", "cookies", "auth", "user", "session", "view", "runId"}.isdisjoint(events[0]))
