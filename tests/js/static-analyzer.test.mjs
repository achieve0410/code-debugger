import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

import { BUILTIN_CONVERTERS, URL_PROOF_VERSION, validateBoundedUrlProof } from "../../analyzers/contracts.mjs";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const analyzer = path.join(repoRoot, "analyzers/index.mjs");

async function analyzeFiles(files) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-static-analyzer-"));
  try {
    await Promise.all(Object.entries(files).map(async ([relative, source]) => {
      const file = path.join(root, relative);
      await fs.mkdir(path.dirname(file), { recursive: true });
      await fs.writeFile(file, source);
    }));
    const { stdout } = await execFileAsync(process.execPath, [analyzer, "--repository", "frontend", root], { cwd: repoRoot });
    return JSON.parse(stdout);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
}

async function analyze(source) {
  return analyzeFiles({ "Page.tsx": source });
}

test("producer contract accepts only exact minimized bounded proofs", () => {
  const proof = {
    version: URL_PROOF_VERSION,
    callKey: "http.react.get",
    normalizedPath: "/api/items/{p0}/",
    placeholders: [{ token: "p0", segmentIndex: 2, memberCount: 2, acceptedConverters: ["int", "slug", "str"] }],
  };
  assert.deepEqual(BUILTIN_CONVERTERS, ["int", "slug", "str", "uuid"]);
  assert.equal(validateBoundedUrlProof(proof), true);
  assert.equal(validateBoundedUrlProof({ ...proof, placeholders: [{ ...proof.placeholders[0], acceptedConverters: ["str", "int"] }] }), false);
  assert.equal(validateBoundedUrlProof({ ...proof, extra: true }), false);
});

test("finite raw and encoded domains emit value-free proof sidecars", async () => {
  const cases = [
    ["raw string", `function Page() { return fetch(\`/api/items/\${"12345"}/\`); }`, 1, ["int", "slug", "str"], ["12345"]],
    ["raw numeric", `function Page() { return fetch(\`/api/items/\${42}/\`); }`, 1, ["int", "slug", "str"], ["42"]],
    ["raw conditional", `function Page() { return fetch(\`/api/items/\${flag ? "12345" : "67890"}/\`); }`, 2, ["int", "slug", "str"], ["12345", "67890"]],
    ["raw const", `function Page() { const id = flag ? "12345" : "67890"; return fetch(\`/api/items/\${id}/\`); }`, 2, ["int", "slug", "str"], ["12345", "67890"]],
    ["encoded string", `function Page() { return fetch(\`/api/items/\${encodeURIComponent("12345")}/\`); }`, 1, ["int", "slug", "str"], ["12345"]],
    ["encoded numeric", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(42)}/\`); }`, 1, ["int", "slug", "str"], ["42"]],
    ["encoded conditional", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(flag ? "12345" : "67890")}/\`); }`, 2, ["int", "slug", "str"], ["12345", "67890"]],
    ["encoded const", `function Page() { const id = flag ? "12345" : "67890"; return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`, 2, ["int", "slug", "str"], ["12345", "67890"]],
  ];
  for (const [label, source, memberCount, acceptedConverters, members] of cases) {
    const fragment = await analyze(source);
    const call = fragment.nodes.find((node) => node.kind === "http_call");
    const proof = fragment.boundedUrlProofs[0];
    assert.equal(call.metadata.urlResolution, "bounded_template", label);
    assert.equal(call.metadata.normalizedPath, "/api/items/{p0}/", label);
    assert.equal(validateBoundedUrlProof(proof), true, label);
    assert.deepEqual(proof.placeholders, [{
      token: "p0",
      segmentIndex: 2,
      memberCount,
      acceptedConverters,
    }], label);
    const emitted = JSON.stringify(fragment);
    for (const member of members) assert.equal(emitted.includes(JSON.stringify(member)), false, label);
  }
});


