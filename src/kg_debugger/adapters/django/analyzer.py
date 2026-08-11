from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...graph.contracts import DIAGNOSTIC_CATALOG, format_external_authority
from ...graph.identity import diagnostic_identity, node_identity, route_identity
from ...security import resolve_repo_path

ADAPTER = "django-ast"
VERSION = "2"
MAX_SOURCE_FILE_BYTES = 1024 * 1024
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
GENERIC_METHODS = {
    "ListAPIView": {"GET"},
    "CreateAPIView": {"POST"},
    "ListCreateAPIView": {"GET", "POST"},
    "RetrieveAPIView": {"GET"},
    "RetrieveUpdateAPIView": {"GET", "PUT", "PATCH"},
    "RetrieveDestroyAPIView": {"GET", "DELETE"},
    "RetrieveUpdateDestroyAPIView": {"GET", "PUT", "PATCH", "DELETE"},
    "UpdateAPIView": {"PUT", "PATCH"},
    "DestroyAPIView": {"DELETE"},
    "ListView": {"GET"},
    "DetailView": {"GET"},
    "TemplateView": {"GET"},
    "RedirectView": {"GET"},
    "FormView": {"GET", "POST"},
    "CreateView": {"GET", "POST"},
    "UpdateView": {"GET", "POST"},
    "DeleteView": {"GET", "POST"},
}
VIEWSET_METHODS = {
    "ModelViewSet": {
        "collection": {"GET", "POST"},
        "detail": {"GET", "PUT", "PATCH", "DELETE"},
    },
    "ReadOnlyModelViewSet": {"collection": {"GET"}, "detail": {"GET"}},
}
ACTION_METHODS = {
    "list": ("collection", "GET"),
    "create": ("collection", "POST"),
    "retrieve": ("detail", "GET"),
    "update": ("detail", "PUT"),
    "partial_update": ("detail", "PATCH"),
    "destroy": ("detail", "DELETE"),
}
CODING_COOKIE = re.compile(r"coding[:=]\s*[-\w.]+")


@dataclass
class ModuleInfo:
    path: Path
    relpath: str
    tree: ast.Module
    names: set[str] = field(default_factory=set)
    imports: dict[str, str] = field(default_factory=dict)
    qualified: str | None = None
    module_names: set[str] = field(default_factory=set)
    literals: dict[str, str] = field(default_factory=dict)


