import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const analyzer = path.join(repoRoot, "analyzers/index.mjs");
const fixtureRoot = path.join(repoRoot, "fixtures/nuxt-django");

async function runFrontendAnalyzer() {
  const { stdout } = await execFileAsync(process.execPath, [
    analyzer,
    "--repository",
    "fixture",
    "--frontend-only",
    fixtureRoot,
  ], {
    cwd: repoRoot,
    maxBuffer: 1024 * 1024 * 8,
  });
  return JSON.parse(stdout);
}

function nodeFor(fragment, kind, metadata, sourcePath) {
  return fragment.nodes.find((node) => (
    node.kind === kind
    && Object.entries(metadata).every(([key, value]) => node.metadata[key] === value)
    && (!sourcePath || node.source.path === sourcePath)
  ));
}

function hasEdge(fragment, source, kind, target, metadata = {}) {
  return fragment.edges.some((edge) => (
    edge.source === source.key
    && edge.kind === kind
    && edge.target === target.key
    && Object.entries(metadata).every(([key, value]) => edge.metadata[key] === value)
  ));
}

test("Nuxt pages directory produces file-based routes with dynamic params", async () => {
  const fragment = await runFrontendAnalyzer();
  const routes = new Set(fragment.routes.map((route) => `${route.framework}:${route.path}`));

  assert.ok(routes.has("nuxt:/"), "missing index route");
  assert.ok(routes.has("nuxt:/orders"), "missing nested index route");
  assert.ok(routes.has("nuxt:/orders/:id"), "missing [id] dynamic route");

  const indexRoute = nodeFor(fragment, "frontend_route", { framework: "nuxt", declaredPath: "/" });
  const ordersRoute = nodeFor(fragment, "frontend_route", { framework: "nuxt", declaredPath: "/orders" });
  const detailRoute = nodeFor(fragment, "frontend_route", { framework: "nuxt", declaredPath: "/orders/:id" });
  const indexPage = nodeFor(fragment, "component", {}, "frontend/pages/index.vue");
  const ordersPage = nodeFor(fragment, "component", {}, "frontend/pages/orders/index.vue");
  const detailPage = nodeFor(fragment, "component", {}, "frontend/pages/orders/%5Bid%5D.vue");
  assert.ok(indexRoute && ordersRoute && detailRoute && indexPage && ordersPage && detailPage);
  assert.ok(hasEdge(fragment, indexRoute, "renders", indexPage));
  assert.ok(hasEdge(fragment, ordersRoute, "renders", ordersPage));
  assert.ok(hasEdge(fragment, detailRoute, "renders", detailPage));
});

test("script setup useFetch/$fetch calls retain strict HTTP and payload structure", async () => {
  const fragment = await runFrontendAnalyzer();
  const expectedCalls = [
    ["GET", "/api/items/", "literal"],
    ["GET", "/api/orders/", "literal"],
    ["POST", "/api/orders/", "literal"],
    ["GET", "/api/orders/{u0}", "unbounded"],
    ["POST", "/api/orders/{u0}", "unbounded"],
  ];
  for (const [method, normalizedPath, urlResolution] of expectedCalls) {
    assert.ok(nodeFor(fragment, "http_call", {
      method,
      normalizedPath,
      urlResolution,
      queryFieldCount: 0,
      hasSensitiveQuery: false,
    }), `${method} ${normalizedPath} should be discovered`);
  }

  const indexPage = nodeFor(fragment, "component", {}, "frontend/pages/index.vue");
  const itemRequest = nodeFor(fragment, "http_call", {
    method: "GET", normalizedPath: "/api/items/", urlResolution: "literal",
  });
  assert.ok(indexPage && itemRequest);
  assert.ok(hasEdge(fragment, indexPage, "calls", itemRequest), "top-level useFetch should be attributed to the page component");

  const orderRequest = nodeFor(fragment, "http_call", {
    method: "POST", normalizedPath: "/api/orders/", urlResolution: "literal",
  });
  const payloadEdge = fragment.edges.find((edge) => edge.source === orderRequest?.key && edge.kind === "carries");
  const payload = fragment.nodes.find((node) => node.key === payloadEdge?.target);
  assert.deepEqual(payload?.metadata, {
    payloadKinds: ["body"],
    bodyShape: "object",
    bodyFieldCount: 2,
    queryFieldCount: 0,
    hasSensitiveFields: false,
  });
  assert.deepEqual(payloadEdge?.metadata, { payloadKinds: ["body"] });
  const emitted = JSON.stringify(fragment);
  assert.equal(emitted.includes("itemId"), false);
  assert.equal(emitted.includes("quantity"), false);
});

test("NuxtLink and navigateTo targets link to file-based routes", async () => {
  const fragment = await runFrontendAnalyzer();
  const indexPage = nodeFor(fragment, "component", {}, "frontend/pages/index.vue");
  const detailPage = nodeFor(fragment, "component", {}, "frontend/pages/orders/%5Bid%5D.vue");
  const ordersRoute = nodeFor(fragment, "frontend_route", { framework: "nuxt", declaredPath: "/orders" });
  const indexRoute = nodeFor(fragment, "frontend_route", { framework: "nuxt", declaredPath: "/" });
  assert.ok(indexPage && detailPage && ordersRoute && indexRoute);
  assert.ok(hasEdge(fragment, indexPage, "navigates_to", ordersRoute));
  assert.ok(hasEdge(fragment, detailPage, "navigates_to", indexRoute));
});

test("Nuxt server/api routes are terminal unresolved boundaries, never traversed", async () => {
  const fragment = await runFrontendAnalyzer();
  const healthRequest = nodeFor(fragment, "http_call", {
    method: "GET", normalizedPath: "/api/health", urlResolution: "literal",
  });
  const resolution = fragment.edges.find((edge) => edge.source === healthRequest?.key && edge.kind === "resolves_to");
  const boundary = fragment.nodes.find((node) => node.key === resolution?.target);

  assert.ok(healthRequest && resolution && boundary, "health request should resolve to an explicit terminal boundary");
  assert.equal(boundary.kind, "unresolved_target");
  assert.equal(boundary.label, "Unresolved");
  assert.equal(boundary.source.path, "frontend/pages/index.vue");
  assert.equal(resolution.metadata.resolutionTier, "unbounded");
  const emitted = JSON.stringify(fragment);
  assert.equal(emitted.includes("server/api/health.get.ts"), false);
  assert.equal(fragment.nodes.some((node) => node.kind === "external_service" && node.source.path.includes("server/api")), false);
});
