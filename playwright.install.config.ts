import { defineConfig } from "@playwright/test";
import { createHash, X509Certificate } from "node:crypto";
import { readFileSync } from "node:fs";

const certificate = new X509Certificate(readFileSync("pem/cert.pem"));
const spkiPin = createHash("sha256")
  .update(certificate.publicKey.export({ type: "spki", format: "der" }))
  .digest("base64");

export default defineConfig({
  testDir: "./tests/install",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "https://127.0.0.1:8447",
    trace: "off",
    screenshot: "only-on-failure",
    launchOptions: {
      args: [`--ignore-certificate-errors-spki-list=${spkiPin}`],
    },
  },
  webServer: {
    command:
      "PYTHONPATH=src ./venv/python3.13/bin/python -m kg_debugger.app --port 8447 --project fixtures/react-django",
    url: "https://127.0.0.1:8447/api/health",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
