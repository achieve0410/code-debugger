import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";

export type EvidenceKind = "observed" | "inferred" | "unresolved";
type Layer = "frontend" | "http" | "backend" | "data" | "external" | "unresolved";
type Selection = { type: "node" | "edge"; id: string } | null;
type Evidence = { kind: EvidenceKind; adapter: string; adapterVersion: string; reason?: string; eventId?: string; timestamp?: string };
type Source = { repository: string; path: string; line?: number; endLine?: number; symbol?: string };
export type GraphNode = { id: string; kind: string; identityKey: string; label: string; layer: Layer; source: Source; evidence: Evidence[]; confidence: number; metadata: Record<string, unknown> };
export type GraphEdge = { id: string; source: string; target: string; kind: string; evidence: Evidence[]; confidence: number; metadata: Record<string, unknown> };
export type PresetPosition = { x: number; y: number };
export function buildRoutePresetPositions(nodes: readonly GraphNode[], edges: readonly GraphEdge[], rootId: string): Record<string, PresetPosition> {
  const invalid = (): never => { throw new Error("preset_layout_invalid_input"); };
  if (nodes.length === 0 && edges.length === 0) return {};
  if (typeof rootId !== "string") return invalid();

  const nodeIndex = new Map<string, number>();
  let previousNodeId: string | null = null;
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (!node || typeof node.id !== "string" || (previousNodeId !== null && previousNodeId >= node.id) || nodeIndex.has(node.id) || !Number.isSafeInteger(index)) return invalid();
    nodeIndex.set(node.id, index);
    previousNodeId = node.id;
  }

  const incoming = Array.from({ length: nodes.length }, () => [] as number[]);
  let previousEdgeId: string | null = null;
  for (let index = 0; index < edges.length; index += 1) {
    const edge = edges[index];
    if (!edge || typeof edge.id !== "string" || typeof edge.source !== "string" || typeof edge.target !== "string" || (previousEdgeId !== null && previousEdgeId >= edge.id) || !Number.isSafeInteger(index)) return invalid();
    const source = nodeIndex.get(edge.source);
    const target = nodeIndex.get(edge.target);
    if (source === undefined || target === undefined) return invalid();
    incoming[target].push(index);
    previousEdgeId = edge.id;
  }

  if (nodes.length === 0) return invalid();
  const adjacency = Array.from({ length: nodes.length }, () => [] as number[]);
  for (let target = 0; target < incoming.length; target += 1) {
    for (const edgeIndex of incoming[target]) {
      const source = nodeIndex.get(edges[edgeIndex].source);
      if (source === undefined) return invalid();
      adjacency[source].push(target);
    }
  }

  const depths = new Array<number>(nodes.length).fill(-1);
  const queue: number[] = [];
  const initial = nodeIndex.get(rootId) ?? 0;
  depths[initial] = 0;
  queue.push(initial);
  let head = 0;
  let maximumLayer = 0;
  const traverse = (baseLayer: number) => {
    while (head < queue.length) {
      const source = queue[head];
      head += 1;
      const layer = depths[source];
      if (layer > maximumLayer) maximumLayer = layer;
      for (const target of adjacency[source]) {
        if (depths[target] !== -1) continue;
        depths[target] = layer + 1;
        queue.push(target);
      }
    }
    if (baseLayer > maximumLayer) maximumLayer = baseLayer;
  };
  traverse(0);

  for (let seed = 0; seed < nodes.length; seed += 1) {
    if (depths[seed] !== -1) continue;
    const baseLayer = maximumLayer + 1;
    if (!Number.isSafeInteger(baseLayer)) return invalid();
    depths[seed] = baseLayer;
    queue.push(seed);
    traverse(baseLayer);
  }

  const layers = Array.from({ length: maximumLayer + 1 }, () => [] as number[]);
  for (let index = 0; index < nodes.length; index += 1) {
    const layer = depths[index];
    if (!Number.isSafeInteger(layer) || layer < 0 || layer >= layers.length) return invalid();
    layers[layer].push(index);
  }

  const positions: Record<string, PresetPosition> = {};
  for (let layer = 0; layer < layers.length; layer += 1) {
    for (let rank = 0; rank < layers[layer].length; rank += 1) {
      const x = 28 + layer * 260;
      const y = 28 + rank * 92;
      if (!Number.isFinite(x) || !Number.isFinite(y)) return invalid();
      positions[nodes[layers[layer][rank]].id] = { x, y };
    }
  }

  const positionIds = Object.keys(positions);
  const positionKeys = new Set<string>();
  if (positionIds.length !== nodes.length) return invalid();
  for (const node of nodes) {
    if (!Object.prototype.hasOwnProperty.call(positions, node.id)) return invalid();
    const position = positions[node.id];
    const positionKey = `${position.x}\u0000${position.y}`;
    if (!Number.isFinite(position.x) || !Number.isFinite(position.y) || positionKeys.has(positionKey)) return invalid();
    positionKeys.add(positionKey);
  }
  return positions;
}
type Route = { id: string; repository: string; framework: "react" | "vue" | "nuxt" | "django"; path: string; nodeId: string };
type Diagnostic = { id: string; code: string; severity: "info" | "warning" | "error"; message: string; repository?: string; source?: Source; nodeId?: string; edgeId?: string; candidateIds?: string[]; eventId?: string };
type GraphSnapshotV2 = { schemaVersion: 2; project: string; repositorySetId: string; repositories: { namespace: string }[]; routes: Route[]; nodes: GraphNode[]; edges: GraphEdge[]; diagnostics: Diagnostic[] };
type ActiveConfig = { project: string; runtimeEnabled: boolean; schemaVersion: 2; repositorySetId: string; repositories: { namespace: string; displayRoot: string }[]; repoRoots: string[]; compatibilityWarnings: string[] };
type GraphState = "loading" | "empty" | "incompatible" | "invalid" | "unavailable" | "ready";

class ApiError extends Error {
  constructor(readonly status: number, readonly error: string, readonly code?: string, readonly action?: string) {
    super(error);
    this.name = "ApiError";
  }
}