test("unapproved scalar and encoder forms remain unbounded without serialized values", async () => {
  const secret = "bounded-proof-secret";
  const cases = [
    ["array member", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(["${secret}"])}/\`); }`],
    ["boolean true", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(true)}/\`); }`],
    ["boolean false", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(false)}/\`); }`],
    ["null", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(null)}/\`); }`],
    ["negative number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(-1)}/\`); }`],
    ["fractional number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(1.5)}/\`); }`],
    ["exponent number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(1e3)}/\`); }`],
    ["hexadecimal number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(0x10)}/\`); }`],
    ["binary number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(0b10)}/\`); }`],
    ["octal number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(0o10)}/\`); }`],
    ["bigint number", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(1n)}/\`); }`],
    ["NaN", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(NaN)}/\`); }`],
    ["Infinity", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(Infinity)}/\`); }`],
    ["numeric separator", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(1_000)}/\`); }`],
    ["runtime exponent boundary", `function Page() { return fetch(\`/api/items/\${encodeURIComponent(1000000000000000000000)}/\`); }`],
    ["parameter", `function Page(id) { return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`],
    ["var binding", `function Page() { var id = "${secret}"; return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`],
    ["let binding", `function Page() { let id = "${secret}"; return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`],
    ["mutable const assignment", `function Page() { const id = "${secret}"; id = "other"; return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`],
    ["const update", `function Page() { const id = 1; id++; return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`],
    ["duplicate binding", `function Page() { const id = "1"; const id = "2"; return fetch(\`/api/items/\${encodeURIComponent(id)}/\`); }`],
    ["encoder var shadow", `function Page() { var encodeURIComponent = fake; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder let shadow", `function Page() { let encodeURIComponent = fake; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder const shadow", `function Page() { const encodeURIComponent = fake; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder function shadow", `function Page() { function encodeURIComponent(value) { return value; } return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder class shadow", `function Page() { class encodeURIComponent {} return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder import shadow", `import { encodeURIComponent } from "encoder"; function Page() { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder catch shadow", `function Page() { try {} catch (encodeURIComponent) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); } }`],
    ["encoder destructuring shadow", `function Page() { const { encodeURIComponent } = helpers; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder parameter shadow", `function Page(encodeURIComponent) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder duplicate binding", `function Page() { const encodeURIComponent = fake; const encodeURIComponent = other; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder named function expression shadow", `function Page() { const request = function encodeURIComponent() { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }; return request(); }`],
    ["encoder nested named function expression shadow", `function Page() { const outer = function outer() { const request = function encodeURIComponent() { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }; return request(); }; return outer(); }`],
    ["encoder named class expression shadow", `function Page() { const Request = class encodeURIComponent { request() { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); } }; return new Request().request(); }`],
    ["encoder nested named class expression shadow", `function Page() { const Outer = class outer { request() { const Request = class encodeURIComponent { request() { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); } }; return new Request().request(); } }; return new Outer().request(); }`],
    ["encoder assignment", `function Page() { encodeURIComponent = fake; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["encoder update", `function Page() { encodeURIComponent++; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["globalThis encoder assignment", `function Page() { globalThis.encodeURIComponent = fake; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["global encoder element assignment", `function Page() { global["encodeURIComponent"] = fake; return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
  ];
  for (const [label, source] of cases) {
    const fragment = await analyze(source);
    const call = fragment.nodes.find((node) => node.kind === "http_call");
    assert.ok(call, label);
    assert.equal(call.metadata.urlResolution, "unbounded", label);
    assert.deepEqual(fragment.boundedUrlProofs, [], label);
    const emitted = JSON.stringify(fragment);
    assert.equal(emitted.includes(secret), false, label);
    assert.equal(emitted.includes("encodeURIComponent("), false, label);
  }
});
test("destructured encoder parameters remain unbounded without values or source expressions", async () => {
  const secret = "destructured-encoder-secret";
  const cases = [
    ["object", `function Page({ encodeURIComponent }) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["renamed default object", `function Page({ encoder: encodeURIComponent = fallback }) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["array", `function Page([encodeURIComponent]) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["object rest", `function Page({ ...encodeURIComponent }) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["array rest default", `function Page([head = fallback, ...encodeURIComponent]) { return fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`); }`],
    ["nested arrow", `function Page() { return (({ encodeURIComponent = fallback }) => fetch(\`/api/items/\${encodeURIComponent("${secret}")}/\`))(helpers); }`],
  ];
  for (const [label, source] of cases) {
    const fragment = await analyze(source);
    const call = fragment.nodes.find((node) => node.kind === "http_call");
    assert.ok(call, label);
    assert.equal(call.metadata.urlResolution, "unbounded", label);
    assert.deepEqual(fragment.boundedUrlProofs, [], label);
    assert.equal(fragment.nodes.some((node) => node.kind === "unresolved_target"), true, label);
    assert.equal(fragment.edges.some((edge) => (
      edge.source === call.key && edge.kind === "resolves_to"
        && edge.metadata.resolutionTier === "unbounded"
    )), true, label);
    const emitted = JSON.stringify(fragment);
    assert.equal(emitted.includes(secret), false, label);
    assert.equal(emitted.includes("encodeURIComponent("), false, label);
  }
});

test("explicit repository is required and no Django nodes are produced", async () => {
  const fragment = await analyze(`function Page() { return fetch("/api/items/"); }`);
  assert.equal(fragment.nodes.some((node) => node.kind === "django_url_pattern"), false);
  await assert.rejects(execFileAsync(process.execPath, [analyzer, repoRoot], { cwd: repoRoot }));
});
test("literal local queries retain only endpoint and query structure", async () => {
  const fragment = await analyze(`function Page() { return fetch("/api/items/?page=2&accessToken=private-value"); }`);
  const call = fragment.nodes.find((node) => node.kind === "http_call");
  assert.deepEqual(call.metadata, {
    method: "GET",
    urlResolution: "literal",
    normalizedPath: "/api/items/",
    endpointId: "GET /api/items/",
    queryFieldCount: 2,
    hasSensitiveQuery: true,
  });
  const payload = fragment.nodes.find((node) => node.kind === "request_payload");
  assert.deepEqual(payload.metadata, {
    payloadKinds: ["query"],
    bodyShape: "none",
    bodyFieldCount: 0,
    queryFieldCount: 2,
    hasSensitiveFields: true,
  });
  const emitted = JSON.stringify(fragment);
  assert.equal(emitted.includes("?page="), false);
  assert.equal(emitted.includes("accessToken"), false);
  assert.equal(emitted.includes("private-value"), false);
});

test("external literals emit minimized terminal boundaries without fetching", async () => {
  const fragment = await analyze(`function Page() { return fetch("https://Api.Example.com:8443/private/ledger?account=123&api_key=top-secret"); }`);
  const call = fragment.nodes.find((node) => node.kind === "http_call");
  const external = fragment.nodes.find((node) => node.kind === "external_service");
  assert.deepEqual(call.metadata, {
    method: "GET",
    urlResolution: "literal",
    normalizedPath: "/",
    queryFieldCount: 0,
    hasSensitiveQuery: false,
  });
  assert.deepEqual(external.metadata, {
    method: "GET",
    scheme: "https",
    host: "api.example.com",
    port: 8443,
    pathPresent: true,
    queryFieldCount: 2,
    hasSensitiveQuery: true,
    boundaryOnly: true,
  });
  assert.equal(fragment.edges.some((edge) => (
    edge.source === call.key && edge.target === external.key
      && edge.kind === "resolves_to" && edge.metadata.resolutionTier === "external_boundary"
  )), true);
  const emitted = JSON.stringify(fragment);
  for (const forbidden of ["/private/ledger", "account", "api_key", "top-secret", "https://Api.Example.com"]) {
    assert.equal(emitted.includes(forbidden), false);
  }
});
test("IPv6 external metadata stays unbracketed while its label is bracketed", async () => {
  const fragment = await analyze(`function Page() { return fetch("https://[2001:db8::1]:8443/private"); }`);
  const external = fragment.nodes.find((node) => node.kind === "external_service");
  assert.equal(external.metadata.host, "2001:db8::1");
  assert.equal(external.metadata.port, 8443);
  assert.equal(external.label, "GET https://[2001:db8::1]:8443");
});

test("userinfo and invalid external URLs remain unbounded", async () => {
  for (const url of ["https://user:pass@example.com/path", "https://example.com:bad/path", "ftp://example.com/path"]) {
    const fragment = await analyze(`function Page() { return fetch(${JSON.stringify(url)}); }`);
    const call = fragment.nodes.find((node) => node.kind === "http_call");
    assert.equal(call.metadata.urlResolution, "unbounded");
    assert.equal(fragment.nodes.some((node) => node.kind === "external_service"), false);
    assert.equal(fragment.edges.some((edge) => edge.kind === "resolves_to" && edge.metadata.resolutionTier === "unbounded"), true);
  }
});
test("unsafe local origins and finite scalar members fall back to unbounded without values", async () => {
  for (const url of ["//example.test/path", "/a/../b", "/a/%zz", "/a b"]) {
    const fragment = await analyze(`function Page() { return fetch(${JSON.stringify(url)}); }`);
    assert.equal(fragment.nodes.find((node) => node.kind === "http_call").metadata.urlResolution, "unbounded");
  }

  const unsafeMembers = [
    "", ".", "..",
    "member-:", "member-/", "member-?", "member-#", "member-[", "member-]", "member-@",
    "member-!", "member-$", "member-&", "member-'", "member-(", "member-)", "member-*",
    "member-+", "member-,", "member-;", "member-=", "member-%", "member-%2F", "member-%41",
    "member-\\", "member- space", "member-\t", "member-\n", "member-\r", "member-\v", "member-\f",
    "member-café", "member-\u0080", "member-😀",
  ];
  for (const member of unsafeMembers) {
    for (const [label, expression] of [
      ["scalar", JSON.stringify(member)],
      ["conditional", `flag ? ${JSON.stringify(member)} : "safe-member"`],
    ]) {
      for (const [transport, placeholder] of [
        ["raw", expression],
        ["encoded", `encodeURIComponent(${expression})`],
      ]) {
        const fragment = await analyze(`function Page() { return fetch(\`/api/\${${placeholder}}\`); }`);
        assert.equal(fragment.nodes.find((node) => node.kind === "http_call").metadata.urlResolution, "unbounded", `${transport} ${label}: ${JSON.stringify(member)}`);
        assert.deepEqual(fragment.boundedUrlProofs, [], `${transport} ${label}: ${JSON.stringify(member)}`);
      }
    }
  }
});

test("unreserved finite scalar members emit value-free bounded proofs", async () => {
  const member = "AZaz09-._~";
  for (const [expression, memberCount] of [
    [JSON.stringify(member), 1],
    [`flag ? ${JSON.stringify(member)} : "other-_~"`, 2],
  ]) {
    const fragment = await analyze(`function Page() { return fetch(\`/api/\${encodeURIComponent(${expression})}\`); }`);
    const call = fragment.nodes.find((node) => node.kind === "http_call");
    assert.equal(call.metadata.urlResolution, "bounded_template");
    assert.deepEqual(fragment.boundedUrlProofs, [{
      version: URL_PROOF_VERSION,
      callKey: call.key,
      normalizedPath: "/api/{p0}",
      placeholders: [{ token: "p0", segmentIndex: 1, memberCount, acceptedConverters: ["str"] }],
    }]);
    const emitted = JSON.stringify(fragment);
    assert.equal(emitted.includes(JSON.stringify(member)), false);
    assert.equal(emitted.includes(JSON.stringify("other-_~")), false);
  }
});
test("approved bounded converters retain their exact classifications", async () => {
  for (const [member, acceptedConverters] of [
    ["00042", ["int", "slug", "str"]],
    ["slug_value", ["slug", "str"]],
    ["123e4567-e89b-02d3-c456-426614174000", ["slug", "str", "uuid"]],
  ]) {
    const fragment = await analyze(`function Page() { return fetch(\`/api/\${encodeURIComponent(${JSON.stringify(member)})}\`); }`);
    const proof = fragment.boundedUrlProofs[0];
    assert.equal(validateBoundedUrlProof(proof), true);
    assert.deepEqual(proof.placeholders[0].acceptedConverters, acceptedConverters);
    assert.equal(JSON.stringify(fragment).includes(JSON.stringify(member)), false);
  }
});

test("unresolved calls retain independent payload topology without values", async () => {
  const fragment = await analyze(`function Page() { return fetch(target, { body: JSON.stringify({ password: "secret-value" }), params: { page: 1 } }); }`);
  const payload = fragment.nodes.find((node) => node.kind === "request_payload");
  assert.deepEqual(payload.metadata, {
    payloadKinds: ["body", "query"],
    bodyShape: "object",
    bodyFieldCount: 1,
    queryFieldCount: 1,
    hasSensitiveFields: true,
  });
  assert.equal(JSON.stringify(fragment).includes("secret-value"), false);
});
test("HTTP and Vue template identities ignore unrelated prefixes", async () => {
  const httpIdentity = async (prefix) => {
    const fragment = await analyze(`function Page() { ${prefix} return fetch("/api/items/"); }`);
    return fragment.nodes.find((node) => node.kind === "http_call").identity;
  };
  assert.equal(await httpIdentity("helper();"), await httpIdentity("helper(); another();"));

  const vueIdentities = async (prefix) => {
    const fragment = await analyzeFiles({
      "Page.vue": `<template>${prefix}<button @click="save"></button><component :is="current" /></template><script setup>function save() {}</script>`,
    });
    return fragment.nodes
      .filter((node) => node.kind === "ui_event" || node.kind === "unresolved_target")
      .map((node) => node.identity)
      .sort();
  };
  assert.deepEqual(await vueIdentities("<div></div>"), await vueIdentities("<aside></aside><span></span>"));
});

test("Vue event modifiers are emitted as canonical tokens", async () => {
  const fragment = await analyzeFiles({
    "Page.vue": `<template><button @click.stop.prevent="save"></button></template><script setup>function save() {}</script>`,
  });
  const event = fragment.nodes.find((node) => node.kind === "ui_event");
  assert.deepEqual(event.metadata.modifiers, ["prevent", "stop"]);
});
test("Vue update events are emitted as canonical tokens", async () => {
  const fragment = await analyzeFiles({
    "Page.vue": `<template><Dialog @update:modelValue="save" /></template><script setup>function save() {}</script>`,
  });
  const event = fragment.nodes.find((node) => node.kind === "ui_event");
  assert.equal(event.metadata.eventKind, "update.modelvalue");
});
test("Vue components do not emit illegal render edges to page nodes", async () => {
  const fragment = await analyzeFiles({
    "App.vue": "<template><MemberPage /></template>",
    "MemberPage.vue": "<template><main>Members</main></template>",
  });
  const app = fragment.nodes.find((node) => node.label === "App");
  const page = fragment.nodes.find((node) => node.label === "MemberPage");
  assert.equal(page.kind, "page");
  assert.equal(
    fragment.edges.some(
      (edge) => edge.source === app.key && edge.kind === "renders" && edge.target === page.key,
    ),
    false,
  );
});
test("Nuxt catch-all page routes remain unresolved", async () => {
  const fragment = await analyzeFiles({
    "nuxt.config.ts": "export default defineNuxtConfig({})",
    "pages/[...slug].vue": "<template><main>Catch all</main></template>",
  });
  assert.equal(
    fragment.nodes.some(
      (node) => node.kind === "frontend_route" && node.metadata.declaredPath === "/:slug*",
    ),
    false,
  );
  assert.ok(
    fragment.nodes.some((node) => node.kind === "unresolved_target"),
  );
});
test("hidden agents support files are not analyzed as application source", async () => {
  const fragment = await analyzeFiles({
    ".agents/skills/HiddenPage.jsx": "export function HiddenPage() { return <main />; }",
    "src/App.jsx": "export function App() { return <main />; }",
  });
  assert.equal(
    fragment.nodes.some((node) => node.source.path.startsWith(".agents/")),
    false,
  );
});
test("Vue child routes are absolute and unsupported catch-alls remain unresolved", async () => {
  const fragment = await analyzeFiles({
    "src/router/routes.js": `export default [{ path: "/users", children: [{ path: "settings", component: () => import("../pages/Settings.vue") }] }, { path: "/:catchAll(.*)*" }]`,
    "src/pages/Settings.vue": `<template><div /></template>`,
  });
  const route = fragment.nodes.find((node) => node.kind === "frontend_route" && node.metadata.declaredPath === "/users/settings");
  assert.equal(route.metadata.declaredPath, "/users/settings");
  assert.equal(fragment.nodes.filter((node) => node.kind === "frontend_route").length, 2);
  assert.equal(fragment.nodes.filter((node) => node.kind === "unresolved_target").length, 1);
});
test("duplicate React component names never fabricate a render target", async () => {
  const fragment = await analyzeFiles({
    "App.tsx": `function App() { return <Child />; }`,
    "one.tsx": `function Child() { return <div />; }`,
    "two.tsx": `function Child() { return <div />; }`,
  });
  const app = fragment.nodes.find((node) => node.label === "App");
  const childKeys = new Set(fragment.nodes.filter((node) => node.label === "Child").map((node) => node.key));
  assert.equal(childKeys.size, 2);
  assert.equal(fragment.edges.some((edge) => edge.source === app.key && edge.kind === "renders" && childKeys.has(edge.target)), false);
});
