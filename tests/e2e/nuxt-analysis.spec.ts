import { expect, test } from "@playwright/test";

test("Nuxt and Django fixture produces a connected route-centered graph", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("Debugger status")).toContainText("API ready");

  await page.getByTestId("analyze-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Static analysis complete.");

  await page.getByTestId("route-option-/orders").click();
  await page.getByRole("button", { name: "Expand all" }).click();

  const outline = page.getByTestId("flow-outline");
  await expect(outline).toContainText("POST /api/orders/");
  await expect(outline).toContainText("order_collection");
  await expect(outline).toContainText("Order");
});

test("Nuxt dynamic page route keeps unproven route values unresolved", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("analyze-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Static analysis complete.");

  await page.getByTestId("route-option-/orders/:id").click();
  await page.getByRole("button", { name: "Expand all" }).click();

  const outline = page.getByTestId("flow-outline");
  await expect(outline).toContainText("GET /api/orders/{u0}");
  await expect(outline).toContainText("Unresolved");
  await expect(outline).not.toContainText("order_detail");
});

test("copies a decoded filesystem-relative source location from an analyzed Nuxt node", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "https://127.0.0.1:8446" });
  await page.goto("/");
  await page.getByTestId("analyze-button").click();
  await expect(page.getByTestId("operation-status")).toHaveText("Static analysis complete.");

  await page.getByTestId("route-option-/orders/:id").click();
  await page.getByRole("button", { name: "Expand all" }).click();
  const component = page.getByTestId("flow-outline").getByRole("button").filter({ hasText: /^\[id\]/ }).first();
  await expect(component).toBeVisible();
  await component.click();

  const source = page.getByTestId("inspector-source");
  await expect(source.getByText("frontend/pages/orders/%5Bid%5D.vue", { exact: true })).toBeVisible();
  await page.getByTestId("copy-source-location").click();
  await expect(page.getByTestId("copy-source-status")).toHaveText("Copied source location.");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe("frontend/pages/orders/[id].vue:1");
  for (const forbidden of ["/Users/", "file://", "http", "capability", "token"]) {
    expect(copied).not.toContain(forbidden);
  }
});
