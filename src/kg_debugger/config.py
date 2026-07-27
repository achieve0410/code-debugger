from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from .graph.contracts import is_secret_value
from .http import EndpointConfig
from .security import (
    SecurityError,
    reject_sensitive_config,
    resolve_analysis_root,
    resolve_repo_path,
)

_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")


@dataclass(frozen=True)
class RepositoryRoot:
    """A named, process-local repository root with a safe public display value."""

    namespace: str
    resolved_root: Path
    display_root: str

    def to_dict(self) -> dict[str, str]:
        return {"namespace": self.namespace, "displayRoot": self.display_root}


@dataclass(frozen=True)
class DebuggerConfig:
    project: str
    repositories: tuple[RepositoryRoot, ...]
    workspace_root: Path
    store_path: Path
    repository_set_id: str
    endpoint: EndpointConfig = field(default_factory=EndpointConfig)
    runtime_enabled: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = 8443

    @property
    def repo_roots(self) -> tuple[Path, ...]:
        """Compatibility root view; callers must migrate to ``repositories``."""
        return tuple(repository.resolved_root for repository in self.repositories)

    @property
    def repository_manifest(self) -> list[dict[str, str]]:
        return [{"namespace": repository.namespace} for repository in self.repositories]

    @property
    def repoRoots(self) -> tuple[str, ...]:
        """One-release public compatibility alias; never an identity input."""
        return tuple(repository.display_root for repository in self.repositories)

    def to_dict(self) -> dict[str, object]:
        """Return the public configuration contract without local paths or endpoint policy."""
        return {
            "project": self.project,
            "runtimeEnabled": self.runtime_enabled,
            "schemaVersion": 2,
            "repositorySetId": self.repository_set_id,
            "repositories": [repository.to_dict() for repository in self.repositories],
            "repoRoots": list(self.repoRoots),
            "compatibilityWarnings": ["repoRoots_deprecated_v2"],
        }

    @classmethod
    def from_dict(cls, workspace_root: str | Path, data: dict[str, object]) -> DebuggerConfig:
        reject_sensitive_config(data)
        workspace = Path(workspace_root).resolve(strict=True)
        if not workspace.is_dir():
            raise SecurityError("workspace root must be a directory")

        project = _validate_project(data.get("project", workspace.name))
        repositories = _parse_repositories(workspace, data)
        store = resolve_repo_path(workspace, str(data.get("storePath", ".kg-debugger/graph.sqlite3")))
        endpoint = _parse_endpoint_config(data.get("endpoint"))
        runtime = bool(data.get("runtimeEnabled", False))
        host = str(data.get("bindHost", "127.0.0.1"))
        if host != "127.0.0.1":
            raise SecurityError("debugger API must bind to 127.0.0.1")
        raw_port = data.get("bindPort", 8443)
        if isinstance(raw_port, bool) or not isinstance(raw_port, int) or not 0 <= raw_port <= 65535:
            raise SecurityError("bindPort must be an integer from 0 to 65535")
        return cls(
            project=project,
            repositories=repositories,
            workspace_root=workspace,
            store_path=store,
            repository_set_id=_repository_set_id(project, repositories, endpoint),
            endpoint=endpoint,
            runtime_enabled=runtime,
            bind_host=host,
            bind_port=raw_port,
        )


def _validate_project(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or unicodedata.normalize("NFC", value) != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or is_secret_value(value)
    ):
        raise SecurityError("invalid project")
    return value

def _parse_endpoint_config(value: object) -> EndpointConfig:
    if not isinstance(value, dict):
        return EndpointConfig()

    raw_origins = value.get("firstPartyOrigins", {})
    if not isinstance(raw_origins, dict):
        raise SecurityError("endpoint firstPartyOrigins must be an object")
    origins: dict[str, str] = {}
    for origin, replacement in raw_origins.items():
        if not isinstance(origin, str) or not isinstance(replacement, str):
            raise SecurityError("endpoint firstPartyOrigins must map strings to strings")
        origins[origin] = replacement

    return EndpointConfig(
        first_party_origins=origins,
        base_paths=_parse_string_sequence(value.get("basePaths", ()), "basePaths"),
        trusted_proxy_prefixes=_parse_string_sequence(
            value.get("trustedProxyPrefixes", ()), "trustedProxyPrefixes"
        ),
    )


