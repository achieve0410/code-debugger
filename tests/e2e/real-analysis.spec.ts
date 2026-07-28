import { expect, test, type Page } from "@playwright/test";
import { readFile, readdir, unlink } from "node:fs/promises";
import { join } from "node:path";

test.use({ trace: "off", screenshot: "off", video: "off" });

const fixture = {
  "react-django": {
    event: "button.onClick",
    eventSource: { path: "frontend/src/HomePage.tsx", line: 21 },
    httpSource: { path: "frontend/src/HomePage.tsx", line: 9 },
    payloadSource: { path: "frontend/src/HomePage.tsx", line: 9 },
    resolutionOccurrence: [1, 2] as const,
    runtimeResolutionEvidence: "Inferred",
    runtimeResolutionOccurrence: [1, 2] as const,
    runtimeInspectorEvidence: "Inferred",
    runtimeInspectorRecords: ["Kind: Inferred;"],
  },
  "vue-django": {
    event: "button.@click",
    eventSource: { path: "frontend/src/HomePage.vue", line: 4 },
    httpSource: { path: "frontend/src/HomePage.vue", line: 19 },
    payloadSource: { path: "frontend/src/HomePage.vue", line: 19 },
    resolutionOccurrence: undefined,
    runtimeResolutionEvidence: "Observed",
    runtimeResolutionOccurrence: undefined,
    runtimeInspectorEvidence: "Inferred + Observed",
    runtimeInspectorRecords: [
      "Kind: Inferred;",
      "Kind: Observed; Adapter: kg_debugger.runtime; Adapter version: 1; Basis: runtime coherent resolution"
    ],
  }
} as const;

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

test.afterEach(async ({}, testInfo) => {
  try {
    await scanCapabilityArtifacts(testInfo.outputDir);
  } finally {
    capabilityArtifactBytes = null;
  }
});

const frontendRoute = (page: Page) => page.getByTestId("route-option-/");

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
type OutlineSource = { repository: string; path: string; line?: number };
const outlineNodeName = (label: string, kind: string, evidence = "Inferred", source?: OutlineSource) => {
  const sourcePattern = source === undefined
    ? "[a-z][a-z0-9._-]*/[^;]+(?::\\d+)?"
    : `${escapeRegExp(source.repository)}/${escapeRegExp(source.path)}${source.line === undefined ? "" : `:${source.line}`}`;
  return new RegExp(
    `^Node: ${escapeRegExp(label)}; kind: ${escapeRegExp(kind.replaceAll("_", " "))}; evidence: ${escapeRegExp(evidence)}; source: ${sourcePattern}$`
  );
};
const outlineEdgeName = (source: string, sourceKind: string, sourceRepository: string, sourcePathPattern: string, target: string, targetKind: string, targetRepository: string, targetPathPattern: string, evidence = "Inferred", occurrence?: readonly [number, number]) => new RegExp(
  `^Edge: resolves to; from ${escapeRegExp(source)} \\(${escapeRegExp(sourceKind.replaceAll("_", " "))}, ${escapeRegExp(sourceRepository)}/${sourcePathPattern}\\) to ${escapeRegExp(target)} \\(${escapeRegExp(targetKind.replaceAll("_", " "))}, ${escapeRegExp(targetRepository)}/${targetPathPattern}\\); evidence: ${escapeRegExp(evidence)}${occurrence ? `; occurrence ${occurrence[0]} of ${occurrence[1]}` : ""}$`
);
const resolutionOutlineEdge = (page: Page, repository: string, evidence: string, occurrence?: readonly [number, number]) => page.getByRole("button", {
  name: outlineEdgeName("Request payload", "request_payload", repository, "frontend/src/HomePage\\.(?:tsx|vue)", "URL pattern", "django_url_pattern", repository, "backend/shop/urls\\.py", evidence, occurrence)
});

async function expectOutlineNode(page: Page, label: string, kind: string, evidence = "Inferred", source?: OutlineSource) {
  await page.getByTestId("outline-search").fill(`${label} ${kind.replaceAll("_", " ")} ${evidence}`);
  const node = page.getByRole("button", { name: outlineNodeName(label, kind, evidence, source) });
  await expect(node).toBeVisible();
  await node.focus();
  await node.press("Enter");
  await expect(page.getByTestId("selected-detail")).toContainText(`Kind${kind.replaceAll("_", " ")}`);
}

async function expectOutlineEdge(page: Page, repository: string, evidence = "Inferred", occurrence?: readonly [number, number]) {
  await page.getByTestId("outline-search").fill(`Request payload URL pattern resolves to ${evidence}`);
  const edge = resolutionOutlineEdge(page, repository, evidence, occurrence);
  await expect(edge).toBeVisible();
  await edge.focus();
  await edge.press("Enter");
  return edge;
}

async function expectInspectorField(section: ReturnType<Page["getByTestId"]>, label: string, value: string | RegExp) {
  const field = section.locator("dt").filter({ hasText: new RegExp(`^${escapeRegExp(label)}$`) }).locator("..");
  await expect(field).toHaveText(value instanceof RegExp ? new RegExp(`^${escapeRegExp(label)}${value.source}$`) : `${label}${value}`);
}

