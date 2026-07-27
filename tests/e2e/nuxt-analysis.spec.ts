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