def analyze_django(
    repo_root: str | Path,
    project: str,
    endpoint_config: Any = None,
    *,
    repository_set_id: str | None = None,
    repositories: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Produce a strict static fragment without importing project code or settings."""
    del endpoint_config
    configured_root = Path(repo_root)
    if configured_root.is_symlink():
        raise ValueError("analysis root must not be a symlink")
    root = configured_root.resolve(strict=True)
    repository = project
    manifest = sorted(
        repositories or [{"namespace": repository}], key=lambda item: item["namespace"]
    )
    set_id = repository_set_id or sha256(repository.encode("utf-8")).hexdigest()
    modules, parse_failures, source_failures = _load_modules(root)
    _prove_modules(modules, _candidate_roots(root))
    by_qualified: dict[str, list[ModuleInfo]] = {}
    for info in modules.values():
        for name in info.module_names:
            by_qualified.setdefault(name, []).append(info)
    local_module_roots = {name.partition(".")[0] for name in by_qualified}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    known: set[str] = set()
    url_endpoints: set[tuple[str, str]] = set()
    declared_url_patterns: set[str] = set()

    def add_node(raw: dict[str, Any]) -> None:
        if raw["key"] not in known:
            known.add(raw["key"])
            nodes.append(raw)

    def add_diagnostic(code: str, info: ModuleInfo) -> None:
        spec = DIAGNOSTIC_CATALOG[code]
        source = _source(repository, info, None)
        fields = tuple(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for value in (
                code,
                spec.severity,
                spec.message,
                repository,
                source,
                None,
                None,
                None,
                None,
            )
        )
        diagnostics.append(
            {
                "id": diagnostic_identity(*fields),
                "code": code,
                "severity": spec.severity,
                "message": spec.message,
                "repository": repository,
                "source": source,
            }
        )

    for info in parse_failures:
        add_diagnostic("unsupported_syntax", info)
    for info in source_failures:
        add_diagnostic("source_read_failed", info)

    def unresolved(info: ModuleInfo, owner: str, reason: str) -> str:
        key = f"unresolved:{info.relpath}:{owner}:{reason}"
        add_node(
            {
                "key": key,
                "kind": "unresolved_target",
                "identity": key,
                "label": "Unresolved",
                "source": _source(repository, info, None),
                "confidence": 0.3,
                "evidenceKind": "unresolved",
                "reason": reason,
                "metadata": {"reasonCode": reason},
            }
        )
        return key

    model_keys: dict[tuple[str, str], str] = {}
    for info in sorted(modules.values(), key=lambda item: item.relpath):
        for cls in _classes(info.tree):
            if _is_model(cls):
                symbol = _qualname(cls)
                key = f"model:{info.relpath}:{symbol}"
                qualified = _python_name(info, symbol)
                add_node(
                    {
                        "key": key,
                        "kind": "model",
                        "identity": f"model:{_identity_symbol(info, cls)}",
                        "label": cls.name,
                        "source": _source(repository, info, cls),
                        "confidence": 0.9,
                        "reason": "ast_symbol_declaration",
                        "metadata": {
                            **({"pythonQualifiedName": qualified} if qualified else {})
                        },
                    }
                )
                model_keys[(info.relpath, cls.name)] = key

    function_keys: set[tuple[str, str]] = set()

    def function_node(
        info: ModuleInfo, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str:
        symbol = _qualname(node)
        key = f"function:{info.relpath}:{symbol}"
        if (info.relpath, symbol) not in function_keys:
            function_keys.add((info.relpath, symbol))
            qualified = _python_name(info, symbol)
            add_node(
                {
                    "key": key,
                    "kind": "function",
                    "identity": f"function:{_identity_symbol(info, node)}",
                    "label": node.name,
                    "source": _source(repository, info, node),
                    "confidence": 0.85,
                    "reason": "ast_symbol_declaration",
                    "metadata": {
                        **({"pythonQualifiedName": qualified} if qualified else {})
                    },
                }
            )
        return key

    view_keys: set[tuple[str, str]] = set()

    def view_node(info: ModuleInfo, node: ast.AST, symbol: str) -> str:
        key = f"view:{info.relpath}:{symbol}"
        if (info.relpath, symbol) not in view_keys:
            view_keys.add((info.relpath, symbol))
            qualified = _python_name(info, symbol)
            add_node(
                {
                    "key": key,
                    "kind": "django_view",
                    "identity": f"view:{_identity_symbol(info, node)}",
                    "label": symbol.rsplit(".", 1)[-1],
                    "source": _source(repository, info, node),
                    "confidence": 0.9,
                    "reason": "django_view_binding",
                    "metadata": {
                        **({"pythonQualifiedName": qualified} if qualified else {})
                    },
                }
            )
        return key

    def resolve_reference(
        info: ModuleInfo, expression: ast.AST
    ) -> tuple[ModuleInfo, ast.AST, str] | None:
        dotted = _dotted(expression)
        if not dotted:
            return None
        first, _, rest = dotted.partition(".")
        target = info.imports.get(first)
        module_name, _, imported = (target or "").partition(":")
        name = imported or (rest if target else dotted)
        candidates = by_qualified.get(module_name, []) if target else [info]
        if target and imported:
            candidates = by_qualified.get(f"{module_name}.{imported}", candidates)
            if candidates and candidates is not by_qualified.get(module_name, []):
                name = rest
        if len(candidates) > 1:
            add_diagnostic("python_import_module_ambiguous", info)
            return None
        if not candidates and target:
            if module_name.partition(".")[0] in local_module_roots:
                add_diagnostic("python_import_module_unresolved", info)
            return None
        if len(candidates) != 1:
            return None
        node = _symbol(candidates[0].tree, name)
        return (candidates[0], node, name) if node else None

    visited: set[tuple[str, str]] = set()

    def visit_owner(info: ModuleInfo, body: ast.AST, owner_key: str) -> None:
        owner = (info.relpath, _qualname(body))
        if owner in visited:
            return
        visited.add(owner)
        ordinal = 0
        local_helpers = {
            item.name: item
            for item in getattr(body, "body", [])
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for call in _calls_in_source_order(body):
            model_target = _orm_model_reference(info, call, by_qualified, body)
            if model_target:
                ordinal += 1
                operation = _orm_operation(call) or "other"
                key = f"query:{info.relpath}:{_owner_qualname(body)}#{ordinal}"
                model_name = _python_name(model_target[0], model_target[1])
                add_node(
                    {
                        "key": key,
                        "kind": "query_boundary",
                        "identity": f"query:{info.relpath}:owner:{_owner_qualname(body)}:query:{ordinal}",
                        "label": operation,
                        "source": _source(repository, info, call),
                        "confidence": 0.8,
                        "reason": "django_query_call",
                        "metadata": {
                            "operation": operation,
                            **(
                                {"modelQualifiedName": model_name} if model_name else {}
                            ),
                        },
                    }
                )
                edges.append(
                    {
                        "source": owner_key,
                        "target": key,
                        "kind": "accesses",
                        "confidence": 0.8,
                        "reason": "django_query_call",
                    }
                )
                target_key = model_keys.get(
                    (model_target[0].relpath, model_target[1].rsplit(".", 1)[-1])
                )
                edges.append(
                    {
                        "source": key,
                        "target": target_key
                        or unresolved(info, key, "referenced_target_missing"),
                        "kind": "accesses",
                        "confidence": 0.8 if target_key else 0.3,
                        "evidenceKind": "unresolved" if not target_key else "inferred",
                        "reason": "referenced_target_missing"
                        if not target_key
                        else "django_query_call",
                    }
                )
            elif isinstance(call.func, (ast.Name, ast.Attribute)):
                target = (
                    (
                        info,
                        local_helpers[call.func.id],
                        _qualname(local_helpers[call.func.id]),
                    )
                    if isinstance(call.func, ast.Name) and call.func.id in local_helpers
                    else resolve_reference(info, call.func)
                )
                if target and isinstance(
                    target[1], (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    target_key = function_node(target[0], target[1])
                    if target_key != owner_key:
                        edges.append(
                            {
                                "source": owner_key,
                                "target": target_key,
                                "kind": "invokes",
                                "confidence": 0.8,
                                "reason": "ast_call",
                            }
                        )
                    visit_owner(target[0], target[1], target_key)
        boundary = _external_boundary(info, body)
        if boundary is False:
            edges.append(
                {
                    "source": owner_key,
                    "target": unresolved(info, owner_key, "dynamic_target_unproven"),
                    "kind": "calls",
                    "confidence": 0.3,
                    "evidenceKind": "unresolved",
                    "reason": "dynamic_target_unproven",
                }
            )
        elif isinstance(boundary, tuple):
            method, scheme, host, port, path_present, query_count, sensitive = boundary
            key = f"external:{info.relpath}:{_qualname(body)}:{scheme}:{host}:{port or ''}"
            metadata = {
                "method": method,
                "scheme": scheme,
                "host": host,
                "pathPresent": path_present,
                "queryFieldCount": query_count,
                "hasSensitiveQuery": sensitive,
                "boundaryOnly": True,
                **({"port": port} if port is not None else {}),
            }
            add_node(
                {
                    "key": key,
                    "kind": "external_service",
                    "identity": key,
                    "label": f"{method} {scheme}://{format_external_authority(host, port)}",
                    "source": _source(repository, info, body),
                    "confidence": 0.8,
                    "reason": "external_boundary",
                    "metadata": metadata,
                }
            )
            edges.append(
                {
                    "source": owner_key,
                    "target": key,
                    "kind": "calls",
                    "confidence": 0.8,
                    "reason": "external_boundary",
                }
            )

    def add_url(
        info: ModuleInfo,
        call: ast.Call,
        prefix: str,
        index: tuple[int, ...],
        view: ast.AST | None = None,
        forced_methods: set[str] | None = None,
    ) -> None:
        route = _literal_string(call.args[0]) if call.args else None
        view = (
            view if view is not None else (call.args[1] if len(call.args) > 1 else None)
        )
        parsed = _route(prefix, route) if route is not None else None
        if parsed is None or view is None:
            add_diagnostic("unresolved_django_url", info)
            return
        declared, normalized, converters, shadow_key = parsed
        if shadow_key in declared_url_patterns:
            return
        declared_url_patterns.add(shadow_key)
        target = resolve_reference(info, _view_expression(view))
        methods = forced_methods or (_view_methods(target[1]) if target else set())
        if not methods:
            methods = {"GET"}
        for method in sorted(methods):
            endpoint_key = (method, normalized)
            if endpoint_key in url_endpoints:
                continue
            url_endpoints.add(endpoint_key)
            key = f"url:{info.relpath}:{'.'.join(map(str, index))}:{method}"
            identity = (
                f"url:{info.relpath}:urlpatterns:{'.'.join(map(str, index))}:{method}"
            )
            endpoint = f"{method} {normalized}"
            add_node(
                {
                    "key": key,
                    "kind": "django_url_pattern",
                    "identity": identity,
                    "label": "URL pattern",
                    "source": _source(repository, info, call),
                    "confidence": 0.9,
                    "reason": "django_url_declaration",
                    "metadata": {
                        "declaredPath": declared,
                        "normalizedPath": normalized,
                        "endpointId": endpoint,
                        "converters": converters,
                    },
                }
            )
            route_node_id = node_identity(
                repository, info.relpath, "django_url_pattern", identity
            )
            routes.append(
                {
                    "id": route_identity(
                        repository, "django", normalized, route_node_id
                    ),
                    "key": key,
                    "path": normalized,
                    "repository": repository,
                    "framework": "django",
                }
            )
            if not target or not isinstance(
                target[1], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                edges.append(
                    {
                        "source": key,
                        "target": unresolved(info, key, "dynamic_target_unproven"),
                        "kind": "resolves_to",
                        "confidence": 0.3,
                        "evidenceKind": "unresolved",
                        "reason": "dynamic_target_unproven",
                        "metadata": {"resolutionTier": "unbounded"},
                    }
                )
                continue
            target_info, target_node, _ = target
            symbol = _qualname(target_node)
            target_key = view_node(target_info, target_node, symbol)
            edges.append(
                {
                    "source": key,
                    "target": target_key,
                    "kind": "resolves_to",
                    "confidence": 0.9,
                    "reason": "django_view_binding",
                    "metadata": {"resolutionTier": "declared_path"},
                }
            )
            if isinstance(target_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit_owner(target_info, target_node, target_key)
            else:
                visit_owner(target_info, target_node, target_key)

    for url_info, call, prefix, index in _url_calls(modules, by_qualified):
        router = _router_include(url_info, call)
        if router:
            for registered_prefix, view, ordinal in _router_registrations(
                url_info, router
            ):
                router_call = ast.Call(
                    func=ast.Name(id="path"),
                    args=[ast.Constant(value=""), view],
                    keywords=[],
                )
                route_prefix = _literal_string(call.args[0]) if call.args else ""
                router_prefix = _join(prefix, route_prefix or "")
                collection = _join(router_prefix, registered_prefix).rstrip("/") + "/"
                add_url(
                    url_info,
                    router_call,
                    collection,
                    index + (ordinal,),
                    view,
                    _view_methods_from_expression(
                        url_info, view, "collection", resolve_reference
                    ),
                )
                detail = collection + "<str:pk>/"
                add_url(
                    url_info,
                    router_call,
                    detail,
                    index + (ordinal, 1),
                    view,
                    _view_methods_from_expression(
                        url_info, view, "detail", resolve_reference
                    ),
                )
                target = resolve_reference(url_info, view)
                if target and isinstance(target[1], ast.ClassDef):
                    for action, methods, detail_action in _viewset_actions(target[1]):
                        action_prefix = detail if detail_action else collection
                        add_url(
                            url_info,
                            router_call,
                            action_prefix + action.rstrip("/") + "/",
                            index + (ordinal, 2),
                            view,
                            methods,
                        )
            continue
        add_url(url_info, call, prefix, index)

    edges = list(
        {
            json.dumps(edge, sort_keys=True, separators=(",", ":")): edge
            for edge in edges
        }.values()
    )
    routes = list({route["id"]: route for route in routes}.values())
    routes_by_path: dict[tuple[str, str, str], dict[str, Any]] = {}
    for route in routes:
        routes_by_path.setdefault(
            (route["repository"], route["framework"], route["path"]),
            route,
        )
    routes = list(routes_by_path.values())
    return {
        "adapter": ADAPTER,
        "adapterVersion": VERSION,
        "repository": repository,
        "repositorySetId": set_id,
        "repositories": manifest,
        "nodes": nodes,
        "edges": edges,
        "routes": sorted(
            routes,
            key=lambda item: (
                item["path"],
                item["repository"],
                item["framework"],
                item["id"],
            ),
        ),
        "diagnostics": sorted(
            {item["id"]: item for item in diagnostics}.values(),
            key=lambda item: item["id"],
        ),
    }


def _load_modules(
    root: Path,
) -> tuple[dict[str, ModuleInfo], list[ModuleInfo], list[ModuleInfo]]:
    modules: dict[str, ModuleInfo] = {}
    parse_failures: list[ModuleInfo] = []
    source_failures: list[ModuleInfo] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if name
            not in {
                ".agents",
                ".aws",
                ".config",
                ".git",
                ".ssh",
                ".venv",
                "venv",
                "node_modules",
                "__pycache__",
                "build",
                "dist",
                "site-packages",
            }
            and not (current / name).is_symlink()
        )
        for name in sorted(files):
            path = current / name
            if not name.endswith(".py") or path.is_symlink():
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            try:
                safe = resolve_repo_path(root, path)
                if safe.stat().st_size > MAX_SOURCE_FILE_BYTES:
                    raise OSError("source file exceeds analysis limit")
                raw = safe.read_bytes()
            except (OSError, UnicodeError, ValueError):
                source_failures.append(
                    ModuleInfo(path, rel, ast.Module(body=[], type_ignores=[]))
                )
                continue
            try:
                tree = ast.parse(raw, filename=rel)
            except (SyntaxError, ValueError, RecursionError):
                try:
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.splitlines(keepends=True)
                    lines[:2] = [
                        CODING_COOKIE.sub("coding-removed", line) for line in lines[:2]
                    ]
                    tree = ast.parse("".join(lines), filename=rel)
                except (SyntaxError, ValueError, RecursionError):
                    parse_failures.append(
                        ModuleInfo(safe, rel, ast.Module(body=[], type_ignores=[]))
                    )
                    continue
            _attach_parents(tree)
            info = ModuleInfo(safe, rel, tree)
            info.names = {
                node.name
                for node in tree.body
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            }
            info.literals = {
                target.id: value
                for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target, value in _assignment_literals(node)
            }
            modules[rel] = info
    return modules, parse_failures, source_failures


def _candidate_roots(root: Path) -> list[Path]:
    candidates = {root}
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if name
            not in {
                ".agents",
                ".aws",
                ".config",
                ".git",
                ".ssh",
                ".venv",
                "venv",
                "node_modules",
                "__pycache__",
                "build",
                "dist",
                "site-packages",
            }
            and not (current / name).is_symlink()
        )
        if (
            "manage.py" in files
            and (current / "manage.py").is_file()
            and not (current / "manage.py").is_symlink()
        ):
            candidates.add(current)
    return sorted(candidates)


def _prove_modules(modules: dict[str, ModuleInfo], roots: list[Path]) -> None:
    for info in modules.values():
        names: set[str] = set()
        proof_depth = -1
        for root in roots:
            try:
                parts = list(info.path.relative_to(root).parts)
            except ValueError:
                continue
            if not parts or parts[-1] == "manage.py":
                continue
            stem, package = Path(parts[-1]).stem, parts[:-1]
            if not stem.isidentifier() or any(
                not item.isidentifier() for item in package
            ):
                continue
            depth = len(root.parts)
            if depth < proof_depth:
                continue
            if depth > proof_depth:
                names.clear()
                proof_depth = depth
            names.add(".".join(package if stem == "__init__" else [*package, stem]))
        info.qualified = next(iter(names)) if len(names) == 1 else None
        info.module_names = names
        for statement in info.tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                _record_import(info, statement)


def _record_import(info: ModuleInfo, node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            info.imports[alias.asname or alias.name.split(".")[0]] = alias.name
        return
    base = node.module or ""
    if node.level:
        package = (
            info.qualified
            if info.path.name == "__init__.py"
            else (info.qualified or "").rpartition(".")[0]
        )
        parts = package.split(".") if package else []
        if node.level > len(parts):
            return
        base = ".".join([*parts[: len(parts) - node.level + 1], base]).strip(".")
    for alias in node.names:
        if alias.name != "*":
            info.imports[alias.asname or alias.name] = (
                f"{base}:{alias.name}" if base else alias.name
            )


def _url_modules(
    modules: dict[str, ModuleInfo], by_qualified: dict[str, list[ModuleInfo]]
) -> list[ModuleInfo]:
    configured_roots = [
        _literal_string(statement.value)
        for info in modules.values()
        if info.relpath.endswith("settings.py")
        or "/settings/" in f"/{info.relpath}"
        for statement in info.tree.body
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
        and statement.value is not None
        and any(
            isinstance(target, ast.Name) and target.id == "ROOT_URLCONF"
            for target in _assignment_targets(statement)
        )
    ]
    if (
        configured_roots
        and all(configured_roots)
        and len(set(configured_roots)) == 1
    ):
        candidates = by_qualified.get(configured_roots[0] or "", [])
        if len(candidates) == 1:
            return candidates
    if configured_roots:
        return []
    included = {
        target[0].relpath
        for info in modules.values()
        for call, _ in _urlpatterns_calls(info.tree)
        if (target := _include_target(info, call, by_qualified))
    }
    roots = [
        info
        for info in modules.values()
        if info.relpath.endswith("urls.py") and info.relpath not in included
    ]
    return sorted(
        roots
        or [info for info in modules.values() if info.relpath.endswith("urls.py")],
        key=lambda item: item.relpath,
    )


def _url_calls(
    modules: dict[str, ModuleInfo], by_qualified: dict[str, list[ModuleInfo]]
) -> list[tuple[ModuleInfo, ast.Call, str, tuple[int, ...]]]:
    return [
        item
        for root in _url_modules(modules, by_qualified)
        for item in _url_calls_from(root, by_qualified)
    ]


def _url_calls_from(
    info: ModuleInfo,
    by_qualified: dict[str, list[ModuleInfo]],
    prefix: str = "",
    seen: set[tuple[str, str]] | None = None,
    pattern_name: str = "urlpatterns",
    identity_prefix: tuple[int, ...] = (),
) -> list[tuple[ModuleInfo, ast.Call, str, tuple[int, ...]]]:
    seen = set() if seen is None else set(seen)
    current = (info.relpath, pattern_name)
    if current in seen:
        return []
    seen.add(current)
    result = []
    for call, index in _urlpatterns_calls(info.tree, pattern_name):
        child_target = _include_target(info, call, by_qualified)
        route = _literal_string(call.args[0]) if call.args else None
        child_identity_prefix = (
            (*identity_prefix, *index)
            if child_target and child_target[1] != "urlpatterns"
            else identity_prefix
        )
        result.extend(
            _url_calls_from(
                child_target[0],
                by_qualified,
                _join(prefix, route),
                seen,
                child_target[1],
                child_identity_prefix,
            )
            if child_target and route is not None
            else [(info, call, prefix, (*identity_prefix, *index))]
        )
    return result


def _urlpatterns_calls(
    tree: ast.Module, pattern_name: str = "urlpatterns"
) -> list[tuple[ast.Call, tuple[int, ...]]]:
    result: list[tuple[ast.Call, tuple[int, ...]]] = []
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)) and any(
            isinstance(target, ast.Name) and target.id == pattern_name
            for target in _assignment_targets(statement)
        ):
            result.extend(_url_calls_in_list(statement.value))
            continue
        if (
            isinstance(statement, ast.AugAssign)
            and isinstance(statement.op, ast.Add)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == pattern_name
            and isinstance(statement.value, ast.Attribute)
            and statement.value.attr == "urls"
            and isinstance(statement.value.value, ast.Name)
        ):
            include_call = ast.Call(
                func=ast.Name(id="include"),
                args=[statement.value],
                keywords=[],
            )
            route_call = ast.Call(
                func=ast.Name(id="path"),
                args=[ast.Constant(value=""), include_call],
                keywords=[],
            )
            result.append((ast.copy_location(route_call, statement), (len(result),)))
    return result


def _url_calls_in_list(
    node: ast.AST | None, index: tuple[int, ...] = ()
) -> list[tuple[ast.Call, tuple[int, ...]]]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    result = []
    for ordinal, item in enumerate(node.elts):
        current = (*index, ordinal)
        if isinstance(item, (ast.List, ast.Tuple)):
            result.extend(_url_calls_in_list(item, current))
        elif isinstance(item, ast.Call) and _dotted(item.func) in {
            "path",
            "re_path",
            "url",
        }:
            result.append((item, current))
    return result


def _router_include(info: ModuleInfo, call: ast.Call) -> str | None:
    view = call.args[1] if len(call.args) > 1 else None
    if (
        isinstance(view, ast.Call)
        and _dotted(view.func) == "include"
        and view.args
        and isinstance(view.args[0], ast.Attribute)
        and view.args[0].attr == "urls"
        and isinstance(view.args[0].value, ast.Name)
    ):
        return view.args[0].value.id
    return None


def _router_registrations(
    info: ModuleInfo, router: str
) -> list[tuple[str, ast.expr, int]]:
    result: list[tuple[str, ast.expr, int]] = []
    for node in ast.walk(info.tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "register"
            or not isinstance(node.func.value, ast.Name)
            or node.func.value.id != router
        ):
            continue
        prefix = (
            node.args[0]
            if node.args
            else next(
                (item.value for item in node.keywords if item.arg == "prefix"),
                None,
            )
        )
        view = (
            node.args[1]
            if len(node.args) > 1
            else next(
                (
                    item.value
                    for item in node.keywords
                    if item.arg in {"viewset", "view"}
                ),
                None,
            )
        )
        if (
            isinstance(prefix, ast.Constant)
            and isinstance(prefix.value, str)
            and view is not None
        ):
            result.append((prefix.value, view, len(result)))
    return result


def _view_methods_from_expression(
    info: ModuleInfo, expression: ast.AST, kind: str, resolver: Any
) -> set[str]:
    target = resolver(info, expression)
    return _view_methods(target[1], kind) if target else set()


def _view_methods(node: ast.AST | None, router_kind: str | None = None) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        methods: set[str] = set()
        for decorator in node.decorator_list:
            name = (
                _dotted(decorator.func)
                if isinstance(decorator, ast.Call)
                else _dotted(decorator)
            )
            short = (name or "").split(".")[-1]
            if short in {"api_view", "require_http_methods"} and isinstance(
                decorator, ast.Call
            ):
                methods.update(
                    value.upper()
                    for arg in decorator.args
                    for value in _string_items(arg)
                )
            elif short == "require_GET":
                methods.add("GET")
            elif short == "require_POST":
                methods.add("POST")
            elif short == "require_safe":
                methods.update({"GET", "HEAD"})
        return methods
    if not isinstance(node, ast.ClassDef):
        return set()
    bases = [
        (name or "").split(".")[-1] for base in node.bases if (name := _dotted(base))
    ]
    if router_kind:
        for base in bases:
            if base in VIEWSET_METHODS:
                return VIEWSET_METHODS[base][router_kind]
        actions = {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        return {
            method
            for action, (kind, method) in ACTION_METHODS.items()
            if action in actions and kind == router_kind
        }
    for base in bases:
        if base in GENERIC_METHODS:
            return GENERIC_METHODS[base]
    return {
        child.name.upper()
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        and child.name in HTTP_METHODS
    }


def _viewset_actions(cls: ast.ClassDef) -> list[tuple[str, set[str], bool]]:
    result: list[tuple[str, set[str], bool]] = []
    for child in cls.body:
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in child.decorator_list:
            if (
                not isinstance(decorator, ast.Call)
                or ("." + (_dotted(decorator.func) or "")).endswith(".action") is False
            ):
                continue
            detail = next(
                (
                    item.value
                    for item in decorator.keywords
                    if item.arg == "detail"
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, bool)
                ),
                None,
            )
            methods = next(
                (
                    set(value.upper() for value in _string_items(item.value))
                    for item in decorator.keywords
                    if item.arg == "methods"
                ),
                set(),
            )
            path = next(
                (
                    item.value.value
                    for item in decorator.keywords
                    if item.arg == "url_path"
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                ),
                child.name,
            )
            if isinstance(detail, bool) and methods:
                result.append((path, methods, detail))
    return result


def _is_viewset_action(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(item, ast.Call) and (_dotted(item.func) or "").endswith(".action")
        for item in node.decorator_list
    )


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[ast.AST]:
    return list(node.targets) if isinstance(node, ast.Assign) else [node.target]


def _assignment_literals(
    node: ast.Assign | ast.AnnAssign,
) -> list[tuple[ast.Name, str]]:
    return (
        [
            (target, node.value.value)
            for target in _assignment_targets(node)
            if isinstance(target, ast.Name)
        ]
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        else []
    )


def _string_items(node: ast.AST) -> list[str]:
    return (
        [
            item.value
            for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set))
        else []
    )


def _include_target(
    info: ModuleInfo, call: ast.Call, by_qualified: dict[str, list[ModuleInfo]]
) -> tuple[ModuleInfo, str] | None:
    if (
        len(call.args) < 2
        or not isinstance(call.args[1], ast.Call)
        or _dotted(call.args[1].func) != "include"
        or not call.args[1].args
    ):
        return None
    argument = call.args[1].args[0]
    pattern_name = "urlpatterns"
    if isinstance(argument, (ast.List, ast.Tuple)) and argument.elts:
        reference = _dotted(argument.elts[0])
        imported = info.imports.get(reference or "")
        if not imported:
            return None
        name, separator, pattern_name = imported.partition(":")
        if not separator or not pattern_name.isidentifier():
            return None
    else:
        target_name = _literal_string(argument) or _dotted(argument)
        if not target_name:
            return None
        name = info.imports.get(target_name, target_name).split(":", 1)[0]
    candidates = by_qualified.get(name, [])
    return (candidates[0], pattern_name) if len(candidates) == 1 else None


def _valid_route_fragment(route: str) -> bool:
    return not any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "^$()[]\\%?#"
        for char in route
    ) and all(part not in {".", ".."} for part in route.split("/"))


def _route(
    prefix: str, route: str | None
) -> tuple[str, str, list[dict[str, Any]], str] | None:
    if route is None or not _valid_route_fragment(route):
        return None
    declared = _join(prefix, route)
    if not _valid_route_fragment(declared) or (declared.startswith("/") and declared):
        return None
    raw_segments = declared.split("/")
    if any(not segment for segment in raw_segments[1:-1]):
        return None
    segments = [part for part in raw_segments if part]
    normalized = []
    shadow_segments = []
    converters = []
    for position, segment in enumerate(segments):
        if segment.startswith("<") and segment.endswith(">"):
            kind, separator, name = segment[1:-1].partition(":")
            if not separator:
                name, kind = kind, "str"
            if not name.isidentifier() or not kind.isidentifier() or ":" in name:
                return None
            converters.append(
                {
                    "name": name,
                    "kind": kind
                    if kind in {"int", "str", "slug", "uuid", "path"}
                    else "custom",
                    "segmentIndex": position,
                }
            )
            normalized.append(f"{{p{len(converters) - 1}}}")
            shadow_segments.append(f"<{kind}>")
        elif "<" in segment or ">" in segment:
            return None
        else:
            normalized.append(segment)
            shadow_segments.append(segment)
    suffix = "/" if declared.endswith("/") and segments else ""
    return (
        "/" + "/".join(segments) + suffix,
        "/" + "/".join(normalized) + suffix,
        converters,
        "/" + "/".join(shadow_segments) + suffix,
    )


def _classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _is_model(node: ast.ClassDef) -> bool:
    return any(
        _dotted(base) in {"models.Model", "Model"}
        or (_dotted(base) or "").endswith(".Model")
        for base in node.bases
    )


def _symbol(tree: ast.Module, name: str) -> ast.AST | None:
    current: ast.AST | None = None
    items: list[ast.stmt] = tree.body
    for part in name.split("."):
        current = next(
            (
                item
                for item in items
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                and item.name == part
            ),
            None,
        )
        if current is None:
            return None
        items = current.body if isinstance(current, ast.ClassDef) else []
    return current


def _orm_model_reference(
    info: ModuleInfo,
    call: ast.Call,
    by_qualified: dict[str, list[ModuleInfo]],
    owner: ast.AST,
) -> tuple[ModuleInfo, str] | None:
    if _orm_operation(call) is None or not isinstance(call.func, ast.Attribute):
        return _queryset_model_reference(info, owner, call, by_qualified)
    value: ast.AST = call.func.value
    while (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and _orm_operation(value)
    ):
        value = value.func.value
    if isinstance(value, ast.Attribute) and value.attr == "objects":
        target = _model_reference(info, value.value, by_qualified)
        if target:
            model_node = _symbol(target[0].tree, target[1])
            if isinstance(model_node, ast.ClassDef) and _is_model(model_node):
                return target
    return _queryset_model_reference(info, owner, call, by_qualified)


def _queryset_model_reference(
    info: ModuleInfo,
    owner: ast.AST,
    call: ast.Call,
    by_qualified: dict[str, list[ModuleInfo]],
) -> tuple[ModuleInfo, str] | None:
    if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    parent = getattr(owner, "parent", None)
    if not isinstance(parent, ast.ClassDef):
        return None
    cls = parent
    for statement in cls.body:
        if (
            isinstance(statement, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "queryset"
                for target in _assignment_targets(statement)
            )
            and isinstance(statement.value, ast.Call)
        ):
            fake = ast.Call(
                func=ast.Attribute(value=statement.value, attr="all"),
                args=[],
                keywords=[],
            )
            return _orm_model_reference(
                info, fake, by_qualified, ast.Module(body=[], type_ignores=[])
            )
    return None


def _model_reference(
    info: ModuleInfo, expression: ast.AST, by_qualified: dict[str, list[ModuleInfo]]
) -> tuple[ModuleInfo, str] | None:
    dotted = _dotted(expression)
    if not dotted:
        return None
    first, _, rest = dotted.partition(".")
    target = info.imports.get(first)
    module, _, imported = (target or "").partition(":")
    candidates, name = (
        (by_qualified.get(module, []), imported or rest) if target else ([info], dotted)
    )
    return (candidates[0], name) if len(candidates) == 1 and name else None


def _orm_operation(call: ast.Call) -> str | None:
    return (
        call.func.attr
        if isinstance(call.func, ast.Attribute)
        and call.func.attr
        in {"all", "filter", "get", "create", "update", "delete", "aggregate"}
        else None
    )


def _view_expression(view: ast.AST) -> ast.AST:
    return (
        view.func.value
        if isinstance(view, ast.Call)
        and isinstance(view.func, ast.Attribute)
        and view.func.attr == "as_view"
        else view
    )


def _calls_in_source_order(node: ast.AST) -> list[ast.Call]:
    return sorted(
        [item for item in ast.walk(node) if isinstance(item, ast.Call)],
        key=lambda item: (item.lineno, item.col_offset),
    )


def _dotted(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _literal_string(node: ast.AST) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _qualname(node: ast.AST) -> str:
    name = getattr(node, "name", None)
    if not isinstance(name, str):
        return "module"
    parents = [name]
    parent = _parent_node(node)
    while isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        parents.append(parent.name)
        parent = _parent_node(parent)
    return ".".join(reversed(parents))


def _owner_qualname(node: ast.AST) -> str:
    return _qualname(node)


def _identity_symbol(info: ModuleInfo, node: ast.AST) -> str:
    return f"{info.relpath}:lexical:{_qualname(node)}"


def _python_name(info: ModuleInfo, symbol: str) -> str | None:
    return f"{info.qualified}.{symbol}" if info.qualified else None


def _source(repository: str, info: ModuleInfo, node: ast.AST | None) -> dict[str, Any]:
    result: dict[str, Any] = {"repository": repository, "path": info.relpath}
    if node is not None:
        line = getattr(node, "lineno", None)
        if isinstance(line, int):
            result["line"] = line
            result["endLine"] = getattr(node, "end_lineno", line)
        name = _qualname(node)
        if name != "module":
            result["symbol"] = name
    return result


def _join(prefix: str, route: str) -> str:
    return route if not prefix else prefix.rstrip("/") + "/" + route.lstrip("/")


def _attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "parent", parent)


def _parent_node(node: ast.AST) -> ast.AST | None:
    return (
        getattr(node, "parent", None)
        if isinstance(getattr(node, "parent", None), ast.AST)
        else None
    )


def _external_boundary(
    info: ModuleInfo, body: ast.AST
) -> tuple[str, str, str, int | None, bool, int, bool] | bool | None:
    for statement in ast.walk(body):
        if not isinstance(statement, ast.Return) or not isinstance(
            statement.value, ast.Dict
        ):
            continue
        fields = {
            _literal_string(key): value
            for key, value in zip(
                statement.value.keys, statement.value.values, strict=True
            )
            if key is not None and _literal_string(key) is not None
        }
        if "url" not in fields:
            continue
        method = (
            _literal_string(fields.get("method", ast.Constant(value="GET"))) or "GET"
        )
        url = _literal_string(fields["url"]) or (
            info.literals.get(fields["url"].id)
            if isinstance(fields["url"], ast.Name)
            else None
        )
        if url is None or method not in {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
        }:
            return False
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "@" in parsed.netloc
        ):
            return False
        host = parsed.hostname.lower()
        if ":" in host:
            try:
                address = ip_address(host)
            except ValueError:
                return False
            if not isinstance(address, IPv6Address) or address.compressed != host:
                return False
        elif any(not label for label in host.split(".")):
            return False
        query = [
            part.partition("=")[0].lower() for part in parsed.query.split("&") if part
        ]
        return (
            method,
            parsed.scheme,
            host,
            port,
            bool(parsed.path),
            len(query),
            any(
                any(
                    token in name
                    for token in ("secret", "token", "password", "key", "credential")
                )
                for name in query
            ),
        )
    return None
