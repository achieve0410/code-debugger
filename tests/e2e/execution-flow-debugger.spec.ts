import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, readdir, unlink } from "node:fs/promises";
import { join } from "node:path";
import { buildFlowOutline, buildRoutePresetPositions, cytoscapeStyles, filterFlowOutline, normalizeOutlineQuery, OUTLINE_PAGE_SIZE, validConverterSegmentIndex, type GraphNode, type GraphEdge, type GraphTokens } from "../../web/src/App";

test.use({ trace: "off", screenshot: "off", video: "off" });

const capabilityHeaderBytes = Buffer.from("x-kg-debugger-capability", "ascii");
let capabilityArtifactBytes: Buffer | null = null;

async function scanCapabilityArtifacts(directory: string): Promise<void> {
  let leaked = false;
  const scan = async (path: string): Promise<void> => {
    for (const entry of await readdir(path, { withFileTypes: true })) {
      const child = join(path, entry.name);
      if (entry.isDirectory()) await scan(child);
      else if (entry.isFile()) {
        const bytes = await readFile(child);
        const normalized = Buffer.from(bytes.map((byte) => byte >= 65 && byte <= 90 ? byte + 32 : byte));
        if ((capabilityArtifactBytes !== null && bytes.includes(capabilityArtifactBytes)) || normalized.includes(capabilityHeaderBytes)) {
          await unlink(child);
          leaked = true;
        }
      }
    }
  };
  try {
    await scan(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  if (leaked) throw new Error("capability_artifact_leak");
}

function assertNoCapabilityArtifacts(value: unknown): void {
  const serialized = JSON.stringify(value);
  expect(serialized).not.toMatch(
    /(x-analyzer-capability|analyzer-capability|capability-value)/i,
  );
}

test.afterEach(async ({ page }, testInfo) => {
  try {
    if (/^https?:\/\//.test(page.url())) {
      const inventory = {
        bodyText: await page.locator("body").textContent(),
        storage: await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })),
      };
      assertNoCapabilityArtifacts(inventory);
    }
    await scanCapabilityArtifacts(testInfo.outputDir);
  } finally {
    capabilityArtifactBytes = null;
  }
});

const digest = (...parts: unknown[]) => createHash("sha256").update(JSON.stringify(parts), "utf8").digest("hex");
const nodeId = (path: string, kind: string, identityKey: string) => `n_${digest("node", "web", path, kind, identityKey)}`;
const edgeId = (source: string, target: string, kind: string) => `e_${digest("edge", source, target, kind)}`;
const inferred = { kind: "inferred", adapter: "e2e", adapterVersion: "1", reason: "ast_call" };
const unresolved = { kind: "unresolved", adapter: "e2e", adapterVersion: "1", reason: "dynamic_target_unproven" };
const observedEndpoint = { kind: "observed", adapter: "e2e", adapterVersion: "1", reason: "runtime_coherent_endpoint", eventId: "22222222-2222-4222-8222-222222222222", timestamp: "2026-01-01T00:00:00.000Z" };
const observedResolution = { ...observedEndpoint, reason: "runtime_coherent_resolution" };
const repositorySetId = "b".repeat(64);
const capability = "e2e-capability-is-never-rendered";

type NodeSpec = readonly [kind: string, identityKey: string, label: string, layer: string, path: string, metadata: Record<string, unknown>];
const specs: NodeSpec[] = [
  ["frontend_route", "/", "/", "frontend", "src/routes.tsx", { framework: "react", declaredPath: "/" }],
  ["page", "Home", "Home", "frontend", "src/home.tsx", { frameworkOwners: ["react"] }],
  ["ui_event", "load", "load", "frontend", "src/home.tsx", { frameworkOwners: ["react"], eventKind: "click", elementKind: "button", modifiers: [] }],
  ["function", "loadItems", "loadItems", "frontend", "src/home.tsx", { frameworkOwners: ["react"] }],
  ["http_call", "GET /api/items", "GET /api/items", "http", "src/api.ts", { method: "GET", urlResolution: "literal", normalizedPath: "/api/items", endpointId: "GET web:/api/items", queryFieldCount: 0, hasSensitiveQuery: false, targetRepository: "web" }],
  ["request_payload", "items-payload", "Request payload", "http", "src/api.ts", { payloadKinds: ["body"], bodyShape: "object", bodyFieldCount: 0, queryFieldCount: 0, hasSensitiveFields: false }],
  ["django_url_pattern", "items", "items", "backend", "server/urls.py", { declaredPath: "/api/items", normalizedPath: "/api/items", endpointId: "GET /api/items", converters: [] }],
  ["django_view", "item_list", "item_list", "backend", "server/views.py", { pythonQualifiedName: "server.views.item_list" }],
  ["function", "list_active_items", "list_active_items", "backend", "server/views.py", { pythonQualifiedName: "server.views.list_active_items" }],
  ["query_boundary", "filter", "filter", "data", "server/views.py", { operation: "filter", modelQualifiedName: "server.models.Item" }],
  ["model", "Item", "Item", "data", "server/models.py", { pythonQualifiedName: "server.models.Item" }],
  ["external_service", "api.example.test", "GET https://api.example.test:443", "external", "src/api.ts", { method: "GET", scheme: "https", host: "api.example.test", port: 443, pathPresent: true, queryFieldCount: 0, hasSensitiveQuery: false, boundaryOnly: true }],
  ["unresolved_target", "dynamic-target", "Unresolved", "unresolved", "src/home.tsx", { reasonCode: "dynamic_target_unproven" }]
];
const nodes = specs.map(([kind, identityKey, label, layer, path, metadata]) => ({ id: nodeId(path, kind, identityKey), kind, identityKey, label, layer, source: { repository: "web", path, line: 1 }, evidence: kind === "unresolved_target" ? [unresolved] : [inferred], confidence: 0.9, metadata })).sort((a, b) => a.id.localeCompare(b.id));
const byLabel = Object.fromEntries(nodes.map((node) => [node.label, node]));
const edge = (source: string, target: string, kind: string, metadata: Record<string, unknown> = {}, evidence = [inferred]) => ({ id: edgeId(source, target, kind), source, target, kind, evidence, confidence: 0.9, metadata });
const edges = [
  edge(byLabel["/"].id, byLabel.Home.id, "renders"), edge(byLabel.Home.id, byLabel.load.id, "contains"), edge(byLabel.load.id, byLabel.loadItems.id, "handles"),
  edge(byLabel.loadItems.id, byLabel["GET /api/items"].id, "calls"), edge(byLabel.loadItems.id, byLabel.Unresolved.id, "calls", {}, [unresolved]), edge(byLabel.loadItems.id, byLabel["GET https://api.example.test:443"].id, "calls"),
  edge(byLabel["GET /api/items"].id, byLabel["Request payload"].id, "carries", { payloadKinds: ["body"] }),
  edge(byLabel["Request payload"].id, byLabel.items.id, "resolves_to", { resolutionTier: "exact_endpoint", targetRepository: "web" }), edge(byLabel.items.id, byLabel.item_list.id, "resolves_to", { resolutionTier: "declared_path" }),
  edge(byLabel.item_list.id, byLabel.list_active_items.id, "invokes"), edge(byLabel.list_active_items.id, byLabel.filter.id, "accesses"), edge(byLabel.filter.id, byLabel.Item.id, "accesses")
].sort((a, b) => a.id.localeCompare(b.id));
const routeId = `r_${digest("web", "react", "/", byLabel["/"].id)}`;
const staticGraph = { schemaVersion: 2 as const, project: "e2e-project", repositorySetId, repositories: [{ namespace: "web" }], routes: [{ id: routeId, repository: "web", framework: "react", path: "/", nodeId: byLabel["/"].id }], nodes, edges, diagnostics: [] };
const transientGraph = { ...staticGraph, nodes: staticGraph.nodes.map((node) => node.id === byLabel.items.id ? { ...node, evidence: [inferred, observedEndpoint] } : node), edges: staticGraph.edges.map((item) => item.target === byLabel.items.id ? { ...item, evidence: [inferred, observedResolution] } : item) };
const runtimeDiagnostic = (code: string, severity: string, message: string, references: Record<string, unknown> = {}) => ({ id: `d_${digest(JSON.stringify(code), JSON.stringify(severity), JSON.stringify(message), ...["repository", "source", "nodeId", "edgeId", "candidateIds", "eventId"].map((key) => JSON.stringify(references[key] ?? null)))}`, code, severity, message, ...references });
const runtimeDiagnostics = [
  runtimeDiagnostic("runtime_capture_empty", "info", "The selected runtime capture contains no events."),
  runtimeDiagnostic("runtime_event_unmatched", "warning", "The runtime event did not match one canonical flow.", { eventId: observedEndpoint.eventId }),
  runtimeDiagnostic("runtime_event_ambiguous", "warning", "The runtime event matched multiple canonical flows.", { candidateIds: [byLabel.items.id], eventId: observedEndpoint.eventId }),
  runtimeDiagnostic("runtime_identity_conflict", "warning", "The runtime event identities do not describe one canonical flow.", { candidateIds: [byLabel.items.id], eventId: observedEndpoint.eventId })
] as const;
const dynamicConverterGraph = (endpointId: string | undefined, direct = false) => {
  const http = byLabel["GET /api/items"];
  const payload = byLabel["Request payload"];
  const target = byLabel.items;
  const nodes = staticGraph.nodes.filter((node) => !direct || node.id !== payload.id).map((node) => {
    if (node.id === http.id) return { ...node, label: "GET /api/items/{p0}", metadata: { ...node.metadata, urlResolution: "bounded_template", normalizedPath: "/api/items/{p0}", ...(endpointId === undefined ? { endpointId: undefined } : { endpointId }) } };
    if (node.id === target.id) return { ...node, metadata: { ...node.metadata, declaredPath: "/api/items/<int:item_id>", normalizedPath: "/api/items/{p0}", endpointId: "GET /api/items/{p0}", converters: [{ name: "item_id", kind: "int", segmentIndex: 2 }] } };
    return node;
  }).sort((a, b) => a.id.localeCompare(b.id));
  const resolution = edge(direct ? http.id : payload.id, target.id, "resolves_to", { resolutionTier: "dynamic_converter", targetRepository: "web" });
  const edges = staticGraph.edges.filter((item) => item.target !== target.id && (!direct || item.source !== http.id && item.target !== payload.id)).concat(resolution).sort((a, b) => a.id.localeCompare(b.id));
  return { ...staticGraph, nodes, edges };
};
const unresolvedResolutionGraph = (bounded: boolean, reasonCode = "dynamic_target_unproven", candidateIds?: string[], residue?: "endpointId" | "targetRepository") => {
  const http = byLabel["GET /api/items"];
  const payload = byLabel["Request payload"];
  const unresolvedTarget = byLabel.Unresolved;
  const unresolvedEvidence = [{ ...unresolved, reason: reasonCode }];
  const nodes = staticGraph.nodes.map((node) => {
    if (node.id === http.id) {
      const metadata = bounded
        ? { ...node.metadata, urlResolution: "bounded_template", normalizedPath: "/api/items/{p0}", ...(residue === "endpointId" ? { endpointId: "GET web:/api/items/{p0}" } : { endpointId: undefined }) }
        : { ...node.metadata, ...(residue === "endpointId" ? {} : { endpointId: undefined }) };
      return { ...node, label: `GET ${String(metadata.normalizedPath)}`, metadata };
    }
    if (node.id === unresolvedTarget.id) return { ...node, evidence: unresolvedEvidence, metadata: { ...node.metadata, reasonCode, ...(candidateIds === undefined ? {} : { candidateIds }) } };
    return node;
  }).sort((a, b) => a.id.localeCompare(b.id));
  const metadata = { resolutionTier: "unbounded", ...(residue === "targetRepository" ? { targetRepository: "web" } : {}) };
  const edges = staticGraph.edges.filter((item) => !(item.source === payload.id && item.kind === "resolves_to")).concat(edge(payload.id, unresolvedTarget.id, "resolves_to", metadata, unresolvedEvidence)).sort((a, b) => a.id.localeCompare(b.id));
  return { ...staticGraph, nodes, edges };
};
const forgedBoundedResolutionGraph = (resolutionTier: "exact_endpoint" | "configured_base") => ({
  ...dynamicConverterGraph("GET web:/api/items/{p0}"),
  edges: dynamicConverterGraph("GET web:/api/items/{p0}").edges.map((item) => item.kind === "resolves_to" && item.target === byLabel.items.id ? { ...item, metadata: { resolutionTier, targetRepository: "web" } } : item)
});
const config = (runtimeEnabled = true, snapshotId = repositorySetId) => ({ project: "e2e-project", runtimeEnabled, schemaVersion: 2, repositorySetId: snapshotId, repositories: [{ namespace: "web", displayRoot: "." }], repoRoots: ["."], compatibilityWarnings: ["repoRoots_deprecated_v2"], mutationCapability: capability });
const encodedDisplayRootAttacks = [
  "packages/%2e%2e/web",
  "packages/%2fweb",
  "packages/%5cweb",
  "%2Fabsolute",
  "%43%3Awork",
  "%2F%2Fserver/share",
  "external%3Aweb"
].flatMap((value) => {
  const variants: string[] = [];
  let encoded = value;
  for (let depth = 1; depth <= 9; depth += 1) {
    variants.push(encoded);
    encoded = encoded.replaceAll("%", "%25");
  }
  return variants;
});

