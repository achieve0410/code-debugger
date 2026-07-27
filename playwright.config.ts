import { defineConfig } from "@playwright/test";
import { createHash, X509Certificate } from "node:crypto";
import { readFileSync } from "node:fs";

const certificate = new X509Certificate(readFileSync("pem/cert.pem"));
const spkiPin = createHash("sha256")
  .update(certificate.publicKey.export({ type: "spki", format: "der" }))
  .digest("base64");

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    trace: "off",
    screenshot: "only-on-failure",
    launchOptions: {
      args: [
        `--ignore-certificate-errors-spki-list=${spkiPin}`
      ]
    }
  },
  projects: [
    {
      name: "mock-ui",
      testMatch: "execution-flow-debugger.spec.ts",
      use: { baseURL: "https://127.0.0.1:8444" }
    },
    {
      name: "react-django",
      testMatch: "real-analysis.spec.ts",
      use: { baseURL: "https://127.0.0.1:8444" }
    },
    {
      name: "vue-django",
      testMatch: "real-analysis.spec.ts",
      use: { baseURL: "https://127.0.0.1:8445" }
    },
    {
      name: "nuxt-django",
      testMatch: "nuxt-analysis.spec.ts",
      use: { baseURL: "https://127.0.0.1:8446" }
    }
  ],
  webServer: [
    {
      command:
        "PATH=$PWD/venv/node24.14.1/bin:$PATH PYTHONPATH=src ./venv/python3.13/bin/python -m kg_debugger.app --port 8444 --runtime --project fixtures/react-django",
      url: "https://127.0.0.1:8444/api/health",
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command:
        "PATH=$PWD/venv/node24.14.1/bin:$PATH PYTHONPATH=src ./venv/python3.13/bin/python -m kg_debugger.app --port 8445 --runtime --project fixtures/vue-django",
      url: "https://127.0.0.1:8445/api/health",
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command:
        "PATH=$PWD/venv/node24.14.1/bin:$PATH PYTHONPATH=src ./venv/python3.13/bin/python -m kg_debugger.app --port 8446 --project fixtures/nuxt-django",
      url: "https://127.0.0.1:8446/api/health",
      reuseExistingServer: false,
      timeout: 120000
    }
  ]
});