def _parse_string_sequence(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise SecurityError(f"endpoint {field_name} must be a list of strings")
    return tuple(item for item in value if isinstance(item, str))


def _parse_repositories(workspace: Path, data: dict[str, object]) -> tuple[RepositoryRoot, ...]:
    named = data.get("repositories")
    roots = data.get("repoRoots", ["."])
    if named is not None and "repoRoots" in data:
        raise SecurityError("configure repositories or repoRoots, not both")
    raw_roots = named if named is not None else roots
    if not isinstance(raw_roots, (list, tuple)) or not raw_roots:
        raise SecurityError("at least one repository root is required")

    parsed: list[tuple[str | None, str]] = []
    for item in raw_roots:
        if isinstance(item, dict):
            if set(item) != {"namespace", "path"} or not all(isinstance(item[key], str) for key in item):
                raise SecurityError("repository descriptors require string namespace and path")
            parsed.append((item["namespace"], item["path"]))
        elif isinstance(item, str):
            namespace, separator, path = item.partition("=")
            parsed.append((namespace if separator else None, path if separator else item))
        else:
            raise SecurityError("repository root must be a path or descriptor")

    bare_names = [Path(path).name for namespace, path in parsed if namespace is None]
    folded_bare_names = [name.casefold() for name in bare_names]
    if len(folded_bare_names) != len(set(folded_bare_names)):
        raise SecurityError("bare repository roots require unique case-insensitive basenames; use NAME=PATH")

    descriptors: list[RepositoryRoot] = []
    namespaces: set[str] = set()
    resolved_roots: set[Path] = set()
    for namespace_value, path in parsed:
        resolved = resolve_analysis_root(workspace, path)
        name = _validate_namespace(namespace_value if namespace_value is not None else resolved.name)
        folded_name = name.casefold()
        if folded_name in namespaces:
            raise SecurityError("repository namespaces must be unique ignoring case")
        if resolved in resolved_roots:
            raise SecurityError("repository roots must resolve to unique directories")
        namespaces.add(folded_name)
        resolved_roots.add(resolved)
        descriptors.append(RepositoryRoot(name, resolved, _display_root(workspace, resolved, name)))
    return tuple(sorted(descriptors, key=lambda repository: repository.namespace))


def _validate_namespace(namespace: str) -> str:
    normalized = unicodedata.normalize("NFC", namespace)
    if namespace != normalized or not _NAMESPACE_RE.fullmatch(normalized) or normalized in {".", ".."}:
        raise SecurityError("repository namespace must be NFC ASCII lowercase and match [a-z][a-z0-9._-]{0,63}")
    return normalized


def _display_root(workspace: Path, resolved: Path, namespace: str) -> str:
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return f"external:{namespace}"
    return relative.as_posix() or "."


def _endpoint_payload(endpoint: EndpointConfig) -> dict[str, object]:
    return {
        "firstPartyOrigins": dict(sorted(endpoint.first_party_origins.items())),
        "basePaths": list(endpoint.base_paths),
        "trustedProxyPrefixes": list(endpoint.trusted_proxy_prefixes),
    }


def _repository_set_id(project: str, repositories: tuple[RepositoryRoot, ...], endpoint: EndpointConfig) -> str:
    payload = {
        "schemaVersion": 2,
        "project": project,
        "repositories": [
            {"namespace": repository.namespace, "resolvedRoot": repository.resolved_root.as_posix()}
            for repository in repositories
        ],
        "endpoint": _endpoint_payload(endpoint),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return sha256(encoded).hexdigest()
