import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const repoRoot = path.resolve(import.meta.dirname, "../..");
const workflowPath = path.join(repoRoot, ".github/workflows/release.yml");

test("main-dispatched release seals, verifies, attests, and drafts", async () => {
  const workflow = await fs.readFile(workflowPath, "utf8");

  assert.match(workflow, /^on:\n  workflow_dispatch:\n    inputs:\n      version:/mu);
  assert.doesNotMatch(workflow, /^\s+push:/mu);
  assert.match(workflow, /^permissions:\n  contents: read\n/mu);
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/u);

  const packageIndex = workflow.indexOf("  package:");
  const verifyIndex = workflow.indexOf("  verify:");
  const smokeIndex = workflow.indexOf("  smoke:");
  const sbomIndex = workflow.indexOf("  sbom:");
  const publishIndex = workflow.indexOf("  publish:");
  assert.ok(packageIndex > 0);
  assert.ok(packageIndex < verifyIndex);
  assert.ok(verifyIndex < smokeIndex);
  assert.ok(smokeIndex < sbomIndex);
  assert.ok(sbomIndex < publishIndex);

  const checkoutCount = workflow.match(/uses: actions\/checkout@[0-9a-f]{40}/gu)?.length ?? 0;
  assert.ok(checkoutCount >= 3);
  assert.equal(workflow.match(/persist-credentials: false/gu)?.length, checkoutCount);
  assert.match(
    workflow,
    /run: \.\/scripts\/package-release\.sh release-artifacts "\$\{RELEASE_TAG\}" "\$\{GITHUB_SHA\}"/u,
  );
  assert.ok(
    workflow.indexOf("./scripts/package-release.sh") < workflow.indexOf("./scripts/bootstrap.sh"),
    "source archive must be sealed before dependency bootstrap",
  );

  assert.match(workflow, /^  verify:\n    needs: package\n/mu);
  assert.match(workflow, /run: \.\/scripts\/check\.sh/u);
  assert.match(workflow, /^  smoke:\n    needs: package\n/mu);
  assert.match(workflow, /- os: ubuntu-24\.04\n\s+install_playwright_deps: "1"/u);
  assert.match(workflow, /- os: macos-15\n\s+install_playwright_deps: "0"/u);
  assert.match(workflow, /run: \.\/scripts\/install-smoke\.sh/u);

  assert.match(workflow, /^  sbom:\n    needs: package\n/mu);
  assert.match(workflow, /SYFT_VERSION: "1\.50\.0"/u);
  assert.match(workflow, /SYFT_COMMIT: "16223e6dd7893fe578787658ceb876257483d404"/u);
  assert.match(workflow, /SYFT_SHA256: "bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788"/u);
  assert.match(workflow, /SYFT_CHECK_FOR_APP_UPDATE: "false"/u);
  assert.match(workflow, /SYFT_JAVASCRIPT_SEARCH_REMOTE_LICENSES: "false"/u);
  assert.match(workflow, /SYFT_PYTHON_SEARCH_REMOTE_LICENSES: "false"/u);
  assert.match(workflow, /--enrich none/u);
  assert.match(workflow, /spdx-json@2\.3/u);
  assert.match(workflow, /finalize-release-sbom\.py/u);
  assert.doesNotMatch(workflow, /dependency-graph\/sbom/u);

  assert.match(workflow, /^  publish:\n    needs: \[package, verify, smoke, sbom\]\n/mu);
  assert.equal(workflow.match(/contents: write/gu)?.length, 1);
  assert.equal(workflow.match(/id-token: write/gu)?.length, 1);
  assert.equal(workflow.match(/attestations: write/gu)?.length, 1);
  assert.match(workflow, /uses: actions\/attest@1e69f48acb82d1966a394da916b4c1698aa569d6/u);
  assert.match(workflow, /uses: actions\/attest-sbom@36590ecaf038d6630c74f2da259095627d52ac11/u);
  assert.match(workflow, /subject-path: release-artifacts\/code-debugger-\$\{\{ env\.RELEASE_TAG \}\}\.tar\.gz/u);
  assert.match(workflow, /sbom-path: release-artifacts\/sbom\.spdx\.json/u);
  assert.match(workflow, /git\/refs/u);
  assert.match(workflow, /ref="refs\/tags\/\$\{RELEASE_TAG\}"/u);
  assert.match(workflow, /git\/ref\/tags\/\$\{RELEASE_TAG\}/u);
  assert.match(workflow, /gh release create "\$RELEASE_TAG"/u);
  assert.match(workflow, /--draft/u);
  assert.match(workflow, /gh release upload "\$RELEASE_TAG"/u);
  assert.doesNotMatch(workflow, /gh release edit/u);
  assert.doesNotMatch(workflow, /--draft=false/u);
});
