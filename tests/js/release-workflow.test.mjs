import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const repoRoot = path.resolve(import.meta.dirname, "../..");
const workflowPath = path.join(repoRoot, ".github/workflows/release.yml");

test("tagged release workflow verifies, smokes, attests, and publishes", async () => {
  const workflow = await fs.readFile(workflowPath, "utf8");

  assert.match(workflow, /^on:\n  push:\n    tags:\n      - "v\*\.\*\.\*"\n/mu);
  assert.doesNotMatch(workflow, /^\s+pull_request:/mu);
  assert.doesNotMatch(workflow, /^\s+workflow_dispatch:/mu);
  assert.match(workflow, /^permissions:\n  contents: read\n/mu);

  assert.match(workflow, /^  verify:\n/mu);
  assert.match(workflow, /run: \.\/scripts\/check\.sh/u);
  assert.match(
    workflow,
    /run: \.\/scripts\/package-release\.sh release-artifacts "\$\{GITHUB_REF_NAME\}"/u,
  );

  assert.match(workflow, /^  smoke:\n/mu);
  assert.match(workflow, /- os: ubuntu-24\.04\n\s+install_playwright_deps: "1"/u);
  assert.match(workflow, /- os: macos-15\n\s+install_playwright_deps: "0"/u);
  assert.match(workflow, /run: \.\/scripts\/install-smoke\.sh/u);

  assert.match(workflow, /^  publish:\n/mu);
  assert.match(workflow, /needs: \[verify, smoke\]/u);
  assert.equal(workflow.match(/contents: write/gu)?.length, 1);
  assert.equal(workflow.match(/id-token: write/gu)?.length, 1);
  assert.equal(workflow.match(/attestations: write/gu)?.length, 1);
  assert.match(workflow, /dependency-graph\/sbom/u);
  assert.match(workflow, /release-artifacts\/sbom\.spdx\.json/u);
  assert.match(workflow, /uses: actions\/attest@[0-9a-f]{40}/u);
  assert.match(workflow, /gh release create "\$GITHUB_REF_NAME"/u);
  assert.match(workflow, /gh release upload "\$GITHUB_REF_NAME"/u);
});
