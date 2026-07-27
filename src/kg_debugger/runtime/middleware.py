from __future__ import annotations

import inspect
import time
from typing import Any, Callable

from .schema import validate_capture_id


class RuntimeEvidenceMiddleware:
    """Disabled-by-default Django middleware with a closed event surface."""

    def __init__(
        self,
        get_response: Callable[[Any], Any],
        *,
        collector: Callable[[dict[str, object]], object] | None = None,
        capture_id: str | None = None,
        enabled: bool = False,
    ) -> None:
        self.get_response = get_response
        self.enabled = enabled
        self.collector = collector
        self.capture_id: str | None = None
        if enabled:
            if not callable(collector):
                raise ValueError("enabled runtime middleware requires a callable collector")
            self.capture_id = validate_capture_id(capture_id)

    def __call__(self, request: Any) -> Any:
        if not self.enabled:
            return self.get_response(request)
        assert self.capture_id is not None

        started = time.perf_counter()
        response = self.get_response(request)
        event: dict[str, object] = {
            "captureId": self.capture_id,
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "durationMs": round((time.perf_counter() - started) * 1000, 3),
        }
        qualified_view = _resolved_view_qualified_name(request)
        if qualified_view is not None:
            event["viewQualifiedName"] = qualified_view
        assert self.collector is not None
        self.collector(event)
        return response


def _resolved_view_qualified_name(request: Any) -> str | None:
    resolver_match = getattr(request, "resolver_match", None)
    function = getattr(resolver_match, "func", None)
    view_class = getattr(function, "view_class", None)
    if inspect.isclass(view_class):
        return _qualified_name(view_class)
    if inspect.isfunction(function) or inspect.ismethod(function):
        return _qualified_name(function)
    return None


def _qualified_name(value: object) -> str | None:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        return None
    qualified = f"{module}.{qualname}"
    if "<locals>" in qualified or not all(part.isidentifier() for part in qualified.split(".")):
        return None
    return qualified