async function mock(page: Page, options: { graph?: unknown; analyzeGraph?: unknown; runtimeEnabled?: boolean; snapshotId?: string; activeConfig?: unknown } = {}) {
  capabilityArtifactBytes = Buffer.from(capability, "utf8");
  await page.route("**/api/health", (route) => route.fulfill({ json: { ok: true, status: "ready" } }));
  await page.route("**/api/config", (route) => route.fulfill({ json: options.activeConfig ?? config(options.runtimeEnabled, options.snapshotId) }));
  await page.route("**/api/graph", (route) => route.fulfill({ json: options.graph ?? staticGraph }));
  await page.route("**/api/analyze", (route) => route.fulfill({ json: options.analyzeGraph ?? transientGraph }));
}
const graphNode = (page: Page, label: keyof typeof byLabel) => page.getByTestId(`graph-node-${byLabel[label].id}`);
const canonicalCssToken = (value: string) => {
  if (/^#[0-9a-f]{3}$/i.test(value)) return `#${[...value.slice(1)].map((part) => part.repeat(2)).join("")}`;
  if (/^(?:\d+(?:\.\d+)?|\.\d+)s$/.test(value)) return `${Math.round(Number(value.slice(0, -1)) * 1000)}ms`;
  return value;
};
test("accepts canonical IPv6 authorities and rejects forged converter and function records", async ({ page }) => {
  const ipv6 = { id: nodeId("src/ipv6.ts", "external_service", "ipv6"), kind: "external_service", identityKey: "ipv6", label: "GET https://[2001:db8::1]:443", layer: "external", source: { repository: "web", path: "src/ipv6.ts", line: 1 }, evidence: [inferred], confidence: 0.9, metadata: { method: "GET", scheme: "https", host: "2001:db8::1", port: 443, pathPresent: true, queryFieldCount: 0, hasSensitiveQuery: false, boundaryOnly: true } };
  await mock(page, { graph: { ...staticGraph, nodes: [...staticGraph.nodes, ipv6].sort((a, b) => a.id.localeCompare(b.id)) } }); await page.goto("/");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  const forged = { ...staticGraph, nodes: staticGraph.nodes.map((node) => node.kind === "django_url_pattern" ? { ...node, metadata: { ...node.metadata, converters: [{ name: "forged", kind: "int", segmentIndex: 0 }] } } : node.kind === "function" && node.layer === "frontend" ? { ...node, metadata: {} } : node) };
  await page.unroute("**/api/graph"); await page.route("**/api/graph", (route) => route.fulfill({ json: forged })); await page.reload();
  await expect(page.getByTestId("graph-state-invalid")).toBeVisible();
});
test("accepts only repository-qualified dynamic converter endpoint identities for direct and payload owners", async ({ page }) => {
  const canonical = "GET web:/api/items/{p0}";
  await mock(page, { graph: dynamicConverterGraph(canonical) }); await page.goto("/");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await page.unroute("**/api/graph"); await page.route("**/api/graph", (route) => route.fulfill({ json: dynamicConverterGraph(canonical, true) })); await page.reload();
  await expect(page.getByTestId("graph-canvas")).toBeVisible();

  for (const endpointId of [undefined, "GET /api/items/{p0}", "GET api:/api/items/{p0}", "POST web:/api/items/{p0}", "GET web:/api/other/{p0}"]) {
    await page.unroute("**/api/graph"); await page.route("**/api/graph", (route) => route.fulfill({ json: dynamicConverterGraph(endpointId) })); await page.reload();
    await expect(page.getByTestId("graph-state-invalid")).toBeVisible();
  }
});
test("enforces the browser converter segmentIndex boundary directly", () => {
  expect(validConverterSegmentIndex(255, 255)).toBe(true);
  for (const value of [-1, 256, true, "255", 1.5]) {
    expect(validConverterSegmentIndex(value, 255)).toBe(false);
  }
  expect(validConverterSegmentIndex(254, 255)).toBe(false);
});
test("accepts unresolved candidates from GET and Analyze, but rejects forged concrete and final-state residue", async ({ page }) => {
  const positives = [
    unresolvedResolutionGraph(false),
    unresolvedResolutionGraph(false, "url_target_unmatched"),
    unresolvedResolutionGraph(true),
    unresolvedResolutionGraph(true, "url_target_ambiguous", [byLabel.items.id])
  ];
  for (const [index, graph] of positives.entries()) {
    await mock(page, { graph, analyzeGraph: graph }); await page.goto("/");
    await expect(page.getByTestId("graph-canvas"), `positive resolution graph ${index}`).toBeVisible();
    await page.getByTestId("analyze-button").click();
    await expect(page.getByTestId("graph-canvas")).toBeVisible();
    await expect(page.getByTestId("graph-message")).toHaveCount(0);
  }
  for (const resolutionTier of ["exact_endpoint", "configured_base"] as const) {
    await mock(page, { graph: staticGraph, analyzeGraph: forgedBoundedResolutionGraph(resolutionTier) }); await page.goto("/");
    await expect(page.getByTestId("graph-canvas")).toBeVisible();
    await page.getByTestId("analyze-button").click();
    await expect(page.getByTestId("graph-message")).toContainText("invalid_graph_response");
    await mock(page, { graph: forgedBoundedResolutionGraph(resolutionTier) }); await page.reload();
    await expect(page.getByTestId("graph-state-invalid")).toBeVisible();
  }
  for (const residue of ["endpointId", "targetRepository"] as const) {
    const forged = unresolvedResolutionGraph(false, "dynamic_target_unproven", undefined, residue);
    await mock(page, { graph: staticGraph, analyzeGraph: forged }); await page.goto("/");
    await expect(page.getByTestId("graph-canvas")).toBeVisible();
    await page.getByTestId("analyze-button").click();
    await expect(page.getByTestId("graph-message"), `${residue} Analyze residue`).toContainText("invalid_graph_response");
    await mock(page, { graph: forged }); await page.reload();
    await expect(page.getByTestId("graph-state-invalid"), `${residue} GET residue`).toBeVisible();
  }
});
test("accepts only canonical display roots and matching external repository labels", async ({ page }) => {
  for (const displayRoot of [".", "packages/web", "packages/web-client_2", "external:web"]) {
    await mock(page, { activeConfig: { ...config(), repositories: [{ namespace: "web", displayRoot }], repoRoots: [displayRoot] } }); await page.goto("/");
    await expect(page.getByTestId("graph-canvas")).toBeVisible();
  }
  for (const displayRoot of ["/absolute", "C:\\work", "\\\\server\\share", "packages\\web", "packages/../web", "packages/%", "packages/%2", "packages/%zz", "external:", "external:other", "external:web/extra", ...encodedDisplayRootAttacks]) {
    await mock(page, { activeConfig: { ...config(), repositories: [{ namespace: "web", displayRoot }], repoRoots: [displayRoot] } }); await page.goto("/");
    await expect(page.getByTestId("graph-state-unavailable"), displayRoot).toBeVisible();
  }
});


test("uses an exact GraphSnapshotV2 static fixture with stable workbench landmarks and entity hooks", async ({ page }) => {
  await mock(page); await page.goto("/");
  await expect(page.locator('header[aria-label="Execution Evidence Workbench commands"]')).toBeVisible();
  await expect(page.getByTestId("snapshot-status")).toHaveText("Static snapshot");
  await expect(page.getByTestId("workbench")).toHaveAttribute("aria-label", "Execution Evidence Workbench");
  await expect(page.getByTestId("routes-region")).toHaveAttribute("aria-label", "Routes");
  await expect(page.getByTestId("graph-region")).toHaveAttribute("aria-labelledby", "graph-heading");
  await expect(page.getByTestId("flow-outline")).toHaveAttribute("aria-labelledby", "flow-outline-heading");
  await expect(page.getByTestId("inspector")).toHaveAttribute("aria-labelledby", "inspector-heading");
  await expect(page.getByTestId("graph-canvas")).toHaveAttribute("aria-label", "Visual execution graph. Use Flow Outline after the canvas to inspect every visible node and edge.");
  await expect(page.getByTestId("route-option-/")).toHaveAccessibleName("/; react; repository web");
  await page.getByRole("button", { name: "Expand all" }).click();
  await expect(graphNode(page, "Request payload")).toBeVisible();
  await expect(page.getByTestId(`graph-edge-${edgeId(byLabel["GET /api/items"].id, byLabel["Request payload"].id, "carries")}`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Node: /; kind: frontend route; evidence: Inferred; source: web/src/routes.tsx:1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edge: carries; from GET /api/items (http call, web/src/api.ts) to Request payload (request payload, web/src/api.ts); evidence: Inferred" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(capability);
  expect(await page.evaluate(() => `${JSON.stringify(localStorage)}${JSON.stringify(sessionStorage)}`)).not.toContain(capability);
});

test("independently tolerates health failure and rejects malformed config, envelopes, and transport", async ({ page }) => {
  await mock(page);
  await page.route("**/api/health", (route) => route.abort("failed"));
  await page.goto("/"); await expect(page.getByLabel("Debugger status")).toContainText("API unavailable");
  await mock(page); await page.route("**/api/config", (route) => route.fulfill({ json: { malformed: true } }));
  await page.reload(); await expect(page.getByTestId("graph-state-unavailable")).toBeVisible();
  await mock(page); await page.route("**/api/graph", (route) => route.fulfill({ status: 200, body: "not-json" }));
  await page.reload(); await expect(page.getByTestId("graph-state-unavailable")).toBeVisible();
  await mock(page); await page.route("**/api/graph", (route) => route.abort("connectionrefused"));
  await page.reload(); await expect(page.getByTestId("graph-state-unavailable")).toBeVisible();
});
test("accepts only exact server error envelopes", async ({ page }) => {
  const errors = [
    [400, "invalid_host_header"], [400, "invalid_origin_header"], [400, "invalid_capability_header"], [400, "invalid_selector"], [400, "unsupported_transfer_encoding"], [400, "invalid_content_length"], [400, "invalid_json_body"], [400, "invalid_request"],
    [403, "origin_forbidden"], [403, "mutation_forbidden"], [403, "runtime_disabled"], [404, "snapshot_not_found", { action: "analyze" }], [404, "not_found"], [405, "method_not_allowed"], [408, "request_timeout"],
    [409, "snapshot_incompatible", { code: "snapshot_schema_unsupported", action: "reanalyze" }], [409, "runtime_unavailable", { action: "analyze_without_capture" }], [411, "length_required"], [413, "request_too_large"], [415, "unsupported_media_type"], [421, "misdirected_request"], [422, "snapshot_invalid", { action: "delete_or_reanalyze" }], [500, "internal_error"]
  ] as const;
  await mock(page);
  for (const [status, error, extra = {}] of errors) {
    await page.unroute("**/api/graph");
    await page.route("**/api/graph", (route) => route.fulfill({ status, json: { error, ...extra } }));
    await page.goto("/");
    await expect(page.getByTestId("graph-message")).toContainText(error);
  }
  await page.unroute("**/api/graph"); await page.route("**/api/graph", (route) => route.fulfill({ status: 400, json: { error: "missing_capability" } }));
  await page.reload(); await expect(page.getByTestId("graph-message")).toContainText("protocol_unavailable");
  await page.unroute("**/api/graph"); await page.route("**/api/graph", (route) => route.fulfill({ status: 409, json: { error: "snapshot_incompatible", code: "obsolete", action: "reanalyze" } }));
  await page.reload(); await expect(page.getByTestId("graph-message")).toContainText("protocol_unavailable");
});
test("rejects static runtime material and malformed canonical graph fields", async ({ page }) => {
  const runtimeDiagnostic = { id: `d_${digest(JSON.stringify("runtime_capture_empty"), JSON.stringify("info"), JSON.stringify("The selected runtime capture contains no events."), "null", "null", "null", "null", "null", "null")}`, code: "runtime_capture_empty", severity: "info", message: "The selected runtime capture contains no events." };
  const cases: Array<[string, unknown]> = [
    ["observed static evidence", { ...staticGraph, nodes: staticGraph.nodes.map((node) => node.id === byLabel.items.id ? { ...node, evidence: [observedEndpoint] } : node) }],
    ["runtime diagnostic", { ...staticGraph, diagnostics: [runtimeDiagnostic] }],
    ["invalid calendar timestamp", { ...transientGraph, nodes: transientGraph.nodes.map((node) => node.id === byLabel.items.id ? { ...node, evidence: [{ ...observedEndpoint, timestamp: "2026-02-30T00:00:00.000Z" }] } : node) }],
    ["malformed route", { ...staticGraph, routes: [{ ...staticGraph.routes[0], path: "/%2f" }] }],
    ["unknown metadata", { ...staticGraph, nodes: staticGraph.nodes.map((node) => node.id === byLabel.items.id ? { ...node, metadata: { ...node.metadata, unexpected: true } } : node) }],
    ["illegal tuple", { ...staticGraph, edges: staticGraph.edges.map((item) => item.kind === "renders" ? { ...item, target: byLabel.items.id } : item) }],
    ["secret project", { ...staticGraph, project: "token=not-safe" }],
    ["invalid Django endpoint method", { ...staticGraph, nodes: staticGraph.nodes.map((node) => node.kind === "django_url_pattern" ? { ...node, metadata: { ...node.metadata, endpointId: "bad /api/items" } } : node) }]
  ];
  await mock(page);
  for (const [, graph] of cases) {
    await page.unroute("**/api/graph");
    await page.route("**/api/graph", (route) => route.fulfill({ json: graph }));
    await page.goto("/");
    await expect(page.getByTestId("graph-state-invalid")).toBeVisible();
  }
});
test("Analyze derives runtime validation exclusively from the submitted capture", async ({ page }) => {
  const staticCases: Array<[string, unknown]> = [
    ["observed evidence", transientGraph],
    ...runtimeDiagnostics.map((diagnostic) => [diagnostic.code, { ...staticGraph, diagnostics: [diagnostic] }] as [string, unknown])
  ];
  await mock(page); await page.goto("/");
  for (const [, graph] of staticCases) {
    await page.unroute("**/api/analyze"); await page.route("**/api/analyze", (route) => route.fulfill({ json: graph }));
    await page.getByTestId("analyze-button").click();
    await expect(page.getByTestId("graph-message")).toContainText("invalid_graph_response");
    await expect(page.getByTestId("transient-status")).toHaveCount(0);
  }
  const runtimeCases: Array<[string, unknown]> = [
    ["observed evidence", transientGraph],
    ...runtimeDiagnostics.map((diagnostic) => [diagnostic.code, { ...staticGraph, diagnostics: [diagnostic] }] as [string, unknown])
  ];
  for (const [index, [, graph]] of runtimeCases.entries()) {
    await page.unroute("**/api/analyze"); await page.route("**/api/analyze", (route) => route.fulfill({ json: graph }));
    await page.getByTestId("runtime-capture-input").fill(`capture-${index}`);
    await page.getByTestId("analyze-button").click();
    await expect(page.getByTestId("transient-status")).toBeVisible();
    await expect(page.getByTestId("graph-message")).toHaveCount(0);
  }
});

test("analyze captures exact payload, overlays observed evidence, and refresh removes transient evidence", async ({ page }) => {
  const requests: unknown[] = []; let graphGets = 0;
  await mock(page);
  await page.route("**/api/graph", (route) => route.fulfill({ json: ++graphGets === 1 ? staticGraph : staticGraph }));
  await page.route("**/api/analyze", (route) => { requests.push(route.request().postDataJSON()); return route.fulfill({ json: transientGraph }); });
  await page.goto("/");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByTestId("inspector-summary")).toHaveText("Route SummaryRoute path/FrameworkreactRepositorywebSnapshot modeStatic snapshotEvidence filtersObserved, Inferred, UnresolvedLayer filtersfrontend, http, backend, data, external, unresolvedRoute nodes5 of 13 route nodesRoute edges4 of 12 route edgesGraph and repository diagnosticsNo graph or repository diagnostics.");
  await page.getByTestId("runtime-capture-input").fill("capture:one");
  await expect(page.getByTestId("runtime-capture-input")).toHaveValue("capture:one");
  await page.getByTestId("analyze-button").click();
  await expect.poll(() => requests).toEqual([{ runtimeCaptureId: "capture:one" }]);
  await expect(page.getByTestId("transient-status")).toBeVisible();
  await page.getByRole("button", { name: "Expand all" }).click();
  await graphNode(page, "items").click();
  await expect(page.getByTestId("inspector-identity")).toHaveText("IdentityLabelitemsKinddjango url patternLayerbackendConfidence90.0%");
  await expect(page.getByTestId("inspector-source")).toHaveText("SourceRepositorywebRelative pathserver/urls.pyLines1SymbolNot reported");
  await expect(page.getByTestId("inspector-evidence")).toHaveText("EvidenceSummaryInferred + ObservedEvidence record 1Kind: Inferred; Adapter: e2e; Adapter version: 1; Basis: ast callEvidence record 2Kind: Observed; Adapter: e2e; Adapter version: 1; Basis: runtime coherent endpoint");
  await expect(page.getByTestId("inspector-metadata")).toHaveText("MetadataDeclared path/api/itemsNormalized path/api/itemsEndpoint IDGET /api/itemsConvertersNone");
  await expect(page.getByTestId("inspector-diagnostics")).toHaveText("DiagnosticsDiagnosticsNo diagnostics for this selection.");
  await expect(page.getByTestId("inspector")).not.toContainText(observedEndpoint.eventId);
  const resolvesTo = edgeId(byLabel["Request payload"].id, byLabel.items.id, "resolves_to");
  await page.getByTestId(`graph-edge-${resolvesTo}`).click();
  await expect(page.getByTestId("inspector-relationship")).toHaveText("RelationshipKindresolves toSource labelRequest payloadSource kindrequest payloadTarget labelitemsTarget kinddjango url patternConfidence90.0%");
  await expect(page.getByTestId("inspector-evidence")).toHaveText("EvidenceSummaryInferred + ObservedEvidence record 1Kind: Inferred; Adapter: e2e; Adapter version: 1; Basis: ast callEvidence record 2Kind: Observed; Adapter: e2e; Adapter version: 1; Basis: runtime coherent resolution");
  await expect(page.getByTestId("inspector-metadata")).toHaveText("MetadataResolution tierexact endpointTarget repositoryweb");
  await page.getByTestId("refresh-button").click();
  await expect(page.getByTestId("transient-status")).toHaveCount(0);
  await graphNode(page, "items").click();
  await expect(page.getByTestId("inspector-evidence")).toHaveText("EvidenceSummaryInferredEvidence record 1Kind: Inferred; Adapter: e2e; Adapter version: 1; Basis: ast call");
  expect(graphGets).toBe(2);
});
test("Inspector orders graph and repository diagnostics without rendering references", async ({ page }) => {
  const repositoryDiagnostic = runtimeDiagnostic("frontend_analyzer_unavailable", "warning", "Frontend analyzer is unavailable.", { repository: "web" });
  const diagnostics = [repositoryDiagnostic, runtimeDiagnostics[0]].sort((left, right) => left.id.localeCompare(right.id));
  const graph = { ...staticGraph, diagnostics: [repositoryDiagnostic] };
  const analyzed = { ...transientGraph, diagnostics };
  await mock(page, { graph, analyzeGraph: analyzed });
  await page.goto("/");
  await expect(page.getByTestId("inspector-summary")).toContainText("warning: frontend analyzer unavailable. Frontend analyzer is unavailable.");
  await page.getByTestId("runtime-capture-input").fill("capture-diagnostics");
  await page.getByTestId("analyze-button").click();
  const summary = page.getByTestId("inspector-summary");
  const expected = diagnostics.map((diagnostic) => `${diagnostic.severity}: ${diagnostic.code.replaceAll("_", " ")}. ${diagnostic.message}`);
  await expect(summary).toContainText(expected[0]);
  await expect(summary).toContainText(expected[1]);
  const text = await summary.textContent();
  expect(text!.indexOf(expected[0])).toBeLessThan(text!.indexOf(expected[1]));
  await expect(summary).not.toContainText(runtimeDiagnostics[1].eventId);
  await expect(summary).not.toContainText(byLabel.items.id);
});

test("retains a compatible stale graph for 409 and 422 but clears repository-set mismatches", async ({ page }) => {
  let get = 0; await mock(page);
  await page.route("**/api/graph", (route) => route.fulfill(++get === 1 ? { json: staticGraph } : get === 2 ? { status: 409, json: { error: "snapshot_incompatible", code: "snapshot_schema_unsupported", action: "reanalyze" } } : get === 3 ? { status: 422, json: { error: "snapshot_invalid", action: "delete_or_reanalyze" } } : { json: { ...staticGraph, repositorySetId: "c".repeat(64) } }));
  await page.goto("/");
  for (const expected of ["snapshot_incompatible", "snapshot_invalid"]) { await page.getByTestId("refresh-button").click(); await expect(page.getByTestId("graph-message")).toContainText(expected); await expect(page.getByTestId("graph-canvas")).toBeVisible(); }
  await page.getByTestId("refresh-button").click(); await expect(page.getByTestId("graph-state-incompatible")).toBeVisible();
});

test("startup, analyze, and refresh races retain the latest compatible response", async ({ page }) => {
  let releaseGraph!: () => void; const delayed = new Promise<void>((resolve) => { releaseGraph = resolve; });
  await mock(page); await page.route("**/api/graph", async (route) => { await delayed; await route.fulfill({ json: staticGraph }); });
  await page.goto("/"); await page.getByTestId("refresh-button").click(); releaseGraph(); await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await page.getByTestId("runtime-capture-input").fill("race-capture"); await page.getByTestId("analyze-button").click(); await expect(page.getByTestId("transient-status")).toBeVisible();
});

test("browser and Django route fallback reset route scope and preserve the canvas instance", async ({ page }) => {
  await mock(page); await page.goto("/deep/browser/path"); await expect(page.getByTestId("graph-canvas")).toBeVisible();
  const canvas = page.getByTestId("graph-canvas"); await canvas.evaluate((element) => element.setAttribute("data-instance-marker", "stable"));
  await page.getByTestId("route-search").fill("missing"); await expect(page.getByTestId("route-list")).not.toContainText("react");
  await page.getByTestId("route-search").fill(""); await page.getByTestId("route-option-/").click(); await expect(canvas).toHaveAttribute("data-instance-marker", "stable");
});

test("refresh rebinds a retained selection and clears a removed node or edge selection", async ({ page }) => {
  const replacement = { ...staticGraph, nodes: staticGraph.nodes.map((node) => node.id === byLabel["GET https://api.example.test:443"].id ? { ...node, confidence: 0.8 } : node) };
  const removed = { ...replacement, nodes: replacement.nodes.filter((node) => node.id !== byLabel["GET https://api.example.test:443"].id), edges: replacement.edges.filter((edge) => edge.source !== byLabel["GET https://api.example.test:443"].id && edge.target !== byLabel["GET https://api.example.test:443"].id) };
  let get = 0; await mock(page);
  await page.route("**/api/graph", (route) => route.fulfill({ json: [staticGraph, replacement, removed][get++] }));
  await page.goto("/"); await page.getByRole("button", { name: "Expand all" }).click(); await graphNode(page, "GET https://api.example.test:443").click();
  await page.getByTestId("refresh-button").click(); await expect(page.getByTestId("selected-detail")).toContainText("GET https://api.example.test:443");
  await page.getByTestId("refresh-button").click(); await expect(page.getByTestId("selected-detail")).toContainText("Route Summary");
});
test("scope expansion, collapse, cyclic/disconnected input, selection rebind, and filters are deterministic", async ({ page }) => {
  const cyclic = { ...staticGraph, edges: [...staticGraph.edges, edge(byLabel.list_active_items.id, byLabel.loadItems.id, "calls")].sort((a, b) => a.id.localeCompare(b.id)) };
  await mock(page, { graph: cyclic }); await page.goto("/");
  await expect(page.getByTestId("visible-node-count")).toHaveText("5 nodes"); await page.getByRole("button", { name: "Expand all" }).click(); await expect(page.getByTestId("visible-node-count")).toHaveText("13 nodes");
  await graphNode(page, "Home").click(); await page.getByTestId("layer-filter-frontend").getByRole("checkbox").uncheck(); await expect(page.getByTestId("selected-detail")).toContainText("Route Summary");
  await page.getByRole("button", { name: "Collapse branches" }).click(); await expect(page.getByTestId("visible-node-count")).toHaveText("1 nodes");
  await page.getByTestId("evidence-filter-inferred").getByRole("checkbox").uncheck(); await expect(page.getByTestId("graph-canvas")).toBeVisible();
});

test("cytoscape effective evidence/backbone styling, keyboard activation, positions, and desktop geometry remain usable", async ({ page }) => {
  const cycle = edge(byLabel.list_active_items.id, byLabel.loadItems.id, "calls");
  const staticCycle = { ...staticGraph, edges: [...staticGraph.edges, cycle].sort((a, b) => a.id.localeCompare(b.id)) };
  const observedCycle = { ...transientGraph, edges: [...transientGraph.edges, cycle].sort((a, b) => a.id.localeCompare(b.id)) };
  await mock(page, { graph: staticCycle, analyzeGraph: observedCycle }); await page.setViewportSize({ width: 1440, height: 900 }); await page.goto("/");
  const canvas = page.getByTestId("graph-canvas"); const before = await canvas.boundingBox();
  await graphNode(page, "/").focus(); await page.keyboard.press("Enter"); await expect(page.getByTestId("selected-detail")).toContainText("Kind");
  await page.getByTestId("runtime-capture-input").fill("capture-42");
  await page.getByTestId("analyze-button").click();
  await expect(page.getByTestId("transient-status")).toBeVisible();
  await page.getByRole("button", { name: "Expand all" }).click();
  const styles = await canvas.evaluate((element, ids) => {
    const cy = (element as unknown as { __cytoscapeForTests?: { $id: (id: string) => { style: (property: string) => string; hasClass: (name: string) => boolean } } }).__cytoscapeForTests;
    if (!cy) throw new Error("missing Cytoscape test instance");
    return Object.fromEntries(ids.map(([name, id, property]) => [name, { value: cy.$id(id).style(property), backbone: cy.$id(id).hasClass("backbone") }]));
  }, [
    ["observed-node", byLabel.items.id, "border-style"], ["inferred-node", byLabel["/"].id, "border-style"], ["unresolved-node", byLabel.Unresolved.id, "border-style"],
    ["observed-backbone-edge", edgeId(byLabel["Request payload"].id, byLabel.items.id, "resolves_to"), "line-style"], ["unresolved-backbone-edge", edgeId(byLabel.loadItems.id, byLabel.Unresolved.id, "calls"), "line-style"], ["inferred-non-backbone-edge", cycle.id, "line-style"]
  ] as [string, string, string][]);
  expect(styles).toEqual({
    "observed-node": { value: "solid", backbone: false }, "inferred-node": { value: "dashed", backbone: false }, "unresolved-node": { value: "dotted", backbone: false },
    "observed-backbone-edge": { value: "solid", backbone: true }, "unresolved-backbone-edge": { value: "dotted", backbone: true }, "inferred-non-backbone-edge": { value: "dashed", backbone: false }
  });
  const after = await canvas.boundingBox(); expect(after?.width).toBe(before?.width); expect(after?.height).toBe(before?.height);
  const [sidebar, stage, inspector] = await Promise.all([".sidebar", ".graph-stage", ".inspector"].map((selector) => page.locator(selector).boundingBox()));
  expect(Math.abs(stage!.width / sidebar!.width - 2)).toBeLessThan(0.08); expect(Math.abs(inspector!.width - sidebar!.width)).toBeLessThanOrEqual(2); expect(stage!.x + stage!.width).toBeLessThanOrEqual(1440);
});
test("exports deterministic outline traversal, declared-field search, collision names, and exact paging", () => {
  const outlineNodes = [
    { id: "n-root", label: "Root", kind: "frontend_route", evidence: [inferred], source: { repository: "web", path: "routes.ts" } },
    { id: "n-a", label: "Duplicate", kind: "page", evidence: [inferred], source: { repository: "web", path: "shared.tsx" } },
    { id: "n-b", label: "Duplicate", kind: "page", evidence: [inferred], source: { repository: "web", path: "shared.tsx" } },
    { id: "n-c", label: "Cycle", kind: "function", evidence: [observedEndpoint], source: { repository: "web", path: "c.ts" } },
    { id: "n-d", label: "Disconnected", kind: "model", evidence: [inferred], source: { repository: "web", path: "d.py" } },
  ] as unknown as GraphNode[];
  const outlineEdges = [
    { id: "e-1", source: "n-root", target: "n-a", kind: "renders", evidence: [inferred], confidence: 1, metadata: {} },
    { id: "e-2", source: "n-root", target: "n-b", kind: "renders", evidence: [unresolved], confidence: 1, metadata: {} },
    { id: "e-3", source: "n-a", target: "n-c", kind: "calls", evidence: [observedEndpoint], confidence: 1, metadata: {} },
    { id: "e-4", source: "n-b", target: "n-c", kind: "calls", evidence: [inferred], confidence: 1, metadata: {} },
    { id: "e-5", source: "n-c", target: "n-root", kind: "calls", evidence: [inferred], confidence: 1, metadata: {} },
  ] as unknown as GraphEdge[];
  const first = buildFlowOutline(outlineNodes, outlineEdges, "n-root");
  const second = buildFlowOutline([...outlineNodes].reverse(), [...outlineEdges].reverse(), "n-root");
  expect(first.map((entry) => `${entry.type}:${entry.id}`)).toEqual(second.map((entry) => `${entry.type}:${entry.id}`));
  expect(first).toHaveLength(10);
  expect(new Set(first.map((entry) => `${entry.type}:${entry.id}`)).size).toBe(10);
  expect(first.filter((entry) => entry.type === "node" && entry.label === "Duplicate").map((entry) => entry.accessibleName)).toEqual([
    "Node: Duplicate; kind: page; evidence: Inferred; source: web/shared.tsx; occurrence 1 of 2",
    "Node: Duplicate; kind: page; evidence: Inferred; source: web/shared.tsx; occurrence 2 of 2",
  ]);
  expect(filterFlowOutline(first, "web d.py inferred").map((entry) => entry.id)).toEqual(["n-d"]);
  expect(filterFlowOutline(first, "missing accessible prose")).toEqual([]);
  expect(normalizeOutlineQuery("  ＷＥＢ\tB.tsx  ")).toBe("web b.tsx");
  expect(() => buildFlowOutline(outlineNodes, [{ ...outlineEdges[0], target: "n-missing" }], "n-root")).toThrow("flow_outline_invalid_input");
  expect(OUTLINE_PAGE_SIZE).toBe(100);
});

test("renders exactly 100 Flow Outline controls per page for a 103-node expanded route", async ({ page }) => {
  const root = staticGraph.nodes.find((node) => node.id === byLabel["/"].id)!;
  const leaves = Array.from({ length: 102 }, (_, index) => ({
    id: nodeId(`src/pages/${index}.tsx`, "page", `Page${index}`), kind: "page", identityKey: `Page${index}`, label: `Page ${index}`, layer: "frontend", source: { repository: "web", path: `src/pages/${index}.tsx`, line: 1 }, evidence: [inferred], confidence: 0.9, metadata: { frameworkOwners: ["react"] },
  }));
  const fanout = leaves.map((leaf) => edge(root.id, leaf.id, "renders"));
  const graph = { ...staticGraph, nodes: [root, ...leaves].sort((left, right) => left.id.localeCompare(right.id)), edges: fanout.sort((left, right) => left.id.localeCompare(right.id)) };
  await mock(page, { graph }); await page.goto("/");
  await page.getByRole("button", { name: "Expand all" }).click();
  await expect(page.getByTestId("outline-count")).toHaveText("205 of 205 visible graph entities");
  await expect(page.locator("[data-outline-key]")).toHaveCount(100);
  await expect(page.getByTestId("outline-page")).toHaveText("Page 1 of 3");
  await page.getByTestId("outline-next").click();
  await expect(page.locator("[data-outline-key]")).toHaveCount(100);
  await page.getByTestId("outline-next").click();
  await expect(page.locator("[data-outline-key]")).toHaveCount(5);
  await expect(page.getByTestId("outline-previous")).toBeEnabled();
});

test("uses the frozen Cytoscape selector order and token mapping", () => {
  const tokens: GraphTokens = {
    canvas: "rgb(1, 1, 1)", nodeFill: "rgb(2, 2, 2)", nodeBorder: "rgb(3, 3, 3)", nodeLabel: "rgb(4, 4, 4)",
    edge: "rgb(5, 5, 5)", edgeLabel: "rgb(6, 6, 6)", edgeLabelBackground: "rgb(7, 7, 7)", evidenceObserved: "rgb(8, 8, 8)",
    evidenceInferred: "rgb(9, 9, 9)", evidenceUnresolved: "rgb(10, 10, 10)", backbone: "rgb(11, 11, 11)",
    selectionFill: "rgb(12, 12, 12)", selectionBorder: "rgb(13, 13, 13)", selectionEdge: "rgb(14, 14, 14)",
  };
  const styles = cytoscapeStyles(tokens);
  expect(styles.map((style) => style.selector)).toEqual(["node", "edge", ".evidence-observed", ".evidence-inferred", ".evidence-unresolved", ".backbone", "node.selected", "edge.selected"]);
  expect(styles[2].style).toMatchObject({ "border-style": "solid", "line-color": tokens.evidenceObserved });
  expect(styles[5].style).toMatchObject({ "line-color": tokens.backbone, width: "4px", "z-index": 10 });
  expect(styles[7].style).toMatchObject({ "line-color": tokens.selectionEdge, width: "5px", "z-index": 11 });
});
test("builds fail-closed canonical route-layered preset positions", () => {
  const node = (id: string) => ({ ...staticGraph.nodes[0], id, label: id }) as GraphNode;
  const graphNodes = ["n-a", "n-b", "n-c", "n-d", "n-e", "n-f"].map(node);
  const graphEdge = (id: string, source: string, target: string) => ({ ...staticGraph.edges[0], id, source, target }) as GraphEdge;
  const graphEdges = [
    graphEdge("e-01", "n-a", "n-b"), graphEdge("e-02", "n-a", "n-c"),
    graphEdge("e-03", "n-a", "n-c"), graphEdge("e-04", "n-b", "n-d"),
    graphEdge("e-05", "n-c", "n-b"), graphEdge("e-06", "n-c", "n-d"),
    graphEdge("e-07", "n-d", "n-b"), graphEdge("e-08", "n-e", "n-f"),
  ];
  const positions = buildRoutePresetPositions(graphNodes, graphEdges, "n-a");
  expect(buildRoutePresetPositions([], [], "n-missing")).toEqual({});
  expect(positions).toEqual({
    "n-a": { x: 28, y: 28 }, "n-b": { x: 288, y: 28 }, "n-c": { x: 288, y: 120 },
    "n-d": { x: 548, y: 28 }, "n-e": { x: 808, y: 28 }, "n-f": { x: 1068, y: 28 },
  });
  expect(buildRoutePresetPositions(graphNodes, graphEdges, "n-missing")).toEqual(positions);
  expect(buildRoutePresetPositions(graphNodes, graphEdges, "n-a")).toEqual(positions);
  expect(Object.keys(positions)).toEqual(graphNodes.map(({ id }) => id));
  expect(new Set(Object.values(positions).map(({ x, y }) => `${x},${y}`)).size).toBe(graphNodes.length);
  for (const { x, y } of Object.values(positions)) expect(Number.isFinite(x) && Number.isFinite(y)).toBe(true);
  expect(positions["n-b"].x - positions["n-a"].x).toBe(260);
  expect(positions["n-c"].y - positions["n-b"].y).toBe(92);
  for (const [invalidNodes, invalidEdges] of [
    [[graphNodes[1], graphNodes[0], ...graphNodes.slice(2)], graphEdges],
    [graphNodes, [graphEdges[1], graphEdges[0], ...graphEdges.slice(2)]],
    [[...graphNodes, graphNodes[5]], graphEdges],
    [[graphNodes[0], { ...graphNodes[1], id: "n-a" }, ...graphNodes.slice(2)], graphEdges],
    [graphNodes, [...graphEdges, { ...graphEdges[7], id: "e-08" }]],
    [graphNodes, [...graphEdges, { ...graphEdges[7], id: "e-00" }]],
    [graphNodes, [...graphEdges, { ...graphEdges[7], id: "e-09", target: "n-missing" }]],
  ] as const) expect(() => buildRoutePresetPositions(invalidNodes as GraphNode[], invalidEdges as GraphEdge[], "n-a")).toThrow("preset_layout_invalid_input");
  const source = buildRoutePresetPositions.toString();
  expect(source).not.toMatch(/localeCompare|toLocale|\.shift\(|\.sort\(|localStorage|sessionStorage|document\.|querySelector|fetch\(|JSON\.stringify|crypto|hash/i);
  expect((source.match(/buildRoutePresetPositions/g) ?? []).length).toBe(1);
});
test("uses one retained Core and one native preset lifecycle for each expanded graph generation", async ({ page }) => {
  await mock(page); await page.goto("/");
  const canvas = page.getByTestId("graph-canvas");
  await canvas.evaluate((element) => {
    const host = element as HTMLElement & {
      __cytoscapeForTests: {
        on: (events: string, listener: (event: { type: string }) => void) => void;
        layout: (options: unknown) => { run: () => unknown };
      };
      __routeLifecycle?: { core: unknown; events: string[]; options: unknown[] };
    };
    const cy = host.__cytoscapeForTests;
    const state = { core: cy as unknown, events: [] as string[], options: [] as unknown[] };
    const layout = cy.layout.bind(cy);
    cy.layout = ((options: unknown) => { state.options.push(options); return layout(options); }) as typeof cy.layout;
    cy.on("layoutstart layoutready layoutstop", (event) => state.events.push(event.type));
    host.__routeLifecycle = state;
  });
  await page.getByRole("button", { name: "Expand all" }).click();
  await expect.poll(() => canvas.evaluate((element) => (element as HTMLElement & { __routeLifecycle: { events: string[] } }).__routeLifecycle.events)).toEqual(["layoutstart", "layoutready", "layoutstop"]);
  const expanded = await canvas.evaluate((element, rootId) => {
    const host = element as HTMLElement & {
      __cytoscapeForTests: {
        nodes: () => { map: <T>(visit: (node: { id: () => string; data: () => unknown; position: () => unknown; hasClass: (name: string) => boolean }) => T) => T[] };
        edges: () => { map: <T>(visit: (edge: { id: () => string; data: () => unknown; hasClass: (name: string) => boolean }) => T) => T[] };
      };
      __routeLifecycle: { core: unknown; options: unknown[] };
    };
    const cy = host.__cytoscapeForTests;
    return {
      sameCore: cy === host.__routeLifecycle.core,
      options: host.__routeLifecycle.options,
      nodes: cy.nodes().map((node) => [node.id(), node.data(), node.position(), ["evidence-inferred", "evidence-observed", "evidence-unresolved"].filter((name) => node.hasClass(name))] as [string, unknown, unknown, string[]]),
      edges: cy.edges().map((edge) => [edge.id(), edge.data(), ["evidence-inferred", "evidence-observed", "evidence-unresolved", "backbone"].filter((name) => edge.hasClass(name))] as [string, unknown, string[]]),
      rootId,
    };
  }, byLabel["/"].id);
  expect(expanded.sameCore).toBe(true);
  expect(expanded.options).toEqual([{ name: "preset", positions: expect.any(Object), fit: true, padding: 28, animate: false }]);
  expect(expanded.nodes).toHaveLength(staticGraph.nodes.length);
  expect(expanded.edges).toHaveLength(staticGraph.edges.length);
  expect(Object.fromEntries(expanded.nodes.map(([id, data, position]) => [id, position]))).toEqual(buildRoutePresetPositions(staticGraph.nodes as GraphNode[], staticGraph.edges as GraphEdge[], expanded.rootId));
  for (const [id, data, , evidence] of expanded.nodes) {
    const expected = staticGraph.nodes.find((node) => node.id === id)!;
    expect(data).toEqual({ id: expected.id, label: expected.label });
    expect(evidence).toEqual([`evidence-${expected.evidence[0].kind}`]);
  }
  for (const [id, data, classes] of expanded.edges) {
    const expected = staticGraph.edges.find((edge) => edge.id === id)!;
    expect(data).toEqual({ id: expected.id, source: expected.source, target: expected.target, label: expected.kind });
    expect(classes).toContain(`evidence-${expected.evidence[0].kind}`);
  }
  await page.getByRole("button", { name: "Collapse branches" }).click();
  await page.getByRole("button", { name: "Expand all" }).click();
  const reexpanded = await canvas.evaluate((element) => {
    const host = element as HTMLElement & { __cytoscapeForTests: { nodes: () => { map: <T>(visit: (node: { id: () => string; position: () => unknown }) => T) => T[] } }; __routeLifecycle: { core: unknown; events: string[] } };
    return { sameCore: host.__cytoscapeForTests === host.__routeLifecycle.core, events: host.__routeLifecycle.events, positions: host.__cytoscapeForTests.nodes().map((node) => [node.id(), node.position()] as [string, unknown]) };
  });
  expect(reexpanded.sameCore).toBe(true);
  expect(reexpanded.events).toEqual(["layoutstart", "layoutready", "layoutstop", "layoutstart", "layoutready", "layoutstop", "layoutstart", "layoutready", "layoutstop"]);
  expect(Object.fromEntries(reexpanded.positions)).toEqual(buildRoutePresetPositions(staticGraph.nodes as GraphNode[], staticGraph.edges as GraphEdge[], byLabel["/"].id));
});
test("rejects duplicate outline identities and never searches structural or identifier-only prose", () => {
  const outline = buildFlowOutline(
    [
      { id: "n-root", label: "Allowed label", kind: "frontend_route", evidence: [inferred], source: { repository: "web", path: "routes.ts" } },
      { id: "n-target", label: "Target", kind: "page", evidence: [inferred], source: { repository: "web", path: "target.tsx", symbol: "AllowedSymbol" } },
    ] as unknown as GraphNode[],
    [{ id: "e-edge", source: "n-root", target: "n-target", kind: "renders", evidence: [inferred], confidence: 1, metadata: {} }] as unknown as GraphEdge[],
    "n-root",
  );
  expect(filterFlowOutline(outline, "allowed label")).toHaveLength(2);
  expect(filterFlowOutline(outline, "allowedsymbol")).toHaveLength(1);
  for (const term of ["node", "edge", "kind", "evidence", "source", "from", "to", "occurrence", "of", "n-root", "n-target", "e-edge", "1", "2"]) {
    expect(filterFlowOutline(outline, term), term).toEqual([]);
  }
  expect(() => buildFlowOutline(
    [{ id: "n-same", label: "One" }, { id: "n-same", label: "Two" }] as unknown as GraphNode[],
    [],
    "n-same",
  )).toThrow("flow_outline_invalid_input");
  expect(() => buildFlowOutline(
    [{ id: "n-one", label: "One" }] as unknown as GraphNode[],
    [{ id: "e-same", source: "n-one", target: "n-one" }, { id: "e-same", source: "n-one", target: "n-one" }] as unknown as GraphEdge[],
    "n-one",
  )).toThrow("flow_outline_invalid_input");
});

test("refresh commits against interactions made after its start and resets only replacement-owned outline state", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let graphRequest = 0;
  await mock(page);
  await page.route("**/api/graph", async (route) => {
    graphRequest += 1;
    if (graphRequest === 1) return route.fulfill({ json: staticGraph });
    await pending;
    return route.fulfill({ json: staticGraph });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Expand all" }).click();
  await graphNode(page, "Home").click();
  await page.getByTestId("refresh-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Refreshing static snapshot…");
  await page.getByTestId("route-search").fill("react");
  await page.getByTestId("outline-search").fill("home");
  await page.getByTestId("layer-filter-frontend").getByRole("checkbox").uncheck();
  await expect(page.getByTestId("selection-status")).toHaveText("Selection cleared because it is no longer visible.");
  release();
  await expect(page.getByTestId("operation-status")).toHaveText("Static snapshot refreshed.");
  await expect(page.getByTestId("route-search")).toHaveValue("react");
  await expect(page.getByTestId("outline-search")).toHaveValue("");
  await expect(page.getByTestId("outline-page")).toHaveText("Page 1 of 1");
  await expect(page.getByTestId("selected-detail")).toContainText("Route Summary");
});

test("operation status exclusively owns polite announcements while a selection is hidden during work", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await mock(page);
  await page.route("**/api/analyze", async (route) => { await pending; await route.fulfill({ json: transientGraph }); });
  await page.goto("/");
  await page.getByRole("button", { name: "Expand all" }).click();
  await graphNode(page, "Home").click();
  await page.getByTestId("runtime-capture-input").fill("capture-live");
  await page.getByTestId("analyze-button").click();
  const status = page.getByTestId("operation-status");
  await expect(status).toHaveAttribute("role", "status");
  await expect(status).toHaveAttribute("aria-live", "polite");
  await expect(status).toHaveAttribute("aria-atomic", "true");
  await expect(status).toHaveText("Analyzing selected runtime capture…");
  await page.getByTestId("layer-filter-frontend").getByRole("checkbox").uncheck();
  const note = page.getByTestId("selection-status");
  await expect(note).toHaveText("Selection cleared because it is no longer visible.");
  await expect(note).toHaveAttribute("aria-live", "off");
  await expect(note).not.toHaveAttribute("role");
  await expect(status).toHaveText("Analyzing selected runtime capture…");
  release();
  await expect(status).toHaveText("Runtime capture analysis complete. Runtime evidence is transient.");
  await expect(note).toHaveCount(0);
});

test("latest start wins across capture, blank Analyze, and Refresh without stale transient installation", async ({ page }) => {
  let releaseCapture!: () => void;
  const capturePending = new Promise<void>((resolve) => { releaseCapture = resolve; });
  let analyzeCount = 0;
  await mock(page);
  await page.route("**/api/analyze", async (route) => {
    analyzeCount += 1;
    if (analyzeCount === 1) {
      await capturePending;
      return route.fulfill({ json: transientGraph });
    }
    return route.fulfill({ json: staticGraph });
  });
  await page.goto("/");
  await page.getByTestId("runtime-capture-input").fill("capture-overlap");
  await page.getByTestId("analyze-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Analyzing selected runtime capture…");
  await page.getByTestId("runtime-capture-input").fill("");
  await page.getByTestId("analyze-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Static analysis complete.");
  await page.getByTestId("refresh-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Static snapshot refreshed.");
  releaseCapture();
  await expect(page.getByTestId("transient-status")).toHaveCount(0);
  await expect(page.getByTestId("operation-status")).toHaveText("Static snapshot refreshed.");
});

test("large graph readiness waits for full layout, finite positions, bounded outline, and a keyboard-operable late edge", async ({ page }) => {
  const root = staticGraph.nodes.find((node) => node.id === byLabel["/"].id)!;
  const pageNode = staticGraph.nodes.find((node) => node.id === byLabel.Home.id)!;
  const functions = Array.from({ length: 1998 }, (_, index) => ({
    id: nodeId(`src/large/${index}.ts`, "function", `large-${index}`),
    kind: "function",
    identityKey: `large-${index}`,
    label: index === 6 ? `Late source ${"é".repeat(244)}` : index === 103 ? `Late target ${"é".repeat(244)}` : `F${index} ${"é".repeat(256 - `F${index} `.length)}`,
    layer: "frontend",
    source: { repository: "web", path: `src/large/${index}.ts`, line: 1 },
    evidence: [inferred],
    confidence: 0.9,
    metadata: { frameworkOwners: ["react"] },
  }));
  const functionEdges = functions.flatMap((node, index) => [1, 17, 53].map((step) => edge(node.id, functions[(index + step) % functions.length].id, "calls")))
    .concat(functions.slice(0, 7).map((node, index) => edge(node.id, functions[(index + 97) % functions.length].id, "calls")));
  const largeGraph = {
    ...staticGraph,
    nodes: [root, pageNode, ...functions].sort((left, right) => left.id.localeCompare(right.id)),
    edges: [
      edge(root.id, pageNode.id, "renders"),
      ...functions.map((node) => edge(pageNode.id, node.id, "calls")),
      ...functionEdges,
    ].sort((left, right) => left.id.localeCompare(right.id)),
  };
  expect(largeGraph.nodes).toHaveLength(2000);
  expect(largeGraph.edges).toHaveLength(8000);
  const pureStart = performance.now();
  expect(buildFlowOutline(largeGraph.nodes as GraphNode[], largeGraph.edges as GraphEdge[], root.id)).toHaveLength(10000);
  expect(performance.now() - pureStart).toBeLessThanOrEqual(250);
  const expectedPositions = buildRoutePresetPositions(largeGraph.nodes as GraphNode[], largeGraph.edges as GraphEdge[], root.id);
  const outgoing = new Map<string, GraphEdge[]>();
  for (const graphEdge of largeGraph.edges as GraphEdge[]) {
    const edges = outgoing.get(graphEdge.source) ?? [];
    edges.push(graphEdge);
    outgoing.set(graphEdge.source, edges);
  }
  const backbone = new Set<string>();
  const seen = new Set<string>();
  const queue = [root.id];
  for (let index = 0; index < queue.length; index += 1) {
    const source = queue[index];
    if (seen.has(source)) continue;
    seen.add(source);
    for (const graphEdge of outgoing.get(source) ?? []) if (!seen.has(graphEdge.target)) {
      backbone.add(graphEdge.id);
      queue.push(graphEdge.target);
    }
  }
  const expectedOracle = {
    nodes: Object.fromEntries(largeGraph.nodes.map((node) => [node.id, { id: node.id, label: node.label, classes: ["evidence-inferred"], position: expectedPositions[node.id] }])),
    edges: Object.fromEntries(largeGraph.edges.map((graphEdge) => [graphEdge.id, { id: graphEdge.id, source: graphEdge.source, target: graphEdge.target, label: graphEdge.kind, classes: backbone.has(graphEdge.id) ? ["evidence-inferred", "backbone"] : ["evidence-inferred"] }])),
  };
  const late = functionEdges[functionEdges.length - 1];
  await mock(page, { graph: largeGraph });
  await page.goto("/");
  const canvas = page.getByTestId("graph-canvas");
  await canvas.evaluate((element, expected) => {
    type ExpectedNode = { id: string; label: string; classes: string[]; position: { x: number; y: number } };
    type ExpectedEdge = { id: string; source: string; target: string; label: string; classes: string[] };
    type Oracle = { nodes: Record<string, ExpectedNode>; edges: Record<string, ExpectedEdge>; tokens: Record<string, string> };
    type Core = { on: (events: string, listener: (event: { type: string }) => void) => void };
    const host = element as HTMLElement & {
      __cytoscapeForTests: Core;
      __largeLayoutGeneration?: { core: Core; events: string[]; started: number; finished: number | null };
      __largeLayoutOracle?: Readonly<Oracle>;
    };
    const resolveToken = (name: string) => {
      const probe = document.createElement("span");
      probe.style.color = `var(${name})`;
      host.append(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      return value;
    };
    const oracle: Oracle = {
      ...expected,
      tokens: {
        nodeFill: resolveToken("--graph-node-fill"),
        nodeLabel: resolveToken("--graph-node-label"),
        evidenceInferred: resolveToken("--graph-evidence-inferred"),
        edgeLabel: resolveToken("--graph-edge-label"),
        edgeLabelBackground: resolveToken("--graph-edge-label-background"),
        backbone: resolveToken("--graph-backbone"),
      },
    };
    for (const node of Object.values(oracle.nodes)) { Object.freeze(node.classes); Object.freeze(node.position); Object.freeze(node); }
    for (const graphEdge of Object.values(oracle.edges)) { Object.freeze(graphEdge.classes); Object.freeze(graphEdge); }
    Object.freeze(oracle.nodes); Object.freeze(oracle.edges); Object.freeze(oracle.tokens); host.__largeLayoutOracle = Object.freeze(oracle);
    const state = { core: host.__cytoscapeForTests, events: [] as string[], started: 0, finished: null as number | null };
    host.__cytoscapeForTests.on("layoutstart layoutready layoutstop", (event) => {
      state.events.push(event.type);
      if (event.type === "layoutstop") state.finished = performance.now();
    });
    host.__largeLayoutGeneration = state;
  }, expectedOracle);
  await page.getByRole("button", { name: "Expand all" }).evaluate((button) => {
    const canvas = document.querySelector('[data-testid="graph-canvas"]') as HTMLElement & { __largeLayoutGeneration: { started: number } };
    canvas.__largeLayoutGeneration.started = performance.now();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => canvas.evaluate((element) => (element as HTMLElement & { __largeLayoutGeneration: { events: string[] } }).__largeLayoutGeneration.events), { timeout: 15_000 }).toEqual(["layoutstart", "layoutready", "layoutstop"]);
  const proof = await canvas.evaluate((element) => {
    type ExpectedNode = { id: string; label: string; classes: string[]; position: { x: number; y: number } };
    type ExpectedEdge = { id: string; source: string; target: string; label: string; classes: string[] };
    type Oracle = { nodes: Record<string, ExpectedNode>; edges: Record<string, ExpectedEdge>; tokens: Record<string, string> };
    type Item = { id: () => string; data: () => Record<string, unknown>; position: () => { x: number; y: number }; style: (name: string) => string; classes: () => string[] };
    type Core = { elements: () => { length: number }; nodes: () => { length: number; forEach: (visit: (item: Item) => void) => void }; edges: () => { length: number; forEach: (visit: (item: Item) => void) => void } };
    const host = element as HTMLElement & { __cytoscapeForTests: Core; __largeLayoutGeneration: { core: Core }; __largeLayoutOracle: Readonly<Oracle> };
    const cy = host.__cytoscapeForTests;
    const oracle = host.__largeLayoutOracle;
    let code = "";
    const fail = (next: string) => { if (!code) code = next; };
    const exactData = (actual: Record<string, unknown>, expected: Record<string, string>) => Object.keys(actual).length === Object.keys(expected).length && Object.entries(expected).every(([key, value]) => actual[key] === value);
    const exactColor = (actual: string, expected: string) => actual.replace(/\s+/g, "") === expected.replace(/\s+/g, "");
    const exactClasses = (item: Item, expected: string[]) => {
      const actual = item.classes();
      return actual.length === expected.length && expected.every((name) => actual.includes(name));
    };
    const nodeIds = new Set<string>();
    const edgeIds = new Set<string>();
    const positions = new Set<string>();
    let nodes = 0;
    let edges = 0;
    cy.nodes().forEach((node) => {
      nodes += 1;
      const id = node.id();
      const expected = oracle.nodes[id];
      if (!expected) { fail("unexpected-node"); return; }
      nodeIds.add(id);
      const position = node.position();
      if (!exactData(node.data(), { id: expected.id, label: expected.label })) fail("node-data");
      if (!Number.isFinite(position.x) || !Number.isFinite(position.y) || position.x !== expected.position.x || position.y !== expected.position.y) fail("node-position");
      positions.add(`${position.x},${position.y}`);
      if (!exactClasses(node, expected.classes)) fail("node-classes");
      if (node.style("display") === "none" || Number(node.style("opacity")) <= 0) fail("node-visibility");
      if (node.style("content") !== expected.label || !exactColor(node.style("border-color"), oracle.tokens.evidenceInferred) || !exactColor(node.style("background-color"), oracle.tokens.nodeFill) || !exactColor(node.style("color"), oracle.tokens.nodeLabel) || node.style("border-width") !== "2px" || node.style("border-style") !== "dashed" || node.style("shape") !== "round-rectangle" || node.style("width") !== "190px" || node.style("height") !== "58px" || node.style("text-wrap") !== "wrap" || node.style("text-max-width") !== "180px") fail("node-style");
    });
    cy.edges().forEach((graphEdge) => {
      edges += 1;
      const id = graphEdge.id();
      const expected = oracle.edges[id];
      if (!expected) { fail("unexpected-edge"); return; }
      edgeIds.add(id);
      if (!exactData(graphEdge.data(), { id: expected.id, source: expected.source, target: expected.target, label: expected.label })) fail("edge-data");
      if (!exactClasses(graphEdge, expected.classes)) fail("edge-classes");
      if (graphEdge.style("display") === "none" || Number(graphEdge.style("opacity")) <= 0) fail("edge-visibility");
      const backboneEdge = expected.classes.includes("backbone");
      const color = backboneEdge ? oracle.tokens.backbone : oracle.tokens.evidenceInferred;
      if (graphEdge.style("label") !== expected.label || !exactColor(graphEdge.style("line-color"), color) || !exactColor(graphEdge.style("target-arrow-color"), color) || !exactColor(graphEdge.style("color"), oracle.tokens.edgeLabel) || !exactColor(graphEdge.style("text-background-color"), oracle.tokens.edgeLabelBackground) || graphEdge.style("line-style") !== "dashed" || graphEdge.style("target-arrow-shape") !== "triangle" || graphEdge.style("width") !== (backboneEdge ? "4px" : "2px") || graphEdge.style("text-background-opacity") !== "1" || graphEdge.style("text-background-padding") !== "3px" || graphEdge.style("text-background-shape") !== "roundrectangle") fail("edge-style");
    });
    if (cy.elements().length !== 10000) fail("element-count");
    if (nodes !== 2000 || nodeIds.size !== 2000 || nodeIds.size !== Object.keys(oracle.nodes).length) fail("node-count");
    if (edges !== 8000 || edgeIds.size !== 8000 || edgeIds.size !== Object.keys(oracle.edges).length) fail("edge-count");
    if (positions.size !== 2000) fail("position-uniqueness");
    if (cy !== host.__largeLayoutGeneration.core) fail("core-replaced");
    return { ok: !code, code, elements: cy.elements().length, nodes, edges, positions: positions.size };
  });
  expect(proof).toEqual({ ok: true, code: "", elements: 10000, nodes: 2000, edges: 8000, positions: 2000 });
  await expect(page.getByTestId("outline-count")).toHaveText("10000 of 10000 visible graph entities");
  await expect(page.locator("[data-outline-key]")).toHaveCount(100);
  await expect(page.getByTestId("flow-outline").locator("button, input")).toHaveCount(103);
  await page.getByTestId("outline-search").fill("late source");
  await canvas.evaluate((element) => {
    (element as HTMLElement & { __largeLayoutGeneration: { lateStarted: number } }).__largeLayoutGeneration.lateStarted = performance.now();
  });
  const lateButton = page.getByTestId(`graph-edge-${late.id}`);
  await lateButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("selected-detail")).toContainText("calls");
  await expect(page.getByTestId("selected-detail")).toContainText("Late source");
  await expect(page.getByTestId("selected-detail")).toContainText("Late target");
  await expect(lateButton).toHaveAttribute("aria-pressed", "true");
  const elapsed = await canvas.evaluate((element) => {
    const state = (element as HTMLElement & { __largeLayoutGeneration: { events: string[]; started: number; finished: number | null; lateStarted: number } }).__largeLayoutGeneration;
    const now = performance.now();
    return { layout: state.finished === null ? null : state.finished - state.started, late: now - state.lateStarted, events: state.events };
  });
  expect(elapsed.events).toEqual(["layoutstart", "layoutready", "layoutstop"]);
  expect(elapsed.layout).not.toBeNull();
  expect(elapsed.layout!).toBeLessThanOrEqual(10_000);
  expect(elapsed.late).toBeLessThanOrEqual(1000);
});
test("unmounting during independent bootstrap delays prevents late graph startup and capability use", async ({ page }) => {
  let releaseHealth!: () => void;
  let releaseConfig!: () => void;
  const health = new Promise<void>((resolve) => { releaseHealth = resolve; });
  const configReady = new Promise<void>((resolve) => { releaseConfig = resolve; });
  let graphRequests = 0;
  let analyzeRequests = 0;
  const consoleErrors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  capabilityArtifactBytes = Buffer.from(capability, "utf8");
  await page.route("**/api/health", async (route) => { await health; await route.fulfill({ json: { ok: true, status: "ready" } }); });
  await page.route("**/api/config", async (route) => { await configReady; await route.fulfill({ json: config() }); });
  await page.route("**/api/graph", (route) => { graphRequests += 1; return route.fulfill({ json: staticGraph }); });
  await page.route("**/api/analyze", (route) => { analyzeRequests += 1; return route.fulfill({ json: staticGraph }); });
  await page.goto("/");
  await page.goto("about:blank");
  releaseHealth();
  releaseConfig();
  await Promise.resolve();
  expect(graphRequests).toBe(0);
  expect(analyzeRequests).toBe(0);
  expect(consoleErrors).toEqual([]);
});
test("resolves exact normal tokens and their WCAG contrast pairs in the browser", async ({ page }) => {
  await mock(page); await page.goto("/");
  const tokens = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries([
      ["--wb-canvas", "#eef2f6"], ["--wb-surface", "#ffffff"], ["--wb-surface-subtle", "#f1f5f9"], ["--wb-surface-raised", "#ffffff"],
      ["--wb-text", "#172033"], ["--wb-text-muted", "#475569"], ["--wb-text-inverse", "#ffffff"], ["--wb-border", "#64748b"],
      ["--wb-border-strong", "#334155"], ["--wb-accent", "#0b4f86"], ["--wb-focus", "#005fcc"], ["--wb-success", "#166534"],
      ["--wb-warning", "#854d0e"], ["--wb-error", "#b91c1c"], ["--wb-info", "#075985"], ["--wb-evidence-observed", "#0f766e"],
      ["--wb-evidence-inferred", "#1d4ed8"], ["--wb-evidence-unresolved", "#6b21a8"], ["--graph-node-fill", "#f8fafc"],
      ["--graph-node-border", "#334155"], ["--graph-node-label", "#172033"], ["--graph-edge", "#475569"], ["--graph-edge-label", "#172033"],
      ["--graph-edge-label-background", "#ffffff"], ["--graph-backbone", "#0b4f86"], ["--graph-selection-fill", "#fef3c7"],
      ["--graph-selection-border", "#92400e"], ["--graph-selection-edge", "#92400e"], ["--motion-fast", "140ms"],
    ].map(([name, expected]) => [name, { expected, actual: style.getPropertyValue(name).trim() }]));
  });
  for (const [name, value] of Object.entries(tokens)) expect(canonicalCssToken(value.actual), name).toBe(value.expected);
  const ratio = (first: string, second: string) => {
    const luminance = (hex: string) => {
      const channels = hex.slice(1).match(/.{2}/g)!.map((part) => Number.parseInt(part, 16) / 255).map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const [lighter, darker] = [luminance(first), luminance(second)].sort((left, right) => right - left);
    return (lighter + 0.05) / (darker + 0.05);
  };
  for (const [foreground, background, minimum] of [
    ["#172033", "#ffffff", 4.5], ["#172033", "#eef2f6", 4.5], ["#475569", "#ffffff", 4.5],
    ["#ffffff", "#0b4f86", 4.5], ["#ffffff", "#b91c1c", 4.5], ["#005fcc", "#ffffff", 3],
    ["#005fcc", "#eef2f6", 3], ["#172033", "#f8fafc", 4.5], ["#172033", "#ffffff", 4.5],
    ["#475569", "#eef2f6", 3], ["#92400e", "#fef3c7", 3], ["#0f766e", "#ffffff", 3],
    ["#1d4ed8", "#ffffff", 3], ["#6b21a8", "#ffffff", 3],
  ] as const) expect(ratio(foreground, background), `${foreground} on ${background}`).toBeGreaterThanOrEqual(minimum);
});

test("forced-colors refresh preserves the Cytoscape instance, elements, positions, and selection", async ({ page }) => {
  await mock(page); await page.goto("/");
  await page.getByRole("button", { name: "Expand all" }).click();
  await graphNode(page, "items").click();
  const canvas = page.getByTestId("graph-canvas");
  const selectedNodeId = nodeId("server/urls.py", "django_url_pattern", "items");
  const before = await canvas.evaluate((element, nodeIdentifier) => {
    const cy = (element as HTMLElement & { __cytoscapeForTests: { nodes: () => { length: number; map: (fn: (node: { id: () => string; position: () => { x: number; y: number } }) => unknown) => unknown[] }; edges: () => { length: number }; $id: (id: string) => { hasClass: (name: string) => boolean } }; __initialCy?: unknown }).__cytoscapeForTests;
    (element as HTMLElement & { __initialCy?: unknown }).__initialCy = cy;
    return { nodes: cy.nodes().length, edges: cy.edges().length, positions: cy.nodes().map((node) => [node.id(), node.position()]), selected: cy.$id(nodeIdentifier).hasClass("selected") };
  }, selectedNodeId);
  await canvas.evaluate((element) => {
    const cy = (element as HTMLElement & { __cytoscapeForTests: Record<"layout" | "add" | "remove", (...args: never[]) => unknown>; __forcedColorCounters?: Record<"layout" | "add" | "remove", number> }).__cytoscapeForTests;
    const counters = { layout: 0, add: 0, remove: 0 };
    for (const name of ["layout", "add", "remove"] as const) {
      const original = cy[name].bind(cy);
      cy[name] = ((...args: never[]) => { counters[name] += 1; return original(...args); }) as typeof cy[typeof name];
    }
    (element as HTMLElement & { __forcedColorCounters: typeof counters }).__forcedColorCounters = counters;
  });
  await page.emulateMedia({ forcedColors: "active" });
  await expect.poll(() => canvas.evaluate(() => matchMedia("(forced-colors: active)").matches)).toBe(true);
  const after = await canvas.evaluate((element, nodeIdentifier) => {
    const host = element as HTMLElement & { __cytoscapeForTests: { nodes: () => { length: number; map: (fn: (node: { id: () => string; position: () => { x: number; y: number } }) => unknown) => unknown[] }; edges: () => { length: number }; $id: (id: string) => { hasClass: (name: string) => boolean; style: (name: string) => string } }; __initialCy?: unknown };
    const cy = host.__cytoscapeForTests;
    const selected = cy.$id(nodeIdentifier);
    return { same: cy === host.__initialCy, nodes: cy.nodes().length, edges: cy.edges().length, positions: cy.nodes().map((node) => [node.id(), node.position()]), selected: selected.hasClass("selected"), nodeFill: selected.style("background-color") };
  }, selectedNodeId);
  expect(after.same).toBe(true); expect(after.nodes).toBe(before.nodes); expect(after.edges).toBe(before.edges);
  expect(after.positions).toEqual(before.positions); expect(after.positions.every(([, point]) => Number.isFinite((point as { x: number }).x) && Number.isFinite((point as { y: number }).y))).toBe(true);
  expect(after.selected).toBe(true); expect(after.nodeFill).toMatch(/^rgb/);
  expect(await canvas.evaluate((element) => (element as HTMLElement & { __forcedColorCounters: { layout: number; add: number; remove: number } }).__forcedColorCounters)).toEqual({ layout: 0, add: 0, remove: 0 });
  await page.emulateMedia({ forcedColors: "none" });
});

test("uses only approved motion, target, focus, landmark, and responsive geometry contracts", async ({ page }) => {
  await mock(page); await page.goto("/");
  const canvas = page.getByTestId("graph-canvas");
  const normalTransitions = await page.locator("button, input, summary").evaluateAll((elements) => [...new Set(elements.flatMap((element) => getComputedStyle(element).transitionProperty.split(",").map((property) => property.trim())))]);
  expect(normalTransitions.every((property) => ["color", "background-color", "border-color", "opacity", "all"].includes(property))).toBe(true);
  await expect(page.locator('[role="status"][aria-live="polite"][aria-atomic="true"]')).toHaveCount(1);
  await expect(page.getByTestId("workbench")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator('header[aria-label="Execution Evidence Workbench commands"]')).toHaveCount(1);
  await expect(page.locator('nav[aria-label="Routes"]')).toHaveCount(1);
  await expect(page.getByTestId("graph-canvas")).toHaveAttribute("aria-describedby", "flow-outline-heading");
  const targetBoxes = await page.locator("button, input, summary, .check-row").evaluateAll((elements) => elements.map((element) => {
    const box = element.getBoundingClientRect(); return { width: box.width, height: box.height, text: element.textContent };
  }));
  for (const box of targetBoxes) { expect(box.width, box.text).toBeGreaterThanOrEqual(24); expect(box.height, box.text).toBeGreaterThanOrEqual(24); }
  await page.getByRole("button", { name: "Expand all" }).click();
  const selected = graphNode(page, "Home");
  await selected.click();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(selected).toBeFocused();
  const focus = await selected.evaluate((element) => ({ outline: getComputedStyle(element).outline, background: getComputedStyle(element).backgroundColor }));
  expect(focus.outline).toContain("rgb(0, 95, 204)"); expect(focus.background).not.toContain("0, 95, 204");
  for (const viewport of [{ width: 1180, height: 900 }, { width: 780, height: 900 }, { width: 320, height: 640 }, { width: 720, height: 450 }]) {
    await page.setViewportSize(viewport);
    const geometry = await page.evaluate(() => {
      const box = (selector: string) => (document.querySelector(selector) as HTMLElement).getBoundingClientRect();
      const routes = box("#routes-region"); const graph = box("#graph-region"); const outline = box("#flow-outline"); const inspector = box("#inspector");
      return { overflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth, routes, graph, outline, inspector };
    });
    expect(geometry.overflow, `${viewport.width}×${viewport.height}`).toBe(true);
    expect(geometry.routes.top).toBeLessThanOrEqual(geometry.graph.top);
    expect(geometry.graph.top).toBeLessThanOrEqual(geometry.outline.top);
    expect(geometry.outline.top).toBeLessThanOrEqual(geometry.inspector.top);
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(canonicalCssToken(await canvas.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--motion-fast").trim()))).toBe("0ms");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  expect(canonicalCssToken(await canvas.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--motion-fast").trim()))).toBe("140ms");
});

test("selection-only changes retain Cytoscape elements and do not invoke layout", async ({ page }) => {
  await mock(page); await page.goto("/");
  await page.getByRole("button", { name: "Expand all" }).click();
  const canvas = page.getByTestId("graph-canvas");
  await canvas.evaluate((element) => {
    const cy = (element as HTMLElement & { __cytoscapeForTests: { layout: () => unknown; add: () => unknown; remove: () => unknown } }).__cytoscapeForTests;
    const state = { layout: 0, add: 0, remove: 0 };
    for (const name of ["layout", "add", "remove"] as const) {
      const original = cy[name].bind(cy);
      cy[name] = ((...args: never[]) => { state[name] += 1; return original(...args); }) as never;
    }
    (element as HTMLElement & { __selectionCounters: typeof state }).__selectionCounters = state;
  });
  await graphNode(page, "Home").click();
  await graphNode(page, "load").click();
  expect(await canvas.evaluate((element) => (element as HTMLElement & { __selectionCounters: { layout: number; add: number; remove: number } }).__selectionCounters)).toEqual({ layout: 0, add: 0, remove: 0 });
});
