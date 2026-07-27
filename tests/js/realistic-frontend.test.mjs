import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const analyzer = path.join(repoRoot, "analyzers/index.mjs");
const fixtureRoot = path.join(repoRoot, "fixtures/react-django-realistic");

async function runFrontendAnalyzer(root = fixtureRoot) {
  const { stdout } = await execFileAsync(process.execPath, [
    analyzer,
    "--repository",
    "fixture",
    "--frontend-only",
    root,
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

function hasEdge(fragment, source, kind, target) {
  return fragment.edges.some((edge) => (
    edge.source === source.key && edge.kind === kind && edge.target === target.key
  ));
}

test("realistic React Router v6 routes are discovered with joined paths", async () => {
  const fragment = await runFrontendAnalyzer();
  const routes = new Set(fragment.routes.map((route) => `${route.framework}:${route.path}`));

  assert.ok(routes.has("react:/"), "missing root route");
  assert.ok(routes.has("react:/orders"), "missing nested element-form /orders route");
  assert.ok(routes.has("react:/orders/:orderId"), "missing child route joined with parent path");
  assert.ok(routes.has("react:/customers"), "missing createBrowserRouter data route");
});

test("realistic routes render their page components", async () => {
  const fragment = await runFrontendAnalyzer();
  const routeComponentPairs = [
    ["/", "frontend/src/HomePage.tsx"],
    ["/orders", "frontend/src/OrdersPage.tsx"],
    ["/orders/:orderId", "frontend/src/OrderDetailPage.tsx"],
    ["/customers", "frontend/src/CustomersPage.tsx"],
  ];
  for (const [declaredPath, sourcePath] of routeComponentPairs) {
    const route = nodeFor(fragment, "frontend_route", { framework: "react", declaredPath });
    const component = nodeFor(fragment, "page", {}, sourcePath);
    assert.ok(route && component, `${declaredPath} should retain its route and page component`);
    assert.ok(hasEdge(fragment, route, "renders", component));
  }
});

test("realistic HTTP calls retain methods and minimized parameterized URLs", async () => {
  const fragment = await runFrontendAnalyzer();
  const expectedCalls = [
    ["GET", "/api/orders/", "literal"],
    ["POST", "/api/orders/", "literal"],
    ["GET", "/api/orders/{u0}", "unbounded"],
    ["POST", "/api/orders/{u0}", "unbounded"],
    ["DELETE", "/api/orders/{u0}", "unbounded"],
    ["GET", "/api/customers/", "literal"],
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

  const createRequest = nodeFor(fragment, "http_call", {
    method: "POST", normalizedPath: "/api/orders/", urlResolution: "literal",
  });
  const payloadEdge = fragment.edges.find((edge) => edge.source === createRequest?.key && edge.kind === "carries");
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
  assert.equal(emitted.includes("customerId"), false);
  assert.equal(emitted.includes("manual order"), false);
});

test("fetch with a non-literal method stays unresolved instead of claiming GET", async () => {
  const root = await fs.mkdtemp(path.join(repoRoot, ".tmp-dynamic-method-"));
  try {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "package.json"),
      JSON.stringify({ dependencies: { react: "19.2.8" } }),
    );
    await fs.writeFile(
      path.join(root, "src", "Dynamic.tsx"),
      [
        "export function DynamicPage({ verb }: { verb: string }) {",
        "  function send() {",
        "    return fetch(\"/api/things/\", { method: verb });",
        "  }",
        "  return <button onClick={send}>Send</button>;",
        "}",
      ].join("\n"),
    );
    const fragment = await runFrontendAnalyzer(root);
    assert.equal(fragment.nodes.some((node) => (
      node.kind === "http_call"
      && node.metadata.normalizedPath === "/api/things/"
      && node.metadata.method === "GET"
    )), false, "computed method must not be asserted as GET");
    const unresolved = nodeFor(fragment, "unresolved_target", { reasonCode: "dynamic_target_unproven" }, "src/Dynamic.tsx");
    assert.ok(unresolved, "computed method should surface as an unresolved target");
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("ambiguous component names do not create render links", async () => {
  const root = await fs.mkdtemp(path.join(repoRoot, ".tmp-ambiguous-components-"));
  try {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(path.join(root, "package.json"), JSON.stringify({ dependencies: { react: "19.2.8" } }));
    await fs.writeFile(path.join(root, "src", "App.tsx"), [
      "import { Route, Routes } from \"react-router-dom\";",
      "export function App() { return <Routes><Route path=\"/\" element={<Shared />} /></Routes>; }",
    ].join("\n"));
    await fs.writeFile(path.join(root, "src", "One.tsx"), "export function Shared() { return <main>one</main>; }\n");
    await fs.writeFile(path.join(root, "src", "Two.tsx"), "export function Shared() { return <main>two</main>; }\n");

    const fragment = await runFrontendAnalyzer(root);
    const route = nodeFor(fragment, "frontend_route", { framework: "react", declaredPath: "/" });
    const sharedComponents = fragment.nodes.filter((node) => node.kind === "component" && node.label === "Shared");
    assert.ok(route);
    assert.equal(sharedComponents.length, 2);
    assert.equal(sharedComponents.some((component) => hasEdge(fragment, route, "renders", component)), false);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
