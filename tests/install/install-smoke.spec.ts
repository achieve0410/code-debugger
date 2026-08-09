import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

test("fresh source archive analyzes the bundled fixture", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("code-debugger");
  await expect(page.getByLabel("Debugger status")).toContainText("API ready");

  const analyze = page.getByTestId("analyze-button");
  await expect(analyze).toBeEnabled();
  await analyze.click();

  await expect(page.getByTestId("operation-status")).toHaveText("Static analysis complete.");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByTestId("route-option-/")).toBeVisible();

  const evidencePath = process.env.KG_DEBUGGER_INSTALL_EVIDENCE;
  if (evidencePath) {
    await mkdir(path.dirname(evidencePath), { recursive: true });
    await page.screenshot({ path: evidencePath, fullPage: true });
  }
});
