import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const envScript = path.join(repoRoot, "scripts/env.sh");

test("sourced env preserves an explicit debugger root", async (t) => {
  const unrelatedCwd = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-env-"));
  t.after(() => fs.rm(unrelatedCwd, { recursive: true, force: true }));

  const { stdout } = await execFileAsync(
    "sh",
    [
      "-c",
      [
        '. "$1"',
        "printf '%s\\n' \\",
        '  "$KG_DEBUGGER_ROOT" \\',
        '  "$KG_DEBUGGER_NODE" \\',
        '  "$PYTHONPATH" \\',
        '  "$PLAYWRIGHT_BROWSERS_PATH" \\',
        '  "$KG_DEBUGGER_CERT" \\',
        '  "$KG_DEBUGGER_KEY"',
      ].join("\n"),
      "env-contract",
      envScript,
    ],
    {
      cwd: unrelatedCwd,
      env: { ...process.env, KG_DEBUGGER_ROOT: repoRoot, PYTHONPATH: "" },
    },
  );

  assert.deepEqual(stdout.trim().split("\n"), [
    repoRoot,
    path.join(repoRoot, "venv/node24.14.1/bin/node"),
    path.join(repoRoot, "src"),
    path.join(repoRoot, "venv/node24.14.1/playwright-browsers"),
    path.join(repoRoot, "pem/cert.pem"),
    path.join(repoRoot, "pem/key.pem"),
  ]);
});