async function expectResolutionInspector(page: Page, evidence: string, records: readonly string[]) {
  const inspector = page.getByTestId("inspector");
  const relationship = page.getByTestId("inspector-relationship");
  await expectInspectorField(relationship, "Kind", "resolves to");
  await expectInspectorField(relationship, "Source label", "Request payload");
  await expectInspectorField(relationship, "Source kind", "request payload");
  await expectInspectorField(relationship, "Target label", "URL pattern");
  await expectInspectorField(relationship, "Target kind", "django url pattern");
  await expectInspectorField(relationship, "Confidence", /\d+\.\d%/);
  const metadata = page.getByTestId("inspector-metadata");
  await expectInspectorField(metadata, "Resolution tier", "exact endpoint");
  await expectInspectorField(metadata, "Target repository", /(?:react-django|vue-django)/);
  await expectInspectorField(page.getByTestId("inspector-diagnostics"), "Diagnostics", "No diagnostics for this selection.");

  const evidenceSection = page.getByTestId("inspector-evidence");
  await expectInspectorField(evidenceSection, "Summary", evidence);
  const evidenceRows = (await evidenceSection.locator("dl > div").allTextContents()).filter((text) => text.startsWith("Evidence record "));
  expect(evidenceRows).toHaveLength(records.length);
  for (const [index, record] of records.entries()) {
    const row = evidenceRows[index];
    if (record.endsWith(";")) {
      expect(row).toMatch(new RegExp(`^Evidence record ${index + 1}${escapeRegExp(record)} Adapter: [^;]+; Adapter version: [^;]+; Basis: [^;]+$`));
    } else {
      expect(row).toBe(`Evidence record ${index + 1}${record}`);
    }
  }
  await expect(inspector).toContainText("Evidence");
}

async function installRuntimeEvidence(page: Page): Promise<number> {
  const result = await page.evaluate(async () => {
    const config = await fetch("/api/config", { cache: "no-store" });
    const { mutationCapability } = await config.json() as { mutationCapability: string };
    const response = await fetch("/api/runtime", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-kg-debugger-capability": mutationCapability
      },
      body: JSON.stringify({
        captureId: "real-e2e-capture",
        method: "GET",
        path: "/api/items/",
        status: 200
      })
    });
    return { capability: mutationCapability, status: response.status };
  });
  capabilityArtifactBytes = Buffer.from(result.capability, "utf8");
  return result.status;
}

test("real fixture exposes the minimized frontend-to-Django route chain and terminal alternatives", async ({ page }, testInfo) => {
  const expected = fixture[testInfo.project.name as keyof typeof fixture];
  try {
  await page.goto("/");
  const analyzed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/analyze");
  await page.getByTestId("analyze-button").click();
  await analyzed;
  await expect(page.getByTestId("operation-status")).toHaveText("Static analysis complete.");
  await expect(page.getByTestId("snapshot-status")).toHaveText("Static snapshot");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await frontendRoute(page).click();
  await page.getByRole("button", { name: "Expand all", exact: true }).click();

  await expectOutlineNode(page, "HomePage", "page");
  await expectOutlineNode(page, expected.event, "ui_event", "Inferred", { repository: testInfo.project.name, ...expected.eventSource });
  await expectOutlineNode(page, "loadItems", "function");
  await expectOutlineNode(page, "GET /api/items/", "http_call", "Inferred", { repository: testInfo.project.name, ...expected.httpSource });
  await expectOutlineNode(page, "Request payload", "request_payload", "Inferred", { repository: testInfo.project.name, ...expected.payloadSource });
  await expectOutlineNode(page, "URL pattern", "django_url_pattern");
  await expectOutlineEdge(page, testInfo.project.name, "Inferred", expected.resolutionOccurrence);
  await expectResolutionInspector(page, "Inferred", ["Kind: Inferred;"]);
  await expectOutlineNode(page, "item_list", "django_view");
  await expectOutlineNode(page, "list_active_items", "function");
  await expectOutlineNode(page, "filter", "query_boundary");
  await expectOutlineNode(page, "Item", "model");
  await expectOutlineNode(page, "GET https://inventory.example.test", "external_service");
  await expectOutlineNode(page, "Unresolved", "unresolved_target", "Unresolved");
  } finally {
    await scanCapabilityArtifacts(testInfo.outputDir);
    capabilityArtifactBytes = null;
  }
});

