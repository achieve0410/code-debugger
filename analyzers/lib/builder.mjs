import { createHash } from "node:crypto";

import { DIAGNOSTIC_CODES, EVIDENCE_REASONS, URL_PROOF_LIMITS, validateBoundedUrlProof } from "../contracts.mjs";
import { ADAPTER, ADAPTER_VERSION } from "./shared.mjs";

const DIAGNOSTIC_SPECS = Object.freeze({
  source_read_failed: Object.freeze({
    severity: "warning",
    message: "A source file could not be read.",
  }),
  unsupported_syntax: Object.freeze({
    severity: "warning",
    message: "Unsupported syntax was left unresolved.",
  }),
  unresolved_dynamic_target: Object.freeze({
    severity: "warning",
    message: "A dynamic target could not be proven.",
  }),
  unresolved_referenced_target: Object.freeze({
    severity: "warning",
    message: "A referenced target was not declared in the analyzed graph.",
  }),
});
const DIAGNOSTIC_FIELDS = Object.freeze([
  "code",
  "severity",
  "message",
  "repository",
  "source",
  "nodeId",
  "edgeId",
  "candidateIds",
  "eventId",
]);

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(",")}}`;
  }
  return JSON.stringify(value);
}

function diagnosticId(diagnostic) {
  const fields = DIAGNOSTIC_FIELDS.map((key) => canonicalJson(diagnostic[key] ?? null));
  return `d_${createHash("sha256").update(JSON.stringify(fields)).digest("hex")}`;
}

function defaultReason(kind) {
  if (kind === "unresolved_target") return "dynamic_target_unproven";
  if (kind === "frontend_route") return "ast_route_declaration";
  if (kind === "request_payload") return "request_payload_shape";
  if (kind === "http_call") return "literal_url";
  if (kind === "external_service") return "external_boundary";
  return "ast_symbol_declaration";
}

function owners(metadata) {
  return [...new Set([metadata.framework, ...(metadata.frameworkOwners ?? [])]
    .filter((value) => value === "react" || value === "vue" || value === "nuxt"))].sort();
}

function minimizedSource(source = {}) {
  const normalized = { repository: source.repository, path: source.path, line: source.line, endLine: source.endLine };
  if (/^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/u.test(source.symbol ?? "")) normalized.symbol = source.symbol;
  return Object.fromEntries(Object.entries(normalized).filter(([, value]) => value !== undefined));
}

function minimizedNodeMetadata(kind, metadata) {
  const frameworkOwners = owners(metadata);
  if (kind === "frontend_route") return { framework: metadata.framework, declaredPath: metadata.declaredPath ?? metadata.path };
  if (["page", "component", "function"].includes(kind)) return { frameworkOwners };
  if (kind === "ui_event") return { frameworkOwners, eventKind: String(metadata.event ?? "event").replace(/^on/u, "").toLowerCase().replace(/:/gu, ".") || "event", elementKind: String(metadata.element ?? "element").toLowerCase(), modifiers: [...new Set(metadata.modifiers ?? [])].sort() };
  if (kind === "unresolved_target") return { reasonCode: "dynamic_target_unproven" };
  if (kind === "external_service") return { method: metadata.method, scheme: metadata.scheme, host: metadata.host, ...(metadata.port ? { port: metadata.port } : {}), pathPresent: Boolean(metadata.pathPresent), queryFieldCount: metadata.queryFieldCount ?? 0, hasSensitiveQuery: Boolean(metadata.hasSensitiveQuery), boundaryOnly: true };
  if (kind === "http_call") return { method: metadata.method, urlResolution: metadata.urlResolution ?? "unbounded", normalizedPath: metadata.normalizedPath ?? "/{u0}", ...(metadata.endpointId ? { endpointId: metadata.endpointId } : {}), queryFieldCount: metadata.queryFieldCount ?? 0, hasSensitiveQuery: Boolean(metadata.hasSensitiveQuery) };
  if (kind === "request_payload") return { payloadKinds: [...new Set(metadata.payloadKinds ?? [])].sort(), bodyShape: metadata.bodyShape ?? "none", bodyFieldCount: metadata.bodyFieldCount ?? 0, queryFieldCount: metadata.queryFieldCount ?? 0, hasSensitiveFields: Boolean(metadata.hasSensitiveFields) };
  return {};
}

function minimizedEdgeMetadata(kind, metadata = {}) {
  if (kind === "carries") return { payloadKinds: [...new Set(metadata.payloadKinds ?? [])].sort() };
  if (kind === "resolves_to") return { resolutionTier: metadata.resolutionTier ?? "unbounded", ...(metadata.targetRepository ? { targetRepository: metadata.targetRepository } : {}) };
  return {};
}

export class FragmentBuilder {
  constructor(root, repository) {
    this.root = root;
    this.repository = repository;
    this.routes = new Map(); this.nodes = new Map(); this.edges = new Map();
    this.diagnostics = new Map(); this.boundedUrlProofs = new Map();
  }
  addRoute(route) {
    const normalized = { ...route, source: minimizedSource({ repository: this.repository, ...route.source }) };
    this.routes.set(route.key, normalized);
    this.addNode({ key: route.key, kind: "frontend_route", label: route.path, source: normalized.source, confidence: route.confidence ?? 0.9, metadata: { framework: route.framework, declaredPath: route.path, ...(route.metadata ?? {}) } });
  }
  addNode(node) {
    const normalized = { source: minimizedSource({ repository: this.repository, ...(node.source ?? {}) }), confidence: 0.8, metadata: {}, evidenceKind: "inferred", reason: defaultReason(node.kind), ...node };
    normalized.source = minimizedSource({ repository: this.repository, ...normalized.source });
    normalized.identity ??= node.key;
    normalized.metadata = minimizedNodeMetadata(normalized.kind, normalized.metadata ?? {});
    normalized.evidenceKind = normalized.evidenceKind === "unresolved" ? "unresolved" : "inferred";
    if (!(EVIDENCE_REASONS[normalized.evidenceKind] ?? []).includes(normalized.reason)) normalized.reason = defaultReason(normalized.kind);
    const current = this.nodes.get(node.key);
    if (!current || normalized.confidence > current.confidence) this.nodes.set(node.key, normalized);
    return normalized.key;
  }
  addEdge(edge) {
    const id = `${edge.source}->${edge.kind}->${edge.target}`;
    if (!this.edges.has(id)) this.edges.set(id, { confidence: 0.8, evidenceKind: "inferred", reason: "ast_call", ...edge, metadata: minimizedEdgeMetadata(edge.kind, edge.metadata) });
  }
  addDiagnostic(diagnostic) {
    if (!DIAGNOSTIC_CODES.includes(diagnostic.code) || !(diagnostic.code in DIAGNOSTIC_SPECS)) {
      throw new Error("invalid frontend diagnostic code");
    }
    const spec = DIAGNOSTIC_SPECS[diagnostic.code];
    const normalized = {
      code: diagnostic.code,
      severity: spec.severity,
      message: spec.message,
      repository: this.repository,
    };
    if (diagnostic.source?.path) {
      normalized.source = minimizedSource({
        repository: this.repository,
        ...diagnostic.source,
      });
    }
    const id = diagnosticId(normalized);
    this.diagnostics.set(id, { id, ...normalized });
  }
  unresolved(keyParts, _label, source, reason) {
    const key = `unresolved:${keyParts.join(":")}`;
    this.addNode({ key, kind: "unresolved_target", label: "Unresolved", layer: "unresolved", source, evidenceKind: "unresolved", reason, confidence: 0.3, metadata: {} });
    return key;
  }
  fragment() {
    for (const edge of this.edges.values()) {
      if (this.nodes.has(edge.target)) continue;
      const source = this.nodes.get(edge.source)?.source ?? {};
      this.addNode({
        key: edge.target,
        kind: "unresolved_target",
        label: "Unresolved",
        layer: "unresolved",
        source,
        evidenceKind: "unresolved",
        reason: "referenced_target_missing",
        confidence: 0.25,
        metadata: {},
      });
    }
    const fragment = {
      adapter: ADAPTER,
      adapterVersion: ADAPTER_VERSION,
      repository: this.repository,
      routes: [...this.routes.values()].map(({ key, path, framework }) => ({ key, path, framework })).sort(byKey),
      nodes: [...this.nodes.values()].sort(byKey),
      edges: [...this.edges.values()].sort((a, b) => `${a.source}:${a.kind}:${a.target}`.localeCompare(`${b.source}:${b.kind}:${b.target}`)),
      diagnostics: [...this.diagnostics.values()].sort((a, b) => a.id.localeCompare(b.id)),
      boundedUrlProofs: [...this.boundedUrlProofs.values()].sort((a, b) => (
        a.callKey.localeCompare(b.callKey) || a.normalizedPath.localeCompare(b.normalizedPath)
      )),
    };
    if (fragment.boundedUrlProofs.length > URL_PROOF_LIMITS.proofs
      || fragment.boundedUrlProofs.some((proof) => !validateBoundedUrlProof(proof))) {
      throw new Error("invalid bounded URL proof");
    }
    return JSON.parse(JSON.stringify(fragment));
  }
}
function byKey(a, b) { return a.key.localeCompare(b.key); }