const kinds: EvidenceKind[] = ["observed", "inferred", "unresolved"];
const layers: Layer[] = ["frontend", "http", "backend", "data", "external", "unresolved"];
const evidenceLabel: Record<EvidenceKind, string> = { observed: "Observed", inferred: "Inferred", unresolved: "Unresolved" };
const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown, max = 4096, min = 1): value is string => typeof value === "string" && value.length >= min && value.length <= max && value.normalize("NFC") === value && !/[\x00-\x1f\x7f]/.test(value);
const id = (value: unknown, prefix: "n" | "e" | "r" | "d") => typeof value === "string" && new RegExp(`^${prefix}_[0-9a-f]{64}$`).test(value);
const namespace = (value: unknown) => typeof value === "string" && /^[a-z][a-z0-9._-]{0,63}$/.test(value);
const token = (value: unknown, max = 127) => typeof value === "string" && new RegExp(`^[A-Za-z][A-Za-z0-9_.-]{0,${max}}$`).test(value);
const runtimeCaptureIdentifier = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
const eventId = (value: string) => /^(?:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|legacy-[0-9a-f]{64})$/.test(value);
const sorted = <T,>(items: T[], key: (item: T) => string) => items.every((item, index) => index === 0 || key(items[index - 1]) < key(item));
const evidenceKey = (item: unknown) => isObject(item) ? ["kind", "adapter", "adapterVersion", "reason", "eventId", "timestamp"].map((key) => canonical(item[key] ?? null)).join("\0") : "";
const sortedValues = (value: unknown, allowed: Set<string>, min: number, max: number) => Array.isArray(value) && value.length >= min && value.length <= max && value.every((item) => typeof item === "string" && allowed.has(item)) && value.every((item, index) => index === 0 || value[index - 1] < item);
const sameKeys = (value: Record<string, unknown>, keys: string[], required = keys) => required.every((key) => key in value) && Object.keys(value).every((key) => keys.includes(key));
const secret = /-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}\b|\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bxox[baprs]-[^\s]{10,}\b|\b(?:authorization|cookie|set[ _-]?cookie|password|passwd|pwd|secret|token|access[ _-]?token|refresh[ _-]?token|api[ _-]?key|apikey|credential|credentials|private[ _-]?key|client[ _-]?secret|session|sessionid|csrf|xsrf|baggage)\s*[:=]\s*\S+|(?<![A-Za-z0-9])[A-Za-z0-9+/_-]{32,}(?![A-Za-z0-9+/_-])/i;
const secretKey = (key: string) => /^(authorization|cookie|set_cookie|password|passwd|pwd|secret|token|access_token|refresh_token|api_key|apikey|credential|credentials|private_key|client_secret|session|sessionid|csrf|xsrf|baggage|request_body|response_body)$/.test(key.normalize("NFKC").replace(/([a-z])([A-Z])/g, "$1_$2").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).join("_"));
function canonical(value: unknown): string { if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`; if (isObject(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`; return JSON.stringify(value); }
function noSecret(value: unknown, key?: string): boolean { if (key && secretKey(key) && !["hasSensitiveQuery", "hasSensitiveFields"].includes(key)) return false; if (typeof value === "string") return !secret.test(value); if (Array.isArray(value)) return value.every((item) => noSecret(item)); return !isObject(value) || Object.entries(value).every(([name, item]) => noSecret(item, name)); }
function utcMillisecond(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value)) return false;
  const match = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)\.(\d{3})Z$/.exec(value);
  if (!match) return false;
  const [year, month, day, hour, minute, second, millisecond] = match.slice(1).map(Number);
  if (year < 1) return false;
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, second, millisecond);
  return instant.getUTCFullYear() === year && instant.getUTCMonth() === month - 1 && instant.getUTCDate() === day && instant.getUTCHours() === hour && instant.getUTCMinutes() === minute && instant.getUTCSeconds() === second && instant.getUTCMilliseconds() === millisecond;
}
function metadataShape(value: unknown, depth = 0): boolean {
  if (depth > 8) return false;
  if (Array.isArray(value)) return value.length <= 1000 && value.every((item) => metadataShape(item, depth + 1));
  if (isObject(value)) return Object.keys(value).length <= 1000 && Object.entries(value).every(([key, item]) => text(key, 64) && metadataShape(item, depth + 1));
  return value === null || typeof value === "boolean" || typeof value === "number" && Number.isFinite(value) || typeof value === "string" && text(value, 4096, 0);
}
function repositoryPath(value: unknown): value is string {
  if (!text(value, 2048) || value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return false;
  return value.split("/").every((part, index) => {
    if (!part || (index === 0 && /^[A-Za-z]:/.test(part)) || /%(?![0-9A-Fa-f]{2})/.test(part)) return false;
    try { const decoded = decodeURIComponent(part); const twice = decodeURIComponent(decoded); return ![".", ".."].includes(decoded) && ![".", ".."].includes(twice) && !/[\\/\x00-\x1f\x7f]/.test(decoded) && !/[\\/]/.test(twice) && encodeURIComponent(decoded).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`) === part; } catch { return false; }
  });
}
function endpointPath(value: unknown, placeholders?: "literal" | "bounded" | "unbounded"): value is string {
  if (!text(value, 2048) || !value.startsWith("/") || value.startsWith("//") || /[\\?#]/.test(value)) return false;
  const found: string[] = [];
  const parts = value.split("/").slice(1);
  for (const [index, part] of parts.entries()) {
    if (!part) { if (value !== "/" && index !== parts.length - 1) return false; continue; }
    if ([".", ".."].includes(part) || /[\s\x00-\x1f\x7f-\x9f]/.test(part)) return false;
    const match = /^\{([pu])([0-9]|[12][0-9]|3[01])\}$/.exec(part);
    if (match) { found.push(match[1] + match[2]); continue; }
    if (/[{}@]|\%(?![0-9A-F]{2})/.test(part)) return false;
    try {
      const decoded = decodeURIComponent(part); const twice = decodeURIComponent(decoded);
      if ([".", ".."].includes(decoded) || [decoded, twice].some((item) => /[\\/\s\x00-\x1f\x7f-\x9f]/.test(item)) || encodeURIComponent(decoded).replace(/%3A/g, ":").replace(/%40/g, "@").replace(/%24/g, "$").replace(/%26/g, "&").replace(/%2B/g, "+").replace(/%2C/g, ",").replace(/%3B/g, ";").replace(/%3D/g, "=") !== part) return false;
    } catch { return false; }
  }
  const indexes = found.map((item) => Number(item.slice(1)));
  const validPlaceholders = placeholders === undefined
    || placeholders === "literal" && found.length === 0
    || placeholders === "bounded" && found.length > 0 && found.every((item) => item[0] === "p")
    || placeholders === "unbounded" && found.length > 0 && found.every((item) => item[0] === "u");
  return (!indexes.length || (indexes.every((item, index) => item === index) && new Set(indexes).size === indexes.length)) && validPlaceholders;
}
function djangoDeclaredPath(value: unknown): value is string {
  if (!text(value, 2048)) return false;
  const normalized = value.replace(/\/(?:<[A-Za-z_][A-Za-z0-9_]{0,63}(?::[A-Za-z_][A-Za-z0-9_]{0,63})?>)/g, "/segment");
  return endpointPath(normalized) && value.split("/").slice(1).every((segment) => !segment.startsWith("<") || /^<(?:[A-Za-z_][A-Za-z0-9_]{0,63}:)?[A-Za-z_][A-Za-z0-9_]{0,63}>$/.test(segment));
}
function canonicalHost(value: unknown): value is string {
  if (typeof value !== "string" || !value || value.length > 253 || value !== value.toLowerCase() || value.includes("@") || value.endsWith(".")) return false;
  try {
    const hostname = new URL(`http://[${value}]/`).hostname;
    if (hostname === `[${value}]`) return !value.includes("%");
  } catch { /* DNS is handled below. */ }
  try {
    const hostname = new URL(`http://${value}/`).hostname;
    if (hostname === value && /^\d+\.\d+\.\d+\.\d+$/.test(value)) return true;
  } catch { return false; }
  return value.split(".").every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
}
const metadataKeys: Record<string, string[]> = { frontend_route: ["framework", "declaredPath"], page: ["frameworkOwners"], component: ["frameworkOwners"], ui_event: ["frameworkOwners", "eventKind", "elementKind", "modifiers"], function: ["frameworkOwners", "pythonQualifiedName"], http_call: ["method", "urlResolution", "normalizedPath", "endpointId", "queryFieldCount", "hasSensitiveQuery", "targetRepository"], request_payload: ["payloadKinds", "bodyShape", "bodyFieldCount", "queryFieldCount", "hasSensitiveFields"], django_url_pattern: ["declaredPath", "normalizedPath", "endpointId", "converters"], django_view: ["pythonQualifiedName"], model: ["pythonQualifiedName"], query_boundary: ["operation", "modelQualifiedName"], external_service: ["method", "scheme", "host", "port", "pathPresent", "queryFieldCount", "hasSensitiveQuery", "boundaryOnly"], unresolved_target: ["reasonCode", "candidateIds"], carries: ["payloadKinds"], resolves_to: ["resolutionTier", "targetRepository"], renders: [], contains: [], handles: [], navigates_to: [], calls: [], invokes: [], accesses: [], branches_to: [] };
const nodeKinds = new Set(["frontend_route", "page", "component", "ui_event", "function", "http_call", "request_payload", "django_url_pattern", "django_view", "model", "query_boundary", "external_service", "unresolved_target"]);
const edgeKinds = new Set(["renders", "contains", "handles", "navigates_to", "calls", "carries", "resolves_to", "invokes", "accesses", "branches_to"]);
const layerByKind: Record<string, Layer[]> = { frontend_route: ["frontend"], page: ["frontend"], component: ["frontend"], ui_event: ["frontend"], function: ["frontend", "backend"], http_call: ["http"], request_payload: ["http"], django_url_pattern: ["backend"], django_view: ["backend"], model: ["data"], query_boundary: ["data"], external_service: ["external"], unresolved_target: ["unresolved"] };
const tuples: Record<string, string[]> = { renders: ["frontend_route:page", "frontend_route:component", "page:component", "component:component"], contains: ["page:ui_event", "component:ui_event", "page:function", "component:function"], handles: ["ui_event:function", "ui_event:unresolved_target"], navigates_to: ["page:frontend_route", "component:frontend_route", "function:frontend_route", "page:unresolved_target", "component:unresolved_target", "function:unresolved_target"], calls: ["page:http_call", "component:http_call", "function:http_call", "page:function", "component:function", "function:function", "django_view:function", "function:external_service", "django_view:external_service", "page:unresolved_target", "component:unresolved_target", "function:unresolved_target", "django_view:unresolved_target"], carries: ["http_call:request_payload"], resolves_to: ["http_call:django_url_pattern", "http_call:external_service", "http_call:unresolved_target", "request_payload:django_url_pattern", "request_payload:external_service", "request_payload:unresolved_target", "django_url_pattern:django_view", "django_url_pattern:unresolved_target"], invokes: ["django_view:function", "function:function", "django_view:unresolved_target", "function:unresolved_target"], accesses: ["django_view:query_boundary", "function:query_boundary", "query_boundary:model", "query_boundary:unresolved_target"], branches_to: ["page:unresolved_target", "component:unresolved_target", "function:unresolved_target", "django_view:unresolved_target"] };
const evidenceReasons: Record<EvidenceKind, string[]> = { inferred: ["ast_route_declaration", "ast_symbol_declaration", "ast_call", "ast_handler_binding", "ast_import_binding", "literal_url", "finite_url_domain", "request_payload_shape", "django_url_declaration", "django_view_binding", "django_query_call", "external_boundary", "exact_endpoint", "declared_path", "configured_base", "dynamic_converter"], unresolved: ["dynamic_target_unproven", "referenced_target_missing", "python_module_unproven", "python_module_ambiguous", "url_target_unmatched", "url_target_ambiguous", "unsupported_syntax"], observed: ["runtime_coherent_endpoint", "runtime_coherent_view", "runtime_coherent_resolution"] };
const diagnosticCatalog: Record<string, [string, string, string[]]> = {
  frontend_analyzer_unavailable: ["warning", "Frontend analyzer is unavailable.", ["repository"]], frontend_analyzer_failed: ["error", "Frontend analyzer failed.", ["repository"]], frontend_analyzer_invalid_output: ["error", "Frontend analyzer returned invalid output.", ["repository"]], source_read_failed: ["warning", "A source file could not be read.", ["repository", "source"]], unsupported_syntax: ["warning", "Unsupported syntax was left unresolved.", ["repository", "source", "nodeId"]], unresolved_dynamic_target: ["warning", "A dynamic target could not be proven.", ["repository", "source", "nodeId"]], unresolved_referenced_target: ["warning", "A referenced target was not declared in the analyzed graph.", ["repository", "source", "nodeId"]], unresolved_django_url: ["warning", "A Django URL declaration could not be resolved statically.", ["repository", "source"]], python_import_module_unresolved: ["warning", "A Python import module could not be proven.", ["repository", "source", "nodeId"]], python_import_module_ambiguous: ["warning", "A Python import module has multiple valid candidates.", ["repository", "source", "nodeId"]], bounded_url_proof_invalid: ["error", "A bounded URL proof was invalid.", ["repository"]], url_target_unmatched: ["warning", "No unique Django URL target matched the request shape.", ["repository", "nodeId"]], url_target_ambiguous: ["warning", "Multiple Django URL targets matched the request shape.", ["repository", "nodeId", "candidateIds"]], runtime_capture_empty: ["info", "The selected runtime capture contains no events.", []], runtime_event_unmatched: ["warning", "The runtime event did not match one canonical flow.", ["eventId"]], runtime_event_ambiguous: ["warning", "The runtime event matched multiple canonical flows.", ["eventId", "candidateIds"]], runtime_identity_conflict: ["warning", "The runtime event identities do not describe one canonical flow.", ["eventId", "candidateIds"]]
};
export function validConverterSegmentIndex(value: unknown, actual: number): value is number {
  return typeof value === "number"
    && Number.isInteger(value)
    && value >= 0
    && value <= 255
    && value === actual;
}
function metadata(kind: string, value: unknown, nodeLayer?: Layer): boolean {
  if (!isObject(value) || !sameKeys(value, metadataKeys[kind] ?? [], []) || !metadataShape(value) || new TextEncoder().encode(canonical(value)).length > 16384 || !Object.entries(value).every(([key, item]) => key === "candidateIds" || noSecret(item, key))) return false;
  const count = (item: unknown) => Number.isInteger(item) && typeof item === "number" && item >= 0 && item <= 1000;
  const qualified = (item: unknown) => typeof item === "string" && /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$/.test(item);
  const method = (item: unknown) => typeof item === "string" && /^[A-Z][A-Z0-9!#$%&'*+.^_`|~-]{0,31}$/.test(item);
  if (kind === "frontend_route") return ["react", "vue", "nuxt", "django"].includes(String(value.framework)) && (value.framework === "django" ? djangoDeclaredPath(value.declaredPath) : typeof value.declaredPath === "string" && endpointPath(value.declaredPath.replace(/\/:[A-Za-z_][A-Za-z0-9_]*/g, "/segment")) && value.declaredPath.split("/").slice(1).every((segment) => !segment.startsWith(":") || /^:[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(segment)));
  if (kind === "page" || kind === "component") return sortedValues(value.frameworkOwners, new Set(["nuxt", "react", "vue"]), 1, 3);
  if (kind === "ui_event") return sortedValues(value.frameworkOwners, new Set(["nuxt", "react", "vue"]), 1, 3) && token(value.eventKind, 63) && token(value.elementKind, 63) && (Array.isArray(value.modifiers) && value.modifiers.length <= 16 && sortedValues(value.modifiers, new Set(value.modifiers.filter((item): item is string => token(item, 63))), 0, 16));
  if (kind === "function") return (nodeLayer === "frontend" ? value.frameworkOwners !== undefined : value.frameworkOwners === undefined) && (value.frameworkOwners === undefined || sortedValues(value.frameworkOwners, new Set(["nuxt", "react", "vue"]), 1, 3)) && (value.pythonQualifiedName === undefined || qualified(value.pythonQualifiedName));
  if (kind === "http_call") return method(value.method) && ["literal", "bounded_template", "unbounded"].includes(String(value.urlResolution)) && endpointPath(value.normalizedPath, value.urlResolution === "literal" ? "literal" : value.urlResolution === "bounded_template" ? "bounded" : "unbounded") && count(value.queryFieldCount) && typeof value.hasSensitiveQuery === "boolean" && (value.targetRepository === undefined || namespace(value.targetRepository)) && (value.urlResolution === "unbounded" ? value.endpointId === undefined : value.endpointId === undefined || text(value.endpointId, 2300, 3));
  if (kind === "request_payload") return sortedValues(value.payloadKinds, new Set(["body", "query", "form"]), 1, 3) && ["none", "object", "array", "scalar", "unknown"].includes(String(value.bodyShape)) && count(value.bodyFieldCount) && count(value.queryFieldCount) && typeof value.hasSensitiveFields === "boolean";
  if (kind === "django_url_pattern") {
    if (!Array.isArray(value.converters) || value.converters.length > 32) return false;
    const converters = value.converters;
    if (!djangoDeclaredPath(value.declaredPath) || !endpointPath(value.normalizedPath, converters.length ? "bounded" : "literal") || !text(value.endpointId, 2300, 3)) return false;
    const declared = String(value.declaredPath).split("/").slice(1).map((segment, index) => [index, segment] as const).filter(([, segment]) => segment.startsWith("<"));
    const normalized = String(value.normalizedPath).split("/").slice(1);
    const names = new Set<string>();
    return declared.length === converters.length && declared.every(([segmentIndex, segment], ordinal) => {
      const converter = converters[ordinal];
      const match = /^<(?:([A-Za-z_][A-Za-z0-9_]{0,63}):)?([A-Za-z_][A-Za-z0-9_]{0,63})>$/.exec(segment);
      const expectedKind = match?.[1] && ["int", "str", "slug", "uuid", "path"].includes(match[1]) ? match[1] : match?.[1] ? "custom" : "str";
      return isObject(converter) && sameKeys(converter, ["name", "kind", "segmentIndex"]) && converter.name === match?.[2] && expectedKind === converter.kind && ["int", "str", "slug", "uuid", "path", "custom"].includes(String(converter.kind)) && validConverterSegmentIndex(converter.segmentIndex, segmentIndex) && normalized[segmentIndex] === `{p${ordinal}}` && !names.has(String(converter.name)) && (names.add(String(converter.name)), true);
    });
  }
  if (kind === "django_view" || kind === "model") return value.pythonQualifiedName === undefined || qualified(value.pythonQualifiedName);
  if (kind === "query_boundary") return ["all", "filter", "get", "create", "update", "delete", "aggregate", "other"].includes(String(value.operation)) && (value.modelQualifiedName === undefined || qualified(value.modelQualifiedName));
  if (kind === "external_service") return method(value.method) && ["http", "https"].includes(String(value.scheme)) && canonicalHost(value.host) && (value.port === undefined || Number.isInteger(value.port) && Number(value.port) >= 1 && Number(value.port) <= 65535) && typeof value.pathPresent === "boolean" && count(value.queryFieldCount) && typeof value.hasSensitiveQuery === "boolean" && value.boundaryOnly === true;
  if (kind === "unresolved_target") return evidenceReasons.unresolved.includes(String(value.reasonCode)) && (value.candidateIds === undefined || sortedValues(value.candidateIds, new Set((value.candidateIds as unknown[]).filter((item): item is string => id(item, "n"))), 1, 100));
  if (kind === "carries") return Object.keys(value).length === 0 || sortedValues(value.payloadKinds, new Set(["body", "query", "form"]), 1, 3);
  return kind !== "resolves_to" || ["exact_endpoint", "declared_path", "configured_base", "dynamic_converter", "external_boundary", "unbounded"].includes(String(value.resolutionTier)) && (value.targetRepository === undefined || namespace(value.targetRepository));
}
const evidence = (value: unknown, allowObserved: boolean): value is Evidence[] => Array.isArray(value) && value.length > 0 && value.length <= 32 && sorted(value, evidenceKey) && new Set(value.map(canonical)).size === value.length && value.every((item) => isObject(item) && sameKeys(item, ["kind", "adapter", "adapterVersion", "reason", "eventId", "timestamp"], ["kind", "adapter", "adapterVersion"]) && kinds.includes(item.kind as EvidenceKind) && token(item.adapter) && typeof item.adapterVersion === "string" && /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$/.test(item.adapterVersion) && (item.reason === undefined || evidenceReasons[item.kind as EvidenceKind].includes(String(item.reason))) && (item.kind !== "observed" ? item.eventId === undefined && item.timestamp === undefined : allowObserved && eventId(String(item.eventId)) && utcMillisecond(item.timestamp)));
const strongest = (records: Evidence[]) => records.some((item) => item.kind === "observed") ? "observed" : records.some((item) => item.kind === "unresolved") ? "unresolved" : "inferred";
const sha256Constants = new Uint32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
function sha256(bytes: Uint8Array): string {
  const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
  padded.set(bytes); padded[bytes.length] = 0x80;
  const bitLength = bytes.length * 8;
  const high = Math.floor(bitLength / 0x1_0000_0000);
  const low = bitLength % 0x1_0000_0000;
  for (let index = 0; index < 4; index++) {
    padded[padded.length - 8 + index] = high >>> (24 - index * 8);
    padded[padded.length - 4 + index] = low >>> (24 - index * 8);
  }
  const hash = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const words = new Uint32Array(64);
  const constants = sha256Constants;
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let index = 0; index < 16; index++) words[index] = (padded[offset + index * 4] << 24) | (padded[offset + index * 4 + 1] << 16) | (padded[offset + index * 4 + 2] << 8) | padded[offset + index * 4 + 3];
    for (let index = 16; index < 64; index++) { const a = words[index - 15]; const b = words[index - 2]; words[index] = (((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3)) + words[index - 16] + (((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10)) + words[index - 7]; }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index++) { const s1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7)); const choice = (e & f) ^ (~e & g); const s0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10)); const majority = (a & b) ^ (a & c) ^ (b & c); const next = (h + s1 + choice + constants[index] + words[index]) | 0; h = g; g = f; f = e; e = (d + next) | 0; d = c; c = b; b = a; a = (next + s0 + majority) | 0; }
    hash[0] += a; hash[1] += b; hash[2] += c; hash[3] += d; hash[4] += e; hash[5] += f; hash[6] += g; hash[7] += h;
  }
  return [...hash].map((word) => word.toString(16).padStart(8, "0")).join("");
}
function stableId(prefix: "n_" | "e_" | "r_" | "d_", ...parts: unknown[]) {
  const bytes = new TextEncoder().encode(canonical(parts).replace(/[\u0080-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`));
  return `${prefix}${sha256(bytes)}`;
}
function expectedLabel(node: GraphNode): string | null {
  if (node.kind === "http_call") return `${String(node.metadata.method)} ${String(node.metadata.normalizedPath)}`;
  if (node.kind === "request_payload") return "Request payload";
  if (node.kind === "external_service") {
    const host = String(node.metadata.host);
    const authority = host.includes(":") ? `[${host}]` : host;
    const port = node.metadata.port === undefined ? "" : `:${String(node.metadata.port)}`;
    return `${String(node.metadata.method)} ${String(node.metadata.scheme)}://${authority}${port}`;
  }
  if (node.kind === "unresolved_target") return "Unresolved";
  return null;
}
function validDiagnosticId(value: Record<string, unknown>) {
  const fields = ["code", "severity", "message", "repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"].map((key) => canonical(value[key] ?? null));
  return value.id === stableId("d_", ...fields);
}
function source(value: unknown, namespaces: Set<string>): value is Source { return isObject(value) && sameKeys(value, ["repository", "path", "line", "endLine", "symbol"], ["repository", "path"]) && namespaces.has(String(value.repository)) && repositoryPath(value.path) && noSecret(value.path) && (value.line === undefined || Number.isInteger(value.line) && Number(value.line) >= 1 && Number(value.line) <= 10_000_000) && (value.endLine === undefined || Number.isInteger(value.endLine) && Number(value.endLine) >= Number(value.line ?? 1) && Number(value.endLine) <= 10_000_000) && (value.symbol === undefined || text(value.symbol, 512) && /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(value.symbol) && noSecret(value.symbol)); }
async function graphSnapshot(value: unknown, allowObserved = false): Promise<GraphSnapshotV2 | null> {
  if (!isObject(value) || !sameKeys(value, ["schemaVersion", "project", "repositorySetId", "repositories", "routes", "nodes", "edges", "diagnostics"]) || value.schemaVersion !== 2 || !text(value.project, 128) || !noSecret(value.project) || !/^[0-9a-f]{64}$/.test(String(value.repositorySetId)) || !Array.isArray(value.repositories) || !Array.isArray(value.routes) || !Array.isArray(value.nodes) || !Array.isArray(value.edges) || !Array.isArray(value.diagnostics) || !value.repositories.length || value.repositories.length > 64 || value.routes.length > 10000 || value.nodes.length > 50000 || value.edges.length > 200000 || value.diagnostics.length > 10000 || new TextEncoder().encode(canonical(value)).length > 32 * 1024 * 1024) return null;
  const repositories = value.repositories as { namespace: string }[]; if (!sorted(repositories, (repo) => repo.namespace) || !repositories.every((repo) => isObject(repo) && sameKeys(repo, ["namespace"]) && namespace(repo.namespace))) return null;
  const namespaces = new Set(repositories.map((repo) => repo.namespace)); if (namespaces.size !== repositories.length || new Set(repositories.map((repo) => repo.namespace.toLowerCase())).size !== repositories.length) return null;
  const nodes = value.nodes as GraphNode[]; if (!sorted(nodes, (node) => node.id)) return null;
  for (const node of nodes) { if (!isObject(node) || !sameKeys(node, ["id", "kind", "identityKey", "label", "layer", "source", "evidence", "confidence", "metadata"]) || !id(node.id, "n") || !nodeKinds.has(node.kind) || !layerByKind[node.kind].includes(node.layer) || !text(node.identityKey, 512) || !text(node.label, 256) || !source(node.source, namespaces) || !evidence(node.evidence, allowObserved) || typeof node.confidence !== "number" || !Number.isFinite(node.confidence) || node.confidence < 0 || node.confidence > 1 || !metadata(node.kind, node.metadata, node.layer) || !noSecret(node.identityKey) || !noSecret(node.label)) return null; const label = expectedLabel(node); if (label !== null && node.label !== label) return null; }
  for (const node of nodes) if (node.id !== stableId("n_", "node", node.source.repository, node.source.path, node.kind, node.identityKey)) return null;
  const nodeMap = new Map(nodes.map((node) => [node.id, node])); if (nodeMap.size !== nodes.length) return null;
  for (const node of nodes) {
    if (node.kind === "http_call" && node.metadata.endpointId !== undefined) {
      const expected = `${String(node.metadata.method)} ${node.metadata.targetRepository === undefined ? "" : `${String(node.metadata.targetRepository)}:`}${String(node.metadata.normalizedPath)}`;
      if (node.metadata.endpointId !== expected) return null;
    }
    if (node.kind === "django_url_pattern") { const endpoint = String(node.metadata.endpointId).split(" "); if (endpoint.length !== 2 || !/^[A-Z][A-Z0-9!#$%&'*+.^_`|~-]{0,31}$/.test(endpoint[0]) || endpoint[1] !== node.metadata.normalizedPath) return null; }
  }
  const edges = value.edges as GraphEdge[]; if (!sorted(edges, (edge) => edge.id)) return null;
  for (const edge of edges) if (!isObject(edge) || !sameKeys(edge, ["id", "source", "target", "kind", "evidence", "confidence", "metadata"]) || !id(edge.id, "e") || !nodeMap.has(edge.source) || !nodeMap.has(edge.target) || edge.source === edge.target || !edgeKinds.has(edge.kind) || !tuples[edge.kind].includes(`${nodeMap.get(edge.source)!.kind}:${nodeMap.get(edge.target)!.kind}`) || !evidence(edge.evidence, allowObserved) || typeof edge.confidence !== "number" || !Number.isFinite(edge.confidence) || edge.confidence < 0 || edge.confidence > 1 || !metadata(edge.kind, edge.metadata)) return null;
  for (const edge of edges) if (edge.id !== stableId("e_", "edge", edge.source, edge.target, edge.kind)) return null;
  const edgeIds = new Set(edges.map((edge) => edge.id)); if (edgeIds.size !== edges.length) return null;
  const outgoingByNode = new Map<string, GraphEdge[]>();
  const incomingByNode = new Map<string, GraphEdge[]>();
  for (const edge of edges) {
    const outgoing = outgoingByNode.get(edge.source) ?? [];
    outgoing.push(edge);
    outgoingByNode.set(edge.source, outgoing);
    const incoming = incomingByNode.get(edge.target) ?? [];
    incoming.push(edge);
    incomingByNode.set(edge.target, incoming);
  }
  for (const edge of edges) {
    if (edge.kind !== "resolves_to") continue;
    const target = nodeMap.get(edge.target)!; const data = edge.metadata;
    if (target.kind === "unresolved_target" && (data.resolutionTier !== "unbounded" || data.targetRepository !== undefined)) return null;
    if ((data.targetRepository !== undefined && (!namespaces.has(String(data.targetRepository)) || data.targetRepository !== target.source.repository)) || (data.resolutionTier === "unbounded" && target.kind !== "unresolved_target") || (data.resolutionTier === "external_boundary" && target.kind !== "external_service") || (["exact_endpoint", "configured_base", "dynamic_converter"].includes(String(data.resolutionTier)) && target.kind !== "django_url_pattern") || (data.resolutionTier === "declared_path" && !(target.kind === "django_view" && nodeMap.get(edge.source)!.kind === "django_url_pattern"))) return null;
    let owner = nodeMap.get(edge.source)!;
    if (owner.kind === "request_payload") {
      const carries = (incomingByNode.get(owner.id) ?? []).filter((candidate) => candidate.kind === "carries");
      if (carries.length !== 1 || (carries[0].metadata.payloadKinds !== undefined && canonical(carries[0].metadata.payloadKinds) !== canonical(owner.metadata.payloadKinds))) return null;
      owner = nodeMap.get(carries[0].source)!;
    }
    if (owner.kind !== "http_call") continue;
    if (owner.metadata.targetRepository !== undefined && target.kind !== "unresolved_target" && owner.metadata.targetRepository !== target.source.repository) return null;
    if (data.resolutionTier === "unbounded") {
      if (owner.metadata.endpointId !== undefined) return null;
      continue;
    }
    if (owner.metadata.urlResolution === "unbounded") return null;
    if (owner.metadata.urlResolution === "bounded_template") {
      if (data.resolutionTier !== "dynamic_converter" || target.kind !== "django_url_pattern" || owner.metadata.endpointId !== `${String(owner.metadata.method)} ${target.source.repository}:${String(target.metadata.normalizedPath)}` || target.metadata.endpointId !== `${String(owner.metadata.method)} ${String(target.metadata.normalizedPath)}` || owner.metadata.targetRepository !== target.source.repository || data.targetRepository !== target.source.repository || owner.metadata.normalizedPath !== target.metadata.normalizedPath || !(target.metadata.converters as unknown[]).length || !(target.metadata.converters as unknown[]).every((converter) => isObject(converter) && ["int", "str", "slug", "uuid"].includes(String(converter.kind)))) return null;
      continue;
    }
    if (owner.metadata.urlResolution !== "literal" || !["exact_endpoint", "configured_base", "external_boundary"].includes(String(data.resolutionTier))) return null;
    if (target.kind !== "django_url_pattern") continue;
    if (owner.metadata.method !== String(target.metadata.endpointId).split(" ", 1)[0] || (data.resolutionTier === "exact_endpoint" && owner.metadata.normalizedPath !== target.metadata.normalizedPath)) return null;
    if (owner.metadata.endpointId !== undefined) {
      const endpoint = String(owner.metadata.endpointId).replace(/^[^ ]+ [^:]+:/, `${String(owner.metadata.method)} `);
      if (endpoint !== target.metadata.endpointId) return null;
    }
  }
  for (const node of nodes) { const outgoing = outgoingByNode.get(node.id) ?? []; const incoming = incomingByNode.get(node.id) ?? []; if (node.kind === "http_call" && ((outgoing.some((edge) => edge.kind === "carries") && outgoing.some((edge) => edge.kind === "resolves_to")) || (!outgoing.some((edge) => edge.kind === "carries") && outgoing.filter((edge) => edge.kind === "resolves_to").length !== 1))) return null; if (node.kind === "request_payload" && (incoming.filter((edge) => edge.kind === "carries").length !== 1 || outgoing.filter((edge) => edge.kind === "resolves_to").length !== 1)) return null; if (node.kind === "django_url_pattern" && outgoing.filter((edge) => edge.kind === "resolves_to").length !== 1) return null; }
  const routes = value.routes as Route[]; if (!sorted(routes, (route) => `${route.framework === "django"}\0${route.path}\0${route.repository}\0${route.framework}\0${route.nodeId}\0${route.id}`) || new Set(routes.map((route) => route.id)).size !== routes.length || new Set(routes.map((route) => `${route.repository}\0${route.framework}\0${route.path}`)).size !== routes.length) return null;
  for (const route of routes) if (!isObject(route) || !sameKeys(route, ["id", "repository", "framework", "path", "nodeId"]) || !id(route.id, "r") || !namespaces.has(route.repository) || !["react", "vue", "nuxt", "django"].includes(route.framework) || !endpointPath(route.path) || !nodeMap.has(route.nodeId) || nodeMap.get(route.nodeId)!.kind !== (route.framework === "django" ? "django_url_pattern" : "frontend_route") || nodeMap.get(route.nodeId)!.source.repository !== route.repository) return null;
  for (const route of routes) if (route.id !== stableId("r_", route.repository, route.framework, route.path, route.nodeId)) return null;
  const diagnosticList = value.diagnostics as Record<string, unknown>[]; if (!sorted(diagnosticList, (diagnostic) => String(diagnostic.id)) || new Set(diagnosticList.map((diagnostic) => diagnostic.id)).size !== diagnosticList.length) return null;
  for (const diagnostic of diagnosticList) { const spec = diagnosticCatalog[String(diagnostic.code)]; const refs = ["repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"]; const runtimeOnly = ["runtime_capture_empty", "runtime_event_unmatched", "runtime_event_ambiguous", "runtime_identity_conflict"].includes(String(diagnostic.code)); if (!isObject(diagnostic) || !spec || runtimeOnly && !allowObserved || !sameKeys(diagnostic, ["id", "code", "severity", "message", ...refs], ["id", "code", "severity", "message"]) || !id(diagnostic.id, "d") || !validDiagnosticId(diagnostic) || diagnostic.severity !== spec[0] || diagnostic.message !== spec[1] || Object.keys(diagnostic).filter((key) => refs.includes(key)).every((key) => spec[2].includes(key)) === false || (diagnostic.repository !== undefined && (!namespaces.has(String(diagnostic.repository)) || !noSecret(diagnostic.repository))) || (diagnostic.source !== undefined && !source(diagnostic.source, namespaces)) || (diagnostic.nodeId !== undefined && !nodeMap.has(String(diagnostic.nodeId))) || (diagnostic.edgeId !== undefined && !edgeIds.has(String(diagnostic.edgeId)) ) || (diagnostic.candidateIds !== undefined && !sortedValues(diagnostic.candidateIds, new Set((diagnostic.candidateIds as unknown[]).filter((item): item is string => typeof item === "string" && nodeMap.has(item))), 1, 100)) || (diagnostic.eventId !== undefined && (!allowObserved || !eventId(String(diagnostic.eventId)))) || new TextEncoder().encode(canonical(diagnostic)).length > 4096) return null; }
  return value as GraphSnapshotV2;
}
function safeError(status: number, value: unknown) {
  const catalog: Record<number, Record<string, { code?: string; action?: string }>> = {
    400: Object.fromEntries(["invalid_host_header", "invalid_origin_header", "invalid_capability_header", "invalid_selector", "unsupported_transfer_encoding", "invalid_content_length", "invalid_json_body", "invalid_request"].map((error) => [error, {}])),
    403: Object.fromEntries(["origin_forbidden", "mutation_forbidden", "runtime_disabled"].map((error) => [error, {}])),
    404: { snapshot_not_found: { action: "analyze" }, not_found: {} },
    405: { method_not_allowed: {} }, 408: { request_timeout: {} },
    409: { snapshot_incompatible: { code: "snapshot_incompatibility", action: "reanalyze" }, runtime_unavailable: { action: "analyze_without_capture" } },
    411: { length_required: {} }, 413: { request_too_large: {} }, 415: { unsupported_media_type: {} }, 421: { misdirected_request: {} },
    422: { snapshot_invalid: { action: "delete_or_reanalyze" } }, 500: { internal_error: {} }
  };
  if (!isObject(value) || typeof value.error !== "string") return new ApiError(status, "protocol_unavailable");
  const spec = catalog[status]?.[value.error];
  if (!spec) return new ApiError(status, "protocol_unavailable");
  const keys = ["error", ...(spec.code ? ["code"] : []), ...(spec.action ? ["action"] : [])];
  if (!sameKeys(value, keys) || (spec.code === "snapshot_incompatibility" && (typeof value.code !== "string" || !["legacy_snapshot_incompatible", "snapshot_schema_unsupported", "snapshot_repository_set_mismatch", "snapshot_manifest_mismatch"].includes(value.code))) || (spec.action !== undefined && value.action !== spec.action)) return new ApiError(status, "protocol_unavailable");
  return new ApiError(status, value.error, typeof value.code === "string" ? value.code : undefined, typeof value.action === "string" ? value.action : undefined);
}
async function readJson(response: Response): Promise<unknown> { try { return await response.json(); } catch { throw new ApiError(response.status, "protocol_unavailable"); } }
async function api<T>(url: string, init?: RequestInit): Promise<T> { let response: Response; try { response = await fetch(url, init); } catch (caught) { if ((caught as DOMException).name === "AbortError") throw caught; throw new ApiError(0, "network_unavailable"); } const body = await readJson(response); if (!response.ok) throw safeError(response.status, body); return body as T; }
function safeDisplayRoot(value: unknown, repository: string): value is string {
  if (typeof value !== "string" || !text(value, 2048) || secret.test(value)) return false;
  if (value === ".") return true;
  if (value === `external:${repository}`) return true;
  if (value.startsWith("external:")) return false;
  let decoded = value;
  for (let iteration = 0; iteration < 8; iteration += 1) {
    if (decoded.startsWith("external:") || /[\x00-\x1f\x7f\\]/.test(decoded) || decoded.startsWith("/") || decoded.startsWith("//") || /^[A-Za-z]:/.test(decoded)) return false;
    const segments = decoded.split("/");
    if (!segments.every((segment) => segment && segment !== "." && segment !== ".." && !/%(?![0-9A-Fa-f]{2})/.test(segment))) return false;
    let next: string;
    try {
      const decodedSegments = segments.map((segment) => decodeURIComponent(segment));
      if (decodedSegments.some((segment) => segment === "." || segment === ".." || /[\\/]/.test(segment))) return false;
      next = decodedSegments.join("/");
    } catch { return false; }
    if (next === decoded) return true;
    decoded = next;
  }
  return false;
}
function config(value: unknown): { active: ActiveConfig; mutationToken: string } | null {
  if (!isObject(value) || !sameKeys(value, ["project", "runtimeEnabled", "schemaVersion", "repositorySetId", "repositories", "repoRoots", "compatibilityWarnings", "mutationCapability"]) || !text(value.project, 128) || !noSecret(value.project) || typeof value.runtimeEnabled !== "boolean" || value.schemaVersion !== 2 || !/^[0-9a-f]{64}$/.test(String(value.repositorySetId)) || !Array.isArray(value.repositories) || !Array.isArray(value.repoRoots) || !Array.isArray(value.compatibilityWarnings) || typeof value.mutationCapability !== "string" || !/^[A-Za-z0-9_-]{32,512}$/.test(value.mutationCapability)) return null;
  const repositories = value.repositories; if (!repositories.length || repositories.length > 64 || !sorted(repositories, (item) => String(isObject(item) ? item.namespace : "")) || !repositories.every((repository) => isObject(repository) && sameKeys(repository, ["namespace", "displayRoot"]) && typeof repository.namespace === "string" && namespace(repository.namespace) && safeDisplayRoot(repository.displayRoot, repository.namespace)) || new Set(repositories.map((repository) => String((repository as Record<string, unknown>).namespace).toLowerCase())).size !== repositories.length || value.repoRoots.length !== repositories.length || !value.repoRoots.every((root, index) => root === (repositories[index] as Record<string, unknown>).displayRoot) || !sortedValues(value.compatibilityWarnings, new Set(["repoRoots_deprecated_v2"]), 1, 1)) return null;
  return { active: { project: value.project, runtimeEnabled: value.runtimeEnabled, schemaVersion: 2, repositorySetId: value.repositorySetId as string, repositories: repositories as ActiveConfig["repositories"], repoRoots: value.repoRoots as string[], compatibilityWarnings: value.compatibilityWarnings as string[] }, mutationToken: value.mutationCapability };
}

function routeScope(graph: GraphSnapshotV2, root: string, expanded: Set<string>) {
  const outgoing = new Map<string, GraphEdge[]>();
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  for (const edge of graph.edges) {
    const edges = outgoing.get(edge.source) ?? [];
    edges.push(edge);
    outgoing.set(edge.source, edges);
  }
  for (const edges of outgoing.values()) edges.sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
  const seen = new Set([root]); const edgeIds = new Set<string>(); const queue = [root];
  for (let index = 0; index < queue.length; index += 1) {
    const choices = outgoing.get(queue[index]) ?? [];
    for (const [choice, edge] of choices.entries()) {
      if (!nodeIds.has(edge.target) || (choice > 0 && !expanded.has(edge.source))) continue;
      edgeIds.add(edge.id); if (!seen.has(edge.target)) { seen.add(edge.target); queue.push(edge.target); }
    }
  }
  return { nodes: graph.nodes.filter((node) => seen.has(node.id)), edges: graph.edges.filter((edge) => edgeIds.has(edge.id)) };
}

function reachableIds(graph: GraphSnapshotV2, root: string) {
  const outgoing = new Map<string, GraphEdge[]>();
  for (const edge of graph.edges) {
    const edges = outgoing.get(edge.source) ?? [];
    edges.push(edge);
    outgoing.set(edge.source, edges);
  }
  const nodes = new Set([root]); const edges = new Set<string>(); const queue = [root];
  for (let index = 0; index < queue.length; index += 1) for (const edge of outgoing.get(queue[index]) ?? []) {
    edges.add(edge.id);
    if (!nodes.has(edge.target)) { nodes.add(edge.target); queue.push(edge.target); }
  }
  return { nodes, edges };
}

function stateForError(error: ApiError): GraphState {
  if (error.error === "invalid_graph_response") return "invalid";
  if (error.status === 404 && error.error === "snapshot_not_found") return "empty";
  if (error.status === 409) return "incompatible";
  if (error.status === 422) return "invalid";
  return "unavailable";
}

function graphMessage(error: ApiError) {
  return error.action ? `${error.error}: ${error.action}` : error.error;
}

export type OperationKind = "loading-static" | "analyzing-static" | "analyzing-capture" | "refreshing-static";
export type OperationState = { phase: "idle"; id: number } | { phase: "running"; id: number; kind: OperationKind };
export type OutlineNodeEntry = { type: "node"; id: string; label: string; kind: string; evidence: EvidenceKind; repository: string; path: string; line?: number; symbol?: string; accessibleName: string };
export type OutlineEdgeEntry = { type: "edge"; id: string; label: string; kind: string; evidence: EvidenceKind; sourceId: string; targetId: string; sourceLabel: string; sourceKind: string; sourceRepository: string; sourcePath: string; targetLabel: string; targetKind: string; targetRepository: string; targetPath: string; accessibleName: string };
export type OutlineEntry = OutlineNodeEntry | OutlineEdgeEntry;
export const OUTLINE_PAGE_SIZE = 100;
const MAX_STYLED_SELECTION_ELEMENTS = 5_000;
export type GraphTokens = { canvas: string; nodeFill: string; nodeBorder: string; nodeLabel: string; edge: string; edgeLabel: string; edgeLabelBackground: string; evidenceObserved: string; evidenceInferred: string; evidenceUnresolved: string; backbone: string; selectionFill: string; selectionBorder: string; selectionEdge: string };

const outlineKey = (entry: OutlineEntry) => `${entry.type}:${entry.id}`;
const kindWords = (value: string) => value.replaceAll("_", " ");
const outlineName = (entry: OutlineEntry) => entry.type === "node"
  ? `Node: ${entry.label}; kind: ${kindWords(entry.kind)}; evidence: ${evidenceLabel[entry.evidence]}; source: ${entry.repository}/${entry.path}${entry.line === undefined ? "" : `:${entry.line}`}`
  : `Edge: ${kindWords(entry.kind)}; from ${entry.sourceLabel} (${kindWords(entry.sourceKind)}, ${entry.sourceRepository}/${entry.sourcePath}) to ${entry.targetLabel} (${kindWords(entry.targetKind)}, ${entry.targetRepository}/${entry.targetPath}); evidence: ${evidenceLabel[entry.evidence]}`

export function buildFlowOutline(nodes: readonly GraphNode[], edges: readonly GraphEdge[], rootId: string | null): OutlineEntry[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const edgeById = new Set(edges.map((edge) => edge.id));
  if (nodeById.size !== nodes.length || edgeById.size !== edges.length || edges.some((edge) => !nodeById.has(edge.source) || !nodeById.has(edge.target))) throw new Error("flow_outline_invalid_input");
  const outgoing = new Map<string, GraphEdge[]>();
  for (const edge of edges) { const list = outgoing.get(edge.source) ?? []; list.push(edge); outgoing.set(edge.source, list); }
  for (const list of outgoing.values()) list.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const result: OutlineEntry[] = []; const seenNodes = new Set<string>(); const seenEdges = new Set<string>();
  const appendNode = (node: GraphNode) => result.push({ type: "node", id: node.id, label: node.label, kind: node.kind, evidence: strongest(node.evidence), repository: node.source.repository, path: node.source.path, line: node.source.line, symbol: node.source.symbol, accessibleName: "" });
  const appendEdge = (edge: GraphEdge) => { const source = nodeById.get(edge.source)!; const target = nodeById.get(edge.target)!; result.push({ type: "edge", id: edge.id, label: edge.kind, kind: edge.kind, evidence: strongest(edge.evidence), sourceId: edge.source, targetId: edge.target, sourceLabel: source.label, sourceKind: source.kind, sourceRepository: source.source.repository, sourcePath: source.source.path, targetLabel: target.label, targetKind: target.kind, targetRepository: target.source.repository, targetPath: target.source.path, accessibleName: "" }); };
  const walk = (seed: string) => {
    const queue = [seed]; seenNodes.add(seed);
    for (let index = 0; index < queue.length; index += 1) {
      const nodeId = queue[index]; appendNode(nodeById.get(nodeId)!);
      for (const edge of outgoing.get(nodeId) ?? []) {
        if (!seenEdges.has(edge.id)) { seenEdges.add(edge.id); appendEdge(edge); }
        if (!seenNodes.has(edge.target)) { seenNodes.add(edge.target); queue.push(edge.target); }
      }
    }
  };
  if (rootId && nodeById.has(rootId)) walk(rootId);
  for (const nodeId of [...nodeById.keys()].sort()) if (!seenNodes.has(nodeId)) walk(nodeId);
  const collisions = new Map<string, OutlineEntry[]>();
  for (const entry of result) { const base = outlineName(entry); collisions.set(base, [...(collisions.get(base) ?? []), entry]); }
  const names = new Map<string, string>();
  for (const [base, entries] of collisions) {
    const ordered = [...entries].sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
    for (const [index, entry] of ordered.entries()) names.set(outlineKey(entry), ordered.length > 1 ? `${base}; occurrence ${index + 1} of ${ordered.length}` : base);
  }
  return result.map((entry) => ({ ...entry, accessibleName: names.get(outlineKey(entry))! }));
}
export function normalizeOutlineQuery(value: string): string { return value.normalize("NFKC").toLocaleLowerCase("en-US").trim().replace(/\s+/g, " "); }
export function filterFlowOutline(entries: readonly OutlineEntry[], query: string): OutlineEntry[] {
  const terms = normalizeOutlineQuery(query).split(" ").filter(Boolean);
  if (!terms.length) return [...entries];
  const haystack = (entry: OutlineEntry) => normalizeOutlineQuery(entry.type === "node"
    ? [entry.label, kindWords(entry.kind), evidenceLabel[entry.evidence], entry.repository, entry.path, entry.symbol ?? ""].join(" ")
    : [kindWords(entry.kind), evidenceLabel[entry.evidence], entry.sourceLabel, kindWords(entry.sourceKind), entry.sourceRepository, entry.sourcePath, entry.targetLabel, kindWords(entry.targetKind), entry.targetRepository, entry.targetPath].join(" "));
  return entries.filter((entry) => { const text = haystack(entry); return terms.every((term) => text.includes(term)); });
}
export function readGraphTokens(element: HTMLElement): GraphTokens {
  const names: Record<keyof GraphTokens, string> = { canvas: "--graph-canvas", nodeFill: "--graph-node-fill", nodeBorder: "--graph-node-border", nodeLabel: "--graph-node-label", edge: "--graph-edge", edgeLabel: "--graph-edge-label", edgeLabelBackground: "--graph-edge-label-background", evidenceObserved: "--graph-evidence-observed", evidenceInferred: "--graph-evidence-inferred", evidenceUnresolved: "--graph-evidence-unresolved", backbone: "--graph-backbone", selectionFill: "--graph-selection-fill", selectionBorder: "--graph-selection-border", selectionEdge: "--graph-selection-edge" };
  const resolved = {} as GraphTokens;
  for (const [key, name] of Object.entries(names) as [keyof GraphTokens, string][]) {
    if (!getComputedStyle(element).getPropertyValue(name).trim()) throw new Error(`Missing graph token: ${name}`);
    const wrapper = document.createElement("span"); const probe = document.createElement("span");
    wrapper.style.cssText = "position:absolute;visibility:hidden"; probe.style.color = `var(${name}, rgb(7, 8, 9))`; wrapper.append(probe); element.append(wrapper);
    try {
      wrapper.style.color = "rgb(1, 2, 3)"; const first = getComputedStyle(probe).color.trim();
      wrapper.style.color = "rgb(4, 5, 6)"; const second = getComputedStyle(probe).color.trim();
      const color = (value: string) => /^(?:rgba?|hsla?)\(/i.test(value) || /^#[0-9a-f]{3,8}$/i.test(value);
      if (!first || !second || first === "rgb(7, 8, 9)" || second === "rgb(7, 8, 9)" || /^var\(/i.test(first) || /^var\(/i.test(second)) throw new Error(`Missing graph token: ${name}`);
      if (!color(first) || !color(second) || first !== second) throw new Error(`Invalid graph token: ${name}`);
      resolved[key] = first;
    } finally { wrapper.remove(); }
  }
  return resolved;
}
export function cytoscapeStyles(tokens: GraphTokens): cytoscape.StylesheetJson {
  return [
    { selector: "node", style: { content: "data(label)", color: tokens.nodeLabel, "text-wrap": "wrap", "text-max-width": "180px", width: "190px", height: "58px", shape: "round-rectangle", "background-color": tokens.nodeFill, "border-color": tokens.nodeBorder, "border-width": "2px" } },
    { selector: "edge", style: { label: "data(label)", color: tokens.edgeLabel, "text-background-color": tokens.edgeLabelBackground, "text-background-opacity": 1, "text-background-padding": "3px", "text-background-shape": "roundrectangle", "target-arrow-shape": "triangle", "line-color": tokens.edge, "target-arrow-color": tokens.edge, width: "2px" } },
    { selector: ".evidence-observed", style: { "border-style": "solid", "line-style": "solid", "border-color": tokens.evidenceObserved, "line-color": tokens.evidenceObserved, "target-arrow-color": tokens.evidenceObserved } },
    { selector: ".evidence-inferred", style: { "border-style": "dashed", "line-style": "dashed", "border-color": tokens.evidenceInferred, "line-color": tokens.evidenceInferred, "target-arrow-color": tokens.evidenceInferred } },
    { selector: ".evidence-unresolved", style: { "border-style": "dotted", "line-style": "dotted", "border-color": tokens.evidenceUnresolved, "line-color": tokens.evidenceUnresolved, "target-arrow-color": tokens.evidenceUnresolved } },
    { selector: ".backbone", style: { "line-color": tokens.backbone, "target-arrow-color": tokens.backbone, width: "4px", "z-index": 10 } },
    { selector: "node.selected", style: { "background-color": tokens.selectionFill, "border-color": tokens.selectionBorder, "border-width": "4px" } },
    { selector: "edge.selected", style: { "line-color": tokens.selectionEdge, "target-arrow-color": tokens.selectionEdge, width: "5px", "z-index": 11 } },
  ];
}

type StylingFault = { message: "Graph styling is unavailable. Use Flow Outline." } | null;
type OperationAlert = { cause: "validation"; serial: number; message: string } | { cause: "operation"; operationId: number; message: string } | null;
type WorkbenchModel = { graph: GraphSnapshotV2 | null; graphState: GraphState; graphMessage: string; statusText: string; transient: boolean; operation: OperationState; routeId: string | null; expanded: Set<string>; selection: Selection; evidenceFilter: Set<EvidenceKind>; layerFilter: Set<Layer>; routeQuery: string; outlineQuery: string; outlinePage: number; selectionNote: string; operationAlert: OperationAlert; focusKey: string | null };
const initialModel: WorkbenchModel = { graph: null, graphState: "loading", graphMessage: "", statusText: "", transient: false, operation: { phase: "idle", id: 0 }, routeId: null, expanded: new Set(), selection: null, evidenceFilter: new Set(kinds), layerFilter: new Set(layers), routeQuery: "", outlineQuery: "", outlinePage: 0, selectionNote: "", operationAlert: null, focusKey: null };
function visibleGraph(graph: GraphSnapshotV2, routeId: string | null, expanded: Set<string>, evidenceFilter: Set<EvidenceKind>, layerFilter: Set<Layer>) {
  const route = graph.routes.find((item) => item.id === routeId);
  const scoped = route ? routeScope(graph, route.nodeId, expanded) : { nodes: [], edges: [] };
  const nodes = scoped.nodes.filter((node) => layerFilter.has(node.layer) && evidenceFilter.has(strongest(node.evidence)));
  const ids = new Set(nodes.map((node) => node.id));
  return { nodes, edges: scoped.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target) && evidenceFilter.has(strongest(edge.evidence))) };
}
function reducer(model: WorkbenchModel, action: any): WorkbenchModel {
  if (action.type === "operation/start") return { ...model, operation: { phase: "running", id: action.id, kind: action.kind }, graphMessage: "", statusText: "", operationAlert: null, selectionNote: "" };
  if (action.id !== undefined && (model.operation.phase !== "running" || model.operation.id !== action.id)) return model;
  if (action.type === "operation/commitGraph") {
    const graph = action.graph as GraphSnapshotV2; const survivingRoute = graph.routes.some((route) => route.id === model.routeId);
    const routeId = survivingRoute ? model.routeId : (graph.routes.find((route) => route.framework !== "django") ?? graph.routes.find((route) => route.framework === "django") ?? null)?.id ?? null;
    const scope = routeId ? reachableIds(graph, graph.routes.find((route) => route.id === routeId)!.nodeId) : { nodes: new Set<string>(), edges: new Set<string>() };
    const expanded = survivingRoute ? new Set([...model.expanded].filter((id) => scope.nodes.has(id))) : new Set<string>();
    const visible = visibleGraph(graph, routeId, expanded, model.evidenceFilter, model.layerFilter);
    const selection = !survivingRoute ? null : model.selection && (model.selection.type === "node" ? visible.nodes : visible.edges).some((item) => item.id === model.selection!.id) ? model.selection : null;
    if (model.operation.phase !== "running") return model;
    const completion = model.operation.kind === "loading-static" ? "Static snapshot loaded." : model.operation.kind === "analyzing-static" ? "Static analysis complete." : model.operation.kind === "analyzing-capture" ? "Runtime capture analysis complete. Runtime evidence is transient." : "Static snapshot refreshed.";
    return { ...model, graph, graphState: graph.routes.length ? "ready" : "empty", graphMessage: "", statusText: completion, transient: action.staticReplacement ? false : action.transientResult, operation: { phase: "idle", id: action.id }, routeId, expanded, selection, outlineQuery: "", outlinePage: 0, selectionNote: "", focusKey: action.focusedOutlineKey ?? null, operationAlert: model.operationAlert?.cause === "operation" && model.operationAlert.operationId === action.id ? null : model.operationAlert };
  }
  if (action.type === "operation/failure") {
    const repositoryMismatch = action.errorCode === "repository_set_mismatch" || action.errorCode === "snapshot_repository_set_mismatch";
    const stale = Boolean(!repositoryMismatch && model.graph && model.graph.repositorySetId === action.repositorySetId && (action.errorStatus === 409 || action.errorStatus === 422));
    return {
      ...model,
      ...(repositoryMismatch ? { graph: null, transient: false, routeId: null, expanded: new Set<string>(), selection: null, outlineQuery: "", outlinePage: 0 } : {}),
      graphState: stale ? model.graphState : stateForError(new ApiError(action.errorStatus ?? 0, action.message)),
      graphMessage: stale ? `stale_graph: ${action.message}` : action.message,
      statusText: "",
      operation: { phase: "idle", id: action.id },
      selectionNote: "",
      operationAlert: { cause: "operation", operationId: action.id, message: action.alert },
    };
  }
  if (action.type === "operation/finish") return { ...model, operation: { phase: "idle", id: action.id }, selectionNote: "" };
  if (action.type === "validation") return { ...model, operationAlert: { cause: "validation", serial: action.serial, message: action.message } };
  if (action.type === "set") {
    const next = { ...model, ...action.value };
    const visibilityChanged = action.value.routeId !== undefined || action.value.expanded !== undefined || action.value.evidenceFilter !== undefined || action.value.layerFilter !== undefined;
    const interactionChanged = visibilityChanged || action.value.selection !== undefined;
    const clearedStatus = interactionChanged && model.statusText === "Selection cleared because it is no longer visible." ? { ...next, statusText: "" } : next;
    if (action.value.selection !== undefined) return clearedStatus;
    if (!visibilityChanged) return next;
    if (!model.graph || !model.selection) return { ...clearedStatus, selectionNote: "" };
    const visible = visibleGraph(model.graph, next.routeId, next.expanded, next.evidenceFilter, next.layerFilter);
    const remainsVisible = (model.selection.type === "node" ? visible.nodes : visible.edges).some((item) => item.id === model.selection!.id);
    if (remainsVisible) return { ...clearedStatus, selectionNote: "" };
    const cleared = "Selection cleared because it is no longer visible.";
    return { ...clearedStatus, selection: null, selectionNote: model.operation.phase === "running" ? cleared : "", statusText: model.operation.phase === "running" ? clearedStatus.statusText : cleared };
  }
  return model;
}

function routeAccessibleNames(routes: readonly Route[]) {
  const collisions = new Map<string, Route[]>();
  for (const route of routes) {
    const base = `${route.path}; ${route.framework}; repository ${route.repository}`;
    collisions.set(base, [...(collisions.get(base) ?? []), route]);
  }
  const names = new Map<string, string>();
  for (const [base, entries] of collisions) {
    const ordered = [...entries].sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
    for (const [index, route] of ordered.entries()) names.set(route.id, ordered.length > 1 ? `${base}; occurrence ${index + 1} of ${ordered.length}` : base);
  }
  return names;
}

function App() {
  const [model, dispatch] = useReducer(reducer, initialModel); const [activeConfig, setActiveConfig] = useState<ActiveConfig | null>(null); const [health, setHealth] = useState("checking"); const [runtimeCaptureId, setRuntimeCaptureId] = useState("");
  const mutationToken = useRef<string | null>(null); const graphRequest = useRef<AbortController | null>(null); const analyzeRequest = useRef<AbortController | null>(null); const healthRequest = useRef<AbortController | null>(null); const configRequest = useRef<AbortController | null>(null); const operationId = useRef(0); const startupRequested = useRef(false); const focusedOutlineKeyRef = useRef<string | null>(null); const outlineFocusActiveRef = useRef(false); const [stylingFault, setStylingFault] = useState<StylingFault>(null); const reportStylingFault = useCallback((fault: StylingFault) => setStylingFault(fault), []);
  const beginOperation = useCallback((kind: OperationKind, controller: AbortController) => { graphRequest.current?.abort(); analyzeRequest.current?.abort(); const id = ++operationId.current; if (kind === "analyzing-static" || kind === "analyzing-capture") analyzeRequest.current = controller; else graphRequest.current = controller; dispatch({ type: "operation/start", id, kind }); return id; }, []);
  const requestGraph = useCallback(async (kind: "loading-static" | "refreshing-static") => {
    if (!activeConfig) return; const controller = new AbortController(); const id = beginOperation(kind, controller);
    try { const parsed = await graphSnapshot(await api<unknown>("/api/graph", { signal: controller.signal })); if (controller.signal.aborted || id !== operationId.current) return; if (!parsed) throw new ApiError(200, "invalid_graph_response"); if (parsed.repositorySetId !== activeConfig.repositorySetId) throw new ApiError(409, "snapshot_incompatible", "repository_set_mismatch"); dispatch({ type: "operation/commitGraph", id, graph: parsed, transientResult: false, staticReplacement: true, focusedOutlineKey: outlineFocusActiveRef.current ? focusedOutlineKeyRef.current : null }); }
    catch (caught) { if (controller.signal.aborted || id !== operationId.current) return; const error = caught instanceof ApiError ? caught : new ApiError(0, "network_unavailable"); dispatch({ type: "operation/failure", id, errorStatus: error.status, errorCode: error.code, repositorySetId: activeConfig.repositorySetId, message: graphMessage(error), alert: `${kind === "refreshing-static" ? "Refresh" : "Static snapshot load"} failed. ${graphMessage(error)}` }); }
  }, [activeConfig, beginOperation]);
  useEffect(() => {
    let mounted = true; const healthController = new AbortController(); const configController = new AbortController(); healthRequest.current = healthController; configRequest.current = configController;
    void api<Record<string, unknown>>("/api/health", { signal: healthController.signal }).then((item) => { if (mounted && !healthController.signal.aborted) setHealth(isObject(item) && sameKeys(item, ["ok", "status"]) && item.ok === true && item.status === "ready" ? "ready" : "unavailable"); }).catch(() => { if (mounted && !healthController.signal.aborted) setHealth("unavailable"); });
    void api<unknown>("/api/config", { signal: configController.signal }).then((value) => { const parsed = config(value); if (!parsed) throw new ApiError(0, "invalid_config_response"); if (mounted && !configController.signal.aborted) { mutationToken.current = parsed.mutationToken; setActiveConfig(parsed.active); } }).catch((caught) => { if (mounted && !configController.signal.aborted) dispatch({ type: "set", value: { graphState: "unavailable", graphMessage: caught instanceof ApiError ? caught.error : "config_unavailable" } }); });
    return () => { mounted = false; healthController.abort(); configController.abort(); graphRequest.current?.abort(); analyzeRequest.current?.abort(); operationId.current += 1; mutationToken.current = null; };
  }, []);
  useEffect(() => { if (activeConfig && !startupRequested.current) { startupRequested.current = true; void requestGraph("loading-static"); } }, [activeConfig, requestGraph]);
  const selectedRoute = model.graph?.routes.find((route) => route.id === model.routeId) ?? null;
  const visible = useMemo(() => model.graph ? visibleGraph(model.graph, model.routeId, model.expanded, model.evidenceFilter, model.layerFilter) : { nodes: [], edges: [] }, [model.graph, model.routeId, model.expanded, model.evidenceFilter, model.layerFilter]);
  const outline = useMemo(() => selectedRoute ? buildFlowOutline(visible.nodes, visible.edges, selectedRoute.nodeId) : [], [visible, selectedRoute]);
  const filteredOutline = useMemo(() => filterFlowOutline(outline, model.outlineQuery), [outline, model.outlineQuery]);
  const pageCount = Math.max(1, Math.ceil(filteredOutline.length / OUTLINE_PAGE_SIZE)); const page = Math.min(model.outlinePage, pageCount - 1); const pageEntries = filteredOutline.slice(page * OUTLINE_PAGE_SIZE, (page + 1) * OUTLINE_PAGE_SIZE);
  useLayoutEffect(() => {
    const key = model.focusKey;
    if (!key || document.activeElement?.getAttribute("data-outline-key") === key) return;
    const index = filteredOutline.findIndex((entry) => outlineKey(entry) === key);
    if (index >= 0 && page !== Math.floor(index / OUTLINE_PAGE_SIZE)) {
      dispatch({ type: "set", value: { outlinePage: Math.floor(index / OUTLINE_PAGE_SIZE) } });
      return;
    }
    const target = index >= 0 ? document.querySelector<HTMLButtonElement>(`[data-outline-key="${CSS.escape(key)}"]`) : null;
    const search = document.getElementById("outline-search") as HTMLInputElement | null;
    (target ?? (search && !search.disabled ? search : document.getElementById("flow-outline-heading")))?.focus({ preventScroll: false });
    dispatch({ type: "set", value: { focusKey: null } });
  }, [model.focusKey, filteredOutline, page]);
  const updateInteraction = (value: Partial<WorkbenchModel>, resetOutline = false) => {
    const activeKey = document.activeElement?.getAttribute("data-outline-key") ?? null;
    dispatch({ type: "set", value: { ...value, focusKey: activeKey, ...(resetOutline ? { outlineQuery: "", outlinePage: 0 } : {}) } });
  };
  const choose = (selection: Selection) => { const entry = selection && outline.find((item) => item.type === selection.type && item.id === selection.id); if (entry && !filteredOutline.some((item) => outlineKey(item) === outlineKey(entry))) updateInteraction({ selection, selectionNote: "Selected entity is outside the outline search." }); else updateInteraction({ selection, selectionNote: "", outlinePage: entry ? Math.floor(filteredOutline.findIndex((item) => outlineKey(item) === outlineKey(entry)) / OUTLINE_PAGE_SIZE) : model.outlinePage }); };
  const analyze = async () => {
    if (!activeConfig || !mutationToken.current) { dispatch({ type: "validation", serial: operationId.current + 1, message: "configuration_unavailable" }); return; }
    const requested = runtimeCaptureId.trim(); if (requested && (!activeConfig.runtimeEnabled || !runtimeCaptureIdentifier(requested))) { dispatch({ type: "validation", serial: operationId.current + 1, message: !activeConfig.runtimeEnabled ? "runtime_disabled" : "invalid_runtime_capture_id" }); return; }
    const controller = new AbortController(); const id = beginOperation(requested ? "analyzing-capture" : "analyzing-static", controller);
    try { const parsed = await graphSnapshot(await api<unknown>("/api/analyze", { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json", "X-KG-Debugger-Capability": mutationToken.current }, body: JSON.stringify(requested ? { runtimeCaptureId: requested } : {}) }), Boolean(requested)); if (controller.signal.aborted || id !== operationId.current) return; if (!parsed) throw new ApiError(200, "invalid_graph_response"); if (parsed.repositorySetId !== activeConfig.repositorySetId) throw new ApiError(409, "snapshot_incompatible", "repository_set_mismatch"); dispatch({ type: "operation/commitGraph", id, graph: parsed, transientResult: Boolean(requested), staticReplacement: false, focusedOutlineKey: outlineFocusActiveRef.current ? focusedOutlineKeyRef.current : null }); }
    catch (caught) { if (controller.signal.aborted || id !== operationId.current) return; const error = caught instanceof ApiError ? caught : new ApiError(0, "network_unavailable"); dispatch({ type: "operation/failure", id, errorStatus: error.status, errorCode: error.code, repositorySetId: activeConfig.repositorySetId, message: graphMessage(error), alert: `Analyze failed. ${graphMessage(error)}` }); }
  };
  const runningText = model.operation.phase === "running" ? ({ "loading-static": "Loading static snapshot…", "analyzing-static": "Analyzing static snapshot…", "analyzing-capture": "Analyzing selected runtime capture…", "refreshing-static": "Refreshing static snapshot…" } as Record<OperationKind, string>)[model.operation.kind] : "";
  const completion = model.operation.phase === "idle" && !model.graphMessage ? model.statusText : "";
  const alert = model.operationAlert?.message ?? stylingFault?.message;
  const captureControl = <label className="field-label" htmlFor="runtime-capture-input">Runtime capture<input id="runtime-capture-input" data-testid="runtime-capture-input" className="search" value={runtimeCaptureId} disabled={!activeConfig?.runtimeEnabled} onChange={(event) => setRuntimeCaptureId(event.target.value)} placeholder={activeConfig?.runtimeEnabled ? "Optional capture ID" : "Runtime capture disabled"} /></label>;
  const controls = <><button className="primary-action" data-testid="analyze-button" disabled={!activeConfig} onClick={() => void analyze()}>Analyze</button><button className="secondary-action" data-testid="refresh-button" disabled={!activeConfig} onClick={() => void requestGraph("refreshing-static")}>Refresh static snapshot</button></>;
  if (!model.graph || model.graphState !== "ready") return <main className="app-shell"><header className="command-bar" aria-label="Execution Evidence Workbench commands"><div className="command-identity"><div><p className="eyebrow">Execution Evidence Workbench</p><h1>Execution evidence</h1></div></div><div className="command-status" aria-label="Debugger status"><span className="status-pill">API {health}</span><span id="snapshot-status" data-testid="snapshot-status" className="status-pill">Static snapshot</span></div><div className="command-actions">{captureControl}{controls}</div></header><section className="recovery-state" data-testid={`graph-state-${model.graphState}`} aria-busy={model.operation.phase === "running"}><h2>{model.graphState === "loading" ? "Loading execution graph" : model.graphState === "empty" ? "No routes are available" : model.graphState === "incompatible" ? "Graph incompatible" : model.graphState === "invalid" ? "Invalid graph response" : "Graph unavailable"}</h2><p data-testid="graph-message">{model.graphMessage || "Load an analyzed GraphSnapshotV2 to inspect routes."}</p><div id="operation-status" className="operation-status" data-testid="operation-status" role="status" aria-live="polite" aria-atomic="true">{runningText}</div>{(model.operationAlert?.message ?? stylingFault?.message) && <div id="operation-alert" data-testid="operation-alert" role="alert" aria-live="assertive" aria-atomic="true">{model.operationAlert?.message ?? stylingFault?.message}</div>}</section></main>;
  const routes = model.graph.routes.filter((route) => route.path.toLocaleLowerCase("en-US").includes(model.routeQuery.toLocaleLowerCase("en-US")));
  const routeNames = routeAccessibleNames(model.graph.routes);
  return <main className="app-shell"><header className="command-bar" aria-label="Execution Evidence Workbench commands"><div className="command-identity"><div><p className="eyebrow">Execution Evidence Workbench</p><h1>Execution evidence</h1></div></div><div className="command-status" aria-label="Debugger status"><span className="status-pill">{activeConfig?.project ?? model.graph.project}</span><span className="status-pill">API {health}</span><span id="snapshot-status" className="status-pill" data-testid="snapshot-status"><span data-testid={model.transient ? "transient-status" : "static-status"}>{model.transient ? "Runtime evidence · transient" : "Static snapshot"}</span></span></div><div className="command-actions">{captureControl}{controls}</div></header><div id="operation-status" className="operation-status" data-testid="operation-status" role="status" aria-live="polite" aria-atomic="true">{runningText || completion}</div>{model.selectionNote && <div data-testid="selection-status" aria-live="off">{model.selectionNote}</div>}{alert && <div id="operation-alert" data-testid="operation-alert" role="alert" aria-live="assertive" aria-atomic="true">{alert}</div>}
{model.graphMessage && <p className="alert-banner" data-testid="graph-message">{model.graphMessage}</p>}
    <section id="workbench" data-testid="workbench" className="workbench" aria-label="Execution Evidence Workbench" aria-busy={model.operation.phase === "running"}><nav id="routes-region" data-testid="routes-region" className="sidebar routes-panel" aria-label="Routes"><label className="field-label" htmlFor="route-search">Routes</label><input id="route-search" data-testid="route-search" className="search" value={model.routeQuery} onChange={(event) => updateInteraction({ routeQuery: event.target.value })} placeholder="Search routes" /><div className="route-list" data-testid="route-list">{routes.filter((route) => route.framework !== "django").map((route) => <RouteButton key={route.id} route={route} accessibleName={routeNames.get(route.id)!} current={model.routeId} choose={(routeId) => updateInteraction({ routeId, expanded: new Set() }, true)} />)}<details className="backend-route-group"><summary>Backend routes</summary>{routes.filter((route) => route.framework === "django").map((route) => <RouteButton key={route.id} route={route} accessibleName={routeNames.get(route.id)!} current={model.routeId} choose={(routeId) => updateInteraction({ routeId, expanded: new Set() }, true)} />)}</details></div><fieldset className="filter-group"><legend>Evidence</legend>{kinds.map((kind) => <label className="check-row" data-testid={`evidence-filter-${kind}`} key={kind}><input type="checkbox" checked={model.evidenceFilter.has(kind)} onChange={() => updateInteraction({ evidenceFilter: toggle(model.evidenceFilter, kind) }, true)} />{evidenceLabel[kind]}</label>)}</fieldset><fieldset className="filter-group"><legend>Layers</legend>{layers.map((layer) => <label className="check-row" data-testid={`layer-filter-${layer}`} key={layer}><input type="checkbox" checked={model.layerFilter.has(layer)} onChange={() => updateInteraction({ layerFilter: toggle(model.layerFilter, layer) }, true)} />{layer}</label>)}</fieldset></nav>
    <section id="graph-region" data-testid="graph-region" className="graph-stage" aria-labelledby="graph-heading"><div className="graph-toolbar"><h2 id="graph-heading">Graph</h2><strong>{selectedRoute?.path}</strong><span id="visible-node-count" data-testid="visible-node-count">{visible.nodes.length} nodes</span><span id="visible-edge-count" data-testid="visible-edge-count">{visible.edges.length} edges</span><button onClick={() => updateInteraction({ expanded: reachableIds(model.graph!, selectedRoute!.nodeId).nodes }, true)}>Expand all</button><button onClick={() => updateInteraction({ expanded: new Set() }, true)}>Collapse branches</button></div><CytoscapeGraph nodes={visible.nodes} edges={visible.edges} root={selectedRoute!.nodeId} selected={model.selection} select={choose} setStylingFault={reportStylingFault} /><section id="flow-outline" data-testid="flow-outline" className="flow-outline" aria-labelledby="flow-outline-heading" onFocusCapture={(event) => { const key = (event.target as HTMLElement).getAttribute("data-outline-key"); if (key) { focusedOutlineKeyRef.current = key; outlineFocusActiveRef.current = true; } }} onBlurCapture={(event) => { if (!(event.relatedTarget as HTMLElement | null)?.getAttribute("data-outline-key")) outlineFocusActiveRef.current = false; }}><div className="flow-outline-toolbar"><h2 id="flow-outline-heading" tabIndex={-1}>Flow Outline</h2><input id="outline-search" data-testid="outline-search" className="search" value={model.outlineQuery} onChange={(event) => updateInteraction({ outlineQuery: event.target.value, outlinePage: 0 })} placeholder="Search visible graph entities" /></div><div className="flow-outline-list">{pageEntries.map((entry) => <button id={`graph-${entry.type}-${entry.id}`} className={`outline-row outline-${entry.type}${model.selection?.type === entry.type && model.selection.id === entry.id ? " is-selected" : ""}`} key={outlineKey(entry)} data-outline-key={outlineKey(entry)} data-testid={`graph-${entry.type}-${entry.id}`} aria-label={entry.accessibleName} aria-pressed={model.selection?.type === entry.type && model.selection.id === entry.id} onClick={() => choose({ type: entry.type, id: entry.id })}><span className={`evidence-mark evidence-${entry.evidence}`} aria-hidden="true" /><span>{entry.type === "node" ? entry.label : kindWords(entry.kind)}</span><small>{entry.type === "node" ? `${kindWords(entry.kind)} · ${evidenceLabel[entry.evidence]}` : `${entry.sourceLabel} → ${entry.targetLabel} · ${evidenceLabel[entry.evidence]}`}</small></button>)}</div><div className="outline-pager"><button id="outline-previous" data-testid="outline-previous" disabled={page === 0} onClick={() => updateInteraction({ outlinePage: page - 1 })}>Previous</button><span id="outline-page" data-testid="outline-page">{`Page ${page + 1} of ${pageCount}`}</span><button id="outline-next" data-testid="outline-next" disabled={page === pageCount - 1} onClick={() => updateInteraction({ outlinePage: page + 1 })}>Next</button></div><p id="outline-count" className="outline-count" data-testid="outline-count">{`${filteredOutline.length} of ${outline.length} visible graph entities`}</p>{model.operationAlert && stylingFault && <div className="styling-fallback">{stylingFault.message}</div>}</section></section>
    <aside id="inspector" data-testid="inspector" className="inspector" aria-labelledby="inspector-heading"><h2 id="inspector-heading">Evidence Inspector</h2><Detail selection={model.selection} graph={model.graph} visible={visible} route={selectedRoute} evidenceFilter={model.evidenceFilter} layerFilter={model.layerFilter} transient={model.transient} /></aside></section></main>;
}
function RouteButton({ route, accessibleName, current, choose }: { route: Route; accessibleName: string; current: string | null; choose: (id: string) => void }) { return <button className={route.id === current ? "route-row is-selected" : "route-row"} aria-label={accessibleName} aria-pressed={route.id === current} data-testid={`route-option-${route.path}`} onClick={() => choose(route.id)}><span>{route.path}</span><small>{route.framework}</small></button>; }
function toggle<T>(source: Set<T>, value: T) { const next = new Set(source); next.has(value) ? next.delete(value) : next.add(value); return next; }
function CytoscapeGraph({ nodes, edges, root, selected, select, setStylingFault }: { nodes: GraphNode[]; edges: GraphEdge[]; root: string; selected: Selection; select: (selection: Selection) => void; setStylingFault: (fault: StylingFault) => void }) {
  const host = useRef<HTMLDivElement>(null); const cy = useRef<Core | null>(null); const selectRef = useRef(select); const previousSelectedId = useRef<string | null>(null); selectRef.current = select;
  useLayoutEffect(() => { if (!host.current) return; let styles: cytoscape.StylesheetJson; try { styles = cytoscapeStyles(readGraphTokens(host.current)); } catch { setStylingFault({ message: "Graph styling is unavailable. Use Flow Outline." }); return; } const instance = cytoscape({ container: host.current, style: styles }); instance.on("tap", "node", (event) => selectRef.current({ type: "node", id: event.target.id() })); instance.on("tap", "edge", (event) => selectRef.current({ type: "edge", id: event.target.id() })); cy.current = instance; (host.current as HTMLDivElement & { __cytoscapeForTests?: Core }).__cytoscapeForTests = instance; const media = matchMedia("(forced-colors: active)"); const refresh = () => { try { instance.style(cytoscapeStyles(readGraphTokens(host.current!))); setStylingFault(null); } catch { setStylingFault({ message: "Graph styling is unavailable. Use Flow Outline." }); } }; media.addEventListener("change", refresh); return () => { media.removeEventListener("change", refresh); delete (host.current as (HTMLDivElement & { __cytoscapeForTests?: Core }) | null)?.__cytoscapeForTests; instance.destroy(); cy.current = null; previousSelectedId.current = null; }; }, [setStylingFault]);
  useLayoutEffect(() => { const instance = cy.current; if (!instance) return; const positions = buildRoutePresetPositions(nodes, edges, root); const backbone = new Set<string>(); const seen = new Set<string>(); const queue = [root]; const adjacency = new Map<string, GraphEdge[]>(); for (const edge of edges) { const list = adjacency.get(edge.source) ?? []; list.push(edge); adjacency.set(edge.source, list); } for (let index = 0; index < queue.length; index += 1) { const source = queue[index]; if (seen.has(source)) continue; seen.add(source); for (const edge of adjacency.get(source) ?? []) if (!seen.has(edge.target)) { backbone.add(edge.id); queue.push(edge.target); } } const elements: ElementDefinition[] = [...nodes.map((node) => ({ data: { id: node.id, label: node.label }, classes: `evidence-${strongest(node.evidence)}` })), ...edges.map((edge) => ({ data: { id: edge.id, source: edge.source, target: edge.target, label: edge.kind }, classes: `evidence-${strongest(edge.evidence)}${backbone.has(edge.id) ? " backbone" : ""}` }))]; instance.batch(() => { instance.elements().remove(); instance.add(elements); instance.layout({ name: "preset", positions, fit: true, padding: 28, animate: false }).run(); }); }, [nodes, edges, root]);
  useEffect(() => { const instance = cy.current; if (!instance) return; const selectedId = selected?.id || null; const previousId = previousSelectedId.current; if (nodes.length + edges.length <= MAX_STYLED_SELECTION_ELEMENTS) { if (previousId && previousId !== selectedId) instance.$id(previousId).removeClass("selected"); if (selectedId) instance.$id(selectedId).addClass("selected"); } previousSelectedId.current = selectedId; }, [selected, nodes, edges, root]);
  return <div className="cy-shell"><div ref={host} className="cy-stage" data-testid="graph-canvas" role="img" aria-label="Visual execution graph. Use Flow Outline after the canvas to inspect every visible node and edge." aria-describedby="flow-outline-heading" /></div>;
}
function Detail({ selection, graph, visible, route, evidenceFilter, layerFilter, transient }: { selection: Selection; graph: GraphSnapshotV2; visible: { nodes: GraphNode[]; edges: GraphEdge[] }; route: Route | null; evidenceFilter: Set<EvidenceKind>; layerFilter: Set<Layer>; transient: boolean }) {
  const selectedNode = selection?.type === "node" ? graph.nodes.find((entry) => entry.id === selection.id) ?? null : null;
  const selectedEdge = selection?.type === "edge" ? graph.edges.find((entry) => entry.id === selection.id) ?? null : null;
  const item = selectedNode ?? selectedEdge;
  const values = <T extends string,>(all: readonly T[], active: Set<T>, label: (value: T) => string = (value) => value) => all.filter((value) => active.has(value)).map(label).join(", ") || "None";
  if (!item) {
    const reachable = route ? reachableIds(graph, route.nodeId) : { nodes: new Set<string>(), edges: new Set<string>() };
    const diagnostics = graph.diagnostics.filter((diagnostic) => !diagnostic.nodeId && !diagnostic.edgeId && (!diagnostic.repository || diagnostic.repository === route?.repository)).sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
    return <section className="inspector-section" data-testid="selected-detail"><div id="inspector-summary" data-testid="inspector-summary"><h3>Route Summary</h3><dl className="detail-list">
      <div><dt>Route path</dt><dd>{route?.path ?? "No route selected."}</dd></div>
      <div><dt>Framework</dt><dd>{route?.framework ?? "Not reported"}</dd></div>
      <div><dt>Repository</dt><dd>{route?.repository ?? "Not reported"}</dd></div>
      <div><dt>Snapshot mode</dt><dd>{transient ? "Runtime evidence · transient" : "Static snapshot"}</dd></div>
      <div><dt>Evidence filters</dt><dd>{values(kinds, evidenceFilter, (value) => evidenceLabel[value])}</dd></div>
      <div><dt>Layer filters</dt><dd>{values(layers, layerFilter)}</dd></div>
      <div><dt>Route nodes</dt><dd>{`${visible.nodes.length} of ${reachable.nodes.size} route nodes`}</dd></div>
      <div><dt>Route edges</dt><dd>{`${visible.edges.length} of ${reachable.edges.size} route edges`}</dd></div>
      <div><dt>Graph and repository diagnostics</dt><dd>{diagnostics.length ? diagnostics.map((diagnostic) => <span key={diagnostic.id}>{`${diagnostic.severity}: ${kindWords(diagnostic.code)}. ${diagnostic.message}`}</span>) : "No graph or repository diagnostics."}</dd></div>
    </dl></div></section>;
  }
  const records = item.evidence;
  const summary = [...new Set(records.map((record) => evidenceLabel[record.kind]))].join(" + ");
  const diagnostics = graph.diagnostics.filter((diagnostic) => selection && (selection.type === "node" ? diagnostic.nodeId === selection.id : diagnostic.edgeId === selection.id)).sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
  const confidence = `${(item.confidence * 100).toFixed(1)}%`;
  const node = selectedNode;
  const edge = selectedEdge;
  const sourceNode = edge ? graph.nodes.find((entry) => entry.id === edge.source) ?? null : null;
  const targetNode = edge ? graph.nodes.find((entry) => entry.id === edge.target) ?? null : null;
  const lines = node ? node.source.line === undefined ? "Not reported" : node.source.endLine === undefined || node.source.endLine === node.source.line ? String(node.source.line) : `${node.source.line}–${node.source.endLine}` : "Not reported";
  return <section className="inspector-section" data-testid="selected-detail"><h3>{node ? node.label : kindWords(edge!.kind)}</h3>
    {node ? <><div id="inspector-identity" data-testid="inspector-identity"><h4>Identity</h4><dl className="detail-list"><div><dt>Label</dt><dd>{node.label}</dd></div><div><dt>Kind</dt><dd>{kindWords(node.kind)}</dd></div><div><dt>Layer</dt><dd>{node.layer}</dd></div><div><dt>Confidence</dt><dd>{confidence}</dd></div></dl></div><div id="inspector-source" data-testid="inspector-source"><h4>Source</h4><dl className="detail-list"><div><dt>Repository</dt><dd>{node.source.repository}</dd></div><div><dt>Relative path</dt><dd>{node.source.path}</dd></div><div><dt>Lines</dt><dd>{lines}</dd></div><div><dt>Symbol</dt><dd>{node.source.symbol ?? "Not reported"}</dd></div></dl></div></> : <div id="inspector-relationship" data-testid="inspector-relationship"><h4>Relationship</h4><dl className="detail-list"><div><dt>Kind</dt><dd>{kindWords(edge!.kind)}</dd></div><div><dt>Source label</dt><dd>{sourceNode?.label ?? "Not reported"}</dd></div><div><dt>Source kind</dt><dd>{sourceNode ? kindWords(sourceNode.kind) : "Not reported"}</dd></div><div><dt>Target label</dt><dd>{targetNode?.label ?? "Not reported"}</dd></div><div><dt>Target kind</dt><dd>{targetNode ? kindWords(targetNode.kind) : "Not reported"}</dd></div><div><dt>Confidence</dt><dd>{confidence}</dd></div></dl></div>}
    <div id="inspector-evidence" data-testid="inspector-evidence"><h4>Evidence</h4><dl className="detail-list"><div><dt>Summary</dt><dd>{summary || "No evidence records."}</dd></div>{records.map((record, index) => <div key={`${record.kind}-${index}`}><dt>{`Evidence record ${index + 1}`}</dt><dd>{`Kind: ${evidenceLabel[record.kind]}; Adapter: ${record.adapter}; Adapter version: ${record.adapterVersion}; Basis: ${record.reason ? kindWords(record.reason) : "Not reported"}`}</dd></div>)}</dl></div>
    <div id="inspector-metadata" data-testid="inspector-metadata"><h4>Metadata</h4><dl className="detail-list"><Metadata kind={item.kind} value={item.metadata} /></dl></div>
    <div id="inspector-diagnostics" data-testid="inspector-diagnostics"><h4>Diagnostics</h4><dl className="detail-list"><div><dt>Diagnostics</dt><dd>{diagnostics.length ? diagnostics.map((diagnostic) => <span key={diagnostic.id}>{`Severity: ${diagnostic.severity}; Code: ${kindWords(diagnostic.code)}; Message: ${diagnostic.message}`}</span>) : "No diagnostics for this selection."}</dd></div></dl></div>
  </section>;
}
function Metadata({ kind, value }: { kind: string; value: Record<string, unknown> }) {
  const labels: Record<string, string> = { framework: "Framework", declaredPath: "Declared path", frameworkOwners: "Framework owners", eventKind: "Event kind", elementKind: "Element kind", modifiers: "Modifiers", pythonQualifiedName: "Python qualified name", method: "Method", urlResolution: "URL resolution", normalizedPath: "Normalized path", endpointId: "Endpoint ID", queryFieldCount: "Query field count", hasSensitiveQuery: "Contains sensitive query fields", targetRepository: "Target repository", payloadKinds: "Payload kinds", bodyShape: "Body shape", bodyFieldCount: "Body field count", hasSensitiveFields: "Contains sensitive fields", converters: "Converters", operation: "Operation", modelQualifiedName: "Model qualified name", scheme: "Scheme", host: "Host", port: "Port", pathPresent: "Path present", boundaryOnly: "Boundary only", reasonCode: "Reason", candidateIds: "Candidate count", resolutionTier: "Resolution tier" };
  const wordKeys = new Set(["eventKind", "elementKind", "urlResolution", "bodyShape", "operation", "reasonCode", "resolutionTier"]);
  const format = (key: string, item: unknown) => {
    if (key === "candidateIds" && Array.isArray(item)) return String(item.length);
    if (key === "converters" && Array.isArray(item)) return item.map((converter) => {
      const record = converter as { name: string; kind: string; segmentIndex: number };
      return `${record.name} (${record.kind}, segment ${record.segmentIndex})`;
    }).join(", ") || "None";
    if (Array.isArray(item)) return item.join(", ") || "None";
    if (typeof item === "boolean") return item ? "Yes" : "No";
    if (typeof item === "number") return String(item);
    return wordKeys.has(key) ? kindWords(String(item)) : String(item);
  };
  const keys = metadataKeys[kind] ?? [];
  const entries = keys.filter((key) => value[key] !== undefined);
  return entries.length ? <>{entries.map((key) => <div key={key}><dt>{labels[key]}</dt><dd>{format(key, value[key])}</dd></div>)}</> : <div><dt>Metadata</dt><dd>No additional metadata.</dd></div>;
}
export default App;