test("real fixture installs capture analysis directly, exposes transient observed evidence, and refreshes static data explicitly", async ({ page }, testInfo) => {
  const expected = fixture[testInfo.project.name as keyof typeof fixture];
  let graphGets = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/graph") graphGets += 1;
  });

  try {
  const initialGraph = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/graph");
  await page.goto("/");
  await initialGraph;
  await expect(page.getByTestId("analyze-button")).toBeEnabled();
  const initialGraphGets = graphGets;

  expect(await installRuntimeEvidence(page)).toBe(202);

  const analyzeRequest = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/analyze");
  const captureInput = page.getByTestId("runtime-capture-input");
  await captureInput.fill("real-e2e-capture");
  await expect(captureInput).toHaveValue("real-e2e-capture");
  await page.getByTestId("analyze-button").click();
  const request = await analyzeRequest;
  const requestAssertions = await (async () => {
    const headers = await request.allHeaders();
    const body = request.postDataJSON() as Record<string, unknown>;
    const capability = headers["x-kg-debugger-capability"];
    const capabilityGrammarIsValid = typeof capability === "string"
      && capability.length >= 32
      && [...capability].every((character) => (character >= "A" && character <= "Z") || (character >= "a" && character <= "z") || (character >= "0" && character <= "9") || character === "_" || character === "-");
    return {
      methodIsPost: request.method() === "POST",
      pathIsAnalyze: new URL(request.url()).pathname === "/api/analyze",
      bodyHasExpectedShape: Object.keys(body).length === 1 && Object.keys(body)[0] === "runtimeCaptureId" && typeof body.runtimeCaptureId === "string",
      captureIdMatches: body.runtimeCaptureId === "real-e2e-capture",
      hasExpectedOrigin: headers.origin === new URL(page.url()).origin,
      hasJsonContentType: headers["content-type"] === "application/json",
      hasCapability: typeof capability === "string" && capability.length > 0,
      capabilityGrammarIsValid
    };
  })();
  expect(requestAssertions).toEqual({
    methodIsPost: true,
    pathIsAnalyze: true,
    bodyHasExpectedShape: true,
    captureIdMatches: true,
    hasExpectedOrigin: true,
    hasJsonContentType: true,
    hasCapability: true,
    capabilityGrammarIsValid: true
  });
  await expect(page.getByTestId("operation-status")).toHaveText("Runtime capture analysis complete. Runtime evidence is transient.");
  await expect(page.getByTestId("transient-status")).toHaveText("Runtime evidence · transient");
  await expect(page.getByTestId("static-diff")).toHaveCount(0);
  const receipt = page.getByTestId("receipt-projection");
  await expect(receipt).toContainText("Transient receipt projection");
  await expect(receipt).toContainText("Current observed graph evidence from this response only");
  await expect(receipt).toContainText("Server receipt provenance timestamp");
  await expect(receipt).toContainText("Event ID");
  await expect(receipt).toContainText("Observed graph annotations");
  await expect(receipt).not.toContainText(/execution order|trace|replay|stack|receivedAt|request_body|response_body|headers|cookie|authorization/i);
  const receiptOrder = await page.getByTestId("receipt-entry").evaluateAll((items) => items.map((item) => {
    const values = [...item.querySelectorAll("dd")].map((entry) => entry.textContent ?? "");
    return [values[0], values[1]];
  }));
  expect(receiptOrder.length).toBeGreaterThan(0);
  expect(receiptOrder).toEqual([...receiptOrder].sort(([leftTime, leftId], [rightTime, rightId]) => leftTime.localeCompare(rightTime) || leftId.localeCompare(rightId)));
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  expect(graphGets).toBe(initialGraphGets);

  await frontendRoute(page).click();
  await page.getByRole("button", { name: "Expand all", exact: true }).click();
  await expectOutlineNode(page, "GET /api/items/", "http_call", "Inferred", { repository: testInfo.project.name, ...expected.httpSource });
  await expectOutlineNode(page, "Request payload", "request_payload", "Inferred", { repository: testInfo.project.name, ...expected.payloadSource });
  await expectOutlineNode(page, "URL pattern", "django_url_pattern", "Observed");
  await expectOutlineEdge(page, testInfo.project.name, expected.runtimeResolutionEvidence, expected.runtimeResolutionOccurrence);
  await expectResolutionInspector(page, expected.runtimeInspectorEvidence, expected.runtimeInspectorRecords);
  await expectOutlineNode(page, "GET https://inventory.example.test", "external_service");
  await expectOutlineNode(page, "Unresolved", "unresolved_target", "Unresolved");

  await page.getByTestId("refresh-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Static snapshot refreshed.");
  await expect(page.getByTestId("transient-status")).toHaveCount(0);
  await expect(page.getByTestId("receipt-projection")).toHaveCount(0);
  await expect(page.getByTestId("snapshot-status")).toHaveText("Static snapshot");
  await expect(page.getByTestId("static-diff")).toContainText("Memory-only static-vs-static comparison");
  await expectOutlineNode(page, "Request payload", "request_payload", "Inferred", { repository: testInfo.project.name, ...expected.payloadSource });
  await expectOutlineNode(page, "URL pattern", "django_url_pattern");
  await expectOutlineEdge(page, testInfo.project.name, "Inferred", expected.resolutionOccurrence);
  await expectResolutionInspector(page, "Inferred", ["Kind: Inferred;"]);
  await expect(page.getByTestId("selected-detail")).not.toContainText("Observed");
  await expectOutlineNode(page, "GET https://inventory.example.test", "external_service");
  await expectOutlineNode(page, "Unresolved", "unresolved_target", "Unresolved");
  expect(graphGets).toBe(initialGraphGets + 1);
  } finally {
    await scanCapabilityArtifacts(testInfo.outputDir);
    capabilityArtifactBytes = null;
  }
});
