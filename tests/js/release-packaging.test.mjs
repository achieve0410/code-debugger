import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const packageScript = path.join(repoRoot, "scripts/package-release.sh");
const installScript = path.join(repoRoot, "scripts/install-smoke.sh");
const envScript = path.join(repoRoot, "scripts/env.sh");

async function git(cwd, args) {
  return execFileAsync("git", args, { cwd });
}

async function createReleaseRepository(t, options = {}) {
  await fs.access(packageScript, fs.constants.X_OK);
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-release-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  await Promise.all([
    fs.mkdir(path.join(root, "scripts"), { recursive: true }),
    fs.mkdir(path.join(root, "src/kg_debugger"), { recursive: true }),
  ]);
  await Promise.all([
    fs.copyFile(packageScript, path.join(root, "scripts/package-release.sh")),
    fs.writeFile(
      path.join(root, "package.json"),
      `${JSON.stringify({ name: "code-debugger", version: "1.2.3" }, null, 2)}\n`,
    ),
    fs.writeFile(
      path.join(root, "package-lock.json"),
      `${JSON.stringify({
        name: "code-debugger",
        version: "1.2.3",
        lockfileVersion: 3,
        packages: { "": { name: "code-debugger", version: "1.2.3" } },
      }, null, 2)}\n`,
    ),
    fs.writeFile(
      path.join(root, "pyproject.toml"),
      `[project]\nname = "code-debugger"\nversion = "${options.mismatch ? "9.9.9" : "1.2.3"}"\n`,
    ),
    fs.writeFile(
      path.join(root, "src/kg_debugger/__init__.py"),
      '"""Release fixture."""\n\n__all__ = ["__version__"]\n\n__version__ = "1.2.3"\n',
    ),
    fs.writeFile(path.join(root, "README.md"), "# release fixture\n"),
  ]);
  await fs.chmod(path.join(root, "scripts/package-release.sh"), 0o755);

  if (options.forbidden) {
    await fs.mkdir(path.join(root, "pem"), { recursive: true });
    await fs.writeFile(path.join(root, "pem/key.pem"), "not-a-real-key\n");
  }

  await git(root, ["init", "-q"]);
  await git(root, ["config", "user.email", "release-test@example.invalid"]);
  await git(root, ["config", "user.name", "Release Test"]);
  await git(root, ["add", "."]);
  await git(root, ["commit", "-qm", "release fixture"]);
  return root;
}

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

test("release builder creates a versioned verified source archive", async (t) => {
  const root = await createReleaseRepository(t);
  const output = path.join(root, "release-artifacts");
  await fs.mkdir(output);
  const { stdout: headOutput } = await git(root, ["rev-parse", "HEAD"]);
  const head = headOutput.trim();

  await execFileAsync("./scripts/package-release.sh", [output, "v1.2.3", head], { cwd: root });

  const archiveName = "code-debugger-v1.2.3.tar.gz";
  const archive = path.join(output, archiveName);
  const checksum = `${archive}.sha256`;
  await Promise.all([fs.access(archive), fs.access(checksum)]);
  await execFileAsync("shasum", ["-a", "256", "-c", path.basename(checksum)], { cwd: output });

  const { stdout } = await execFileAsync("tar", ["-tzf", archive]);
  const entries = stdout.trim().split("\n");
  assert.ok(entries.length > 0);
  assert.ok(entries.every((entry) => entry.startsWith("code-debugger-v1.2.3/")));
  for (const forbidden of ["venv/", "node_modules/", "pem/", ".kg-debugger/", "web/dist/"]) {
    assert.equal(entries.some((entry) => entry.includes(`/${forbidden}`)), false);
  }
});

test("release builder rejects an expected commit behind HEAD", async (t) => {
  const root = await createReleaseRepository(t);
  const { stdout: previousOutput } = await git(root, ["rev-parse", "HEAD"]);
  const previous = previousOutput.trim();
  await fs.writeFile(path.join(root, "README.md"), "# second release fixture commit\n");
  await git(root, ["add", "README.md"]);
  await git(root, ["commit", "-qm", "advance release fixture"]);

  const output = path.join(root, "release-artifacts");
  await fs.mkdir(output);

  await assert.rejects(
    execFileAsync("./scripts/package-release.sh", [output, "v1.2.3", previous], { cwd: root }),
    (error) => {
      assert.match(String(error.stderr), /expected release commit does not match HEAD/u);
      return true;
    },
  );
  assert.deepEqual(await fs.readdir(output), []);
});

test("release builder rejects inconsistent public versions", async (t) => {
  const root = await createReleaseRepository(t, { mismatch: true });
  const output = path.join(root, "release-artifacts");
  await fs.mkdir(output);

  await assert.rejects(
    execFileAsync("./scripts/package-release.sh", [output], { cwd: root }),
    (error) => {
      assert.match(String(error.stderr), /version mismatch/u);
      return true;
    },
  );
});

test("release builder rejects tracked forbidden artifacts", async (t) => {
  const root = await createReleaseRepository(t, { forbidden: true });
  const output = path.join(root, "release-artifacts");
  await fs.mkdir(output);

  await assert.rejects(
    execFileAsync("./scripts/package-release.sh", [output], { cwd: root }),
    (error) => {
      assert.match(String(error.stderr), /forbidden release path/u);
      return true;
    },
  );
});

test("install smoke rejects a corrupt checksum before bootstrap", async (t) => {
  await fs.access(installScript, fs.constants.X_OK);
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-install-checksum-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  const marker = path.join(root, "bootstrap-ran");
  const payloadRoot = path.join(root, "code-debugger-v1.2.3");
  await fs.mkdir(path.join(payloadRoot, "scripts"), { recursive: true });
  await fs.writeFile(
    path.join(payloadRoot, "scripts/bootstrap.sh"),
    `#!/bin/sh\ntouch ${JSON.stringify(marker)}\n`,
  );
  await fs.chmod(path.join(payloadRoot, "scripts/bootstrap.sh"), 0o755);

  const archive = path.join(root, "code-debugger-v1.2.3.tar.gz");
  await execFileAsync("tar", ["-czf", archive, "-C", root, "code-debugger-v1.2.3"]);
  const checksum = `${archive}.sha256`;
  await fs.writeFile(checksum, `${"0".repeat(64)}  ${path.basename(archive)}\n`);

  await assert.rejects(execFileAsync(installScript, [archive, checksum], { cwd: repoRoot }));
  await assert.rejects(fs.access(marker));
});
