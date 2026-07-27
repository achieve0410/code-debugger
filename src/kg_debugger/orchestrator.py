from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .adapters.django import analyze_django
from .config import DebuggerConfig
from .graph.merge import (
    CanonicalFragment,
    FragmentValidationError,
    _diagnostic,
    canonicalize_fragment,
    merge_canonical_fragments,
)
from .graph.schema import GraphSnapshot
from .graph.store import GraphStore
from .http import normalize_endpoint


class Orchestrator:
    """Owns the active repository set and its private runtime scope."""

    def __init__(self, config: DebuggerConfig, runtime_scope_id: str) -> None:
        self.config = config
        self.runtime_scope_id = runtime_scope_id
        self.store = GraphStore(config.store_path, config.project, config.repository_set_id, config.repository_manifest, endpoint_config=config.endpoint)

    def record_runtime_event(self, payload: dict[str, Any]) -> tuple[str, str]:
        event_id = str(uuid.uuid4())
        received_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        capture_id = str(payload["captureId"])
        self.store.add_runtime_event(event_id, self.runtime_scope_id, capture_id, payload, received_at=received_at)
        return event_id, received_at

    def analyze(self, runtime_capture_id: str | None = None) -> GraphSnapshot:
        fragments: list[CanonicalFragment] = []
        roots: dict[Path, str] = {}
        for repository in self.config.repositories:
            django = analyze_django(
                repository.resolved_root, repository.namespace, self.config.endpoint,
                repository_set_id=self.config.repository_set_id, repositories=self.config.repository_manifest,
            )
            django["project"] = self.config.project
            fragments.append(canonicalize_fragment(django))
            roots[repository.resolved_root] = repository.namespace
        frontend_fragments, frontend_diagnostics = self._run_frontend_analyzer(roots)
        fragments.extend(frontend_fragments)
        if not fragments:
            raise RuntimeError("no canonical graph fragments")
        snapshot = merge_canonical_fragments(*fragments, active_manifest=self.config.repository_manifest)
        if frontend_diagnostics:
            raw = snapshot.to_dict()
            raw["diagnostics"] = sorted([*raw["diagnostics"], *frontend_diagnostics], key=lambda item: item["id"])
            snapshot = GraphSnapshot.from_dict(raw)
        snapshot.validate_persistable()
        self.store.save_snapshot(snapshot)
        self.store.save_run(uuid.uuid4().hex, "complete", snapshot.diagnostics)
        if runtime_capture_id is None:
            return snapshot
        if not self.config.runtime_enabled:
            raise RuntimeError("runtime_disabled")
        return self._overlay_runtime(snapshot, runtime_capture_id)

    def _run_frontend_analyzer(self, repository_by_root: dict[Path, str]) -> tuple[list[CanonicalFragment], list[dict[str, Any]]]:
        analyzer = self.config.workspace_root / "analyzers" / "index.mjs"
        node_override = os.environ.get("KG_DEBUGGER_NODE", "")
        node = Path(node_override) if node_override else self.config.workspace_root / "venv" / "node24.14.1" / "bin" / "node"
        if not analyzer.exists() or not node.exists():
            return [], [
                _diagnostic("frontend_analyzer_unavailable", repository=repository)
                for repository in repository_by_root.values()
            ]
        fragments: list[CanonicalFragment] = []
        diagnostics: list[dict[str, Any]] = []
        for root, repository in repository_by_root.items():
            try:
                completed = subprocess.run(
                    [str(node), str(analyzer), "--repository", repository, str(root)],
                    cwd=self.config.workspace_root,
                    check=False,
                    text=False,
                    capture_output=True,
                    timeout=_frontend_analyzer_timeout(),
                )
                if completed.returncode != 0:
                    diagnostics.append(_diagnostic("frontend_analyzer_failed", repository=repository))
                    continue
                raw_stdout: bytes | str = completed.stdout
                if isinstance(raw_stdout, bytes):
                    try:
                        stdout = raw_stdout.decode("utf-8")
                    except UnicodeDecodeError:
                        diagnostics.append(
                            _diagnostic(
                                "frontend_analyzer_invalid_output",
                                repository=repository,
                            )
                        )
                        continue
                else:
                    stdout = raw_stdout
                if not stdout.strip():
                    diagnostics.append(_diagnostic("frontend_analyzer_failed", repository=repository))
                    continue
                data = json.loads(stdout)
                if not isinstance(data, dict) or data.get("repository") != repository:
                    diagnostics.append(_diagnostic("frontend_analyzer_invalid_output", repository=repository))
                    continue
                data["repositorySetId"] = self.config.repository_set_id
                data["repositories"] = self.config.repository_manifest
                data["project"] = self.config.project
                fragments.append(canonicalize_fragment(data))
            except (OSError, subprocess.SubprocessError):
                diagnostics.append(_diagnostic("frontend_analyzer_failed", repository=repository))
            except json.JSONDecodeError:
                diagnostics.append(_diagnostic("frontend_analyzer_invalid_output", repository=repository))
            except FragmentValidationError as exc:
                diagnostics.append(
                    _diagnostic(
                        "bounded_url_proof_invalid"
                        if str(exc) == "bounded_url_proof_invalid"
                        else "frontend_analyzer_invalid_output",
                        repository=repository,
                    )
                )
        return fragments, diagnostics

    def _runtime_diagnostic(self, code: str, event_id: str | None = None, candidates: list[str] | None = None) -> dict[str, Any]:
        from .graph.contracts import DIAGNOSTIC_CATALOG
        from .graph.identity import diagnostic_identity
        spec = DIAGNOSTIC_CATALOG[code]
        result: dict[str, Any] = {"code": code, "severity": spec.severity, "message": spec.message}
        if event_id is not None:
            result["eventId"] = event_id
        if candidates is not None:
            result["candidateIds"] = sorted(candidates)
        fields = tuple(json.dumps(result.get(key), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for key in ("code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"))
        return {"id": diagnostic_identity(*fields), **result}

    def _overlay_runtime(self, static: GraphSnapshot, capture_id: str) -> GraphSnapshot:
        # Serialization is the clone boundary: static data was already validated and saved.
        raw = json.loads(json.dumps(static.to_dict(), sort_keys=True, separators=(",", ":")))
        events = self.store.list_runtime_events(self.runtime_scope_id, capture_id)
        if not events:
            raw["diagnostics"].append(self._runtime_diagnostic("runtime_capture_empty"))
            raw["diagnostics"].sort(key=lambda item: item["id"])
            return GraphSnapshot.from_dict(raw)
        diagnostics: list[dict[str, Any]] = []
        observed_by_target: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            event_id = event["eventId"]
            received_at = event["receivedAt"]
            payload = event["payload"]
            endpoint_id = normalize_endpoint(str(payload["method"]), str(payload["path"]), self.config.endpoint)["id"]
            urls = [
                node for node in raw["nodes"]
                if node["kind"] == "django_url_pattern"
                and node["metadata"].get("endpointId") == endpoint_id
            ]
            qualified_view = payload.get("viewQualifiedName")
            views = [
                node for node in raw["nodes"]
                if qualified_view
                and node["kind"] == "django_view"
                and node["metadata"].get("pythonQualifiedName") == qualified_view
            ]
            url_view_edges = [
                edge for edge in raw["edges"]
                if len(urls) == 1
                and len(views) == 1
                and edge["kind"] == "resolves_to"
                and edge["source"] == urls[0]["id"]
                and edge["target"] == views[0]["id"]
            ]
            candidates = [node["id"] for node in [*urls, *views]]
            if len(urls) != 1 or (qualified_view and len(views) != 1):
                diagnostics.append(
                    self._runtime_diagnostic(
                        "runtime_event_ambiguous"
                        if len(urls) > 1 or len(views) > 1
                        else "runtime_event_unmatched",
                        event_id,
                        candidates if len(urls) > 1 or len(views) > 1 else None,
                    )
                )
                continue
            if qualified_view and len(url_view_edges) != 1:
                diagnostics.append(
                    self._runtime_diagnostic(
                        "runtime_identity_conflict", event_id, candidates
                    )
                )
                continue
            endpoint_edges = [
                edge for edge in raw["edges"]
                if edge["kind"] == "resolves_to"
                and edge["target"] == urls[0]["id"]
                and next(
                    (
                        node for node in raw["nodes"]
                        if node["id"] == edge["source"]
                        and node["kind"] in {"http_call", "request_payload"}
                    ),
                    None,
                ) is not None
            ]
            endpoint_evidence = {
                "kind": "observed",
                "adapter": "kg_debugger.runtime",
                "adapterVersion": "1",
                "reason": "runtime_coherent_endpoint",
                "eventId": event_id,
                "timestamp": received_at,
            }
            targets: list[tuple[dict[str, Any], str]] = [
                (urls[0], "runtime_coherent_endpoint")
            ]
            if qualified_view:
                targets.extend(
                    [
                        (views[0], "runtime_coherent_view"),
                        (url_view_edges[0], "runtime_coherent_view"),
                    ]
                )
            if len(endpoint_edges) == 1:
                targets.append((endpoint_edges[0], "runtime_coherent_resolution"))
            for target, reason in targets:
                observed_by_target.setdefault(target["id"], []).append(
                    {**endpoint_evidence, "reason": reason}
                )
        targets_by_id = {
            target["id"]: target for target in [*raw["nodes"], *raw["edges"]]
        }
        for target_id, observed in observed_by_target.items():
            target = targets_by_id[target_id]
            available = 32 - len(target["evidence"])
            if available <= 0:
                continue
            selected = sorted(
                {tuple(item.items()): item for item in observed}.values(),
                key=lambda value: tuple(value.items()),
            )[:available]
            if selected:
                target["evidence"] = sorted(
                    [*target["evidence"], *selected],
                    key=lambda value: tuple(value.items()),
                )
                target["confidence"] = max(target["confidence"], 0.95)
        raw["diagnostics"] = sorted([*raw["diagnostics"], *diagnostics], key=lambda item: item["id"])
        return GraphSnapshot.from_dict(raw)


def _frontend_analyzer_timeout() -> float:
    raw = os.environ.get("KG_DEBUGGER_ANALYZER_TIMEOUT", "")
    try:
        value = float(raw)
    except ValueError:
        return 120.0
    return value if math.isfinite(value) and 0 < value <= 120 else 120.0


def default_config(workspace_root: str | Path) -> DebuggerConfig:
    return DebuggerConfig.from_dict(workspace_root, {"project": Path(workspace_root).name, "repoRoots": ["."]})
