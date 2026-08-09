import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const installScript = path.join(repoRoot, "scripts/install-smoke.sh");
const verifyScript = path.join(repoRoot, "scripts/verify-release-archive.py");

async function writeChecksum(archive) {
  const digest = crypto.createHash("sha256").update(await fs.readFile(archive)).digest("hex");
  const checksum = `${archive}.sha256`;
  await fs.writeFile(checksum, `${digest}  ${path.basename(archive)}\n`);
  return checksum;
}

async function makeArchive(root, configure) {
  const name = "code-debugger-v1.2.3";
  const payload = path.join(root, name);
  await fs.mkdir(path.join(payload, "scripts"), { recursive: true });
  await configure(payload);
  const archive = path.join(root, `${name}.tar.gz`);
  await execFileAsync("tar", ["-czf", archive, "-C", root, name], {
    env: { ...process.env, COPYFILE_DISABLE: "1" },
  });
  return { archive, checksum: await writeChecksum(archive) };
}

async function makeUnsafeMemberArchive(root, kind) {
  const archive = path.join(root, "code-debugger-v1.2.3.tar.gz");
  const python = [
    "import io",
    "import sys",
    "import tarfile",
    "archive, kind = sys.argv[1:]",
    "root = 'code-debugger-v1.2.3'",
    "with tarfile.open(archive, 'w:gz') as output:",
    "    directory = tarfile.TarInfo(root + '/')",
    "    directory.type = tarfile.DIRTYPE",
    "    output.addfile(directory)",
    "    member = tarfile.TarInfo(root + '/scripts/bootstrap.sh')",
    "    if kind == 'traversal':",
    "        member.name = root + '/../escaped'",
    "        member.size = 1",
    "        output.addfile(member, io.BytesIO(b'x'))",
    "    elif kind == 'hardlink':",
    "        member.type = tarfile.LNKTYPE",
    "        member.linkname = '../../outside-bootstrap.sh'",
    "        output.addfile(member)",
    "    elif kind == 'device':",
    "        member.type = tarfile.CHRTYPE",
    "        member.devmajor = 1",
    "        member.devminor = 3",
    "        output.addfile(member)",
  ].join("\n");
  await execFileAsync("python3.13", ["-c", python, archive, kind]);
  return { archive, checksum: await writeChecksum(archive) };
}

test("install smoke rejects a symlinked bootstrap before execution", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-symlink-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const marker = path.join(root, "outside-bootstrap-ran");
  const outsideBootstrap = path.join(root, "outside-bootstrap.sh");
  await fs.writeFile(outsideBootstrap, `#!/bin/sh\ntouch ${JSON.stringify(marker)}\n`);
  await fs.chmod(outsideBootstrap, 0o755);

  const { archive, checksum } = await makeArchive(root, async (payload) => {
    await fs.symlink(outsideBootstrap, path.join(payload, "scripts/bootstrap.sh"));
    await fs.writeFile(path.join(payload, "scripts/run.sh"), "#!/bin/sh\nexit 0\n");
    await fs.chmod(path.join(payload, "scripts/run.sh"), 0o755);
  });

  await assert.rejects(execFileAsync(installScript, [archive, checksum], {
    cwd: repoRoot,
    env: { ...process.env, INSTALL_PLAYWRIGHT_DEPS: "0" },
  }));
  await assert.rejects(fs.access(marker));
});

test("install smoke extracts verified bytes when the original path is mutable", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-mutable-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const marker = path.join(root, "install-test-ran");
  const bin = path.join(root, "bin");
  await fs.mkdir(bin);
  await fs.writeFile(path.join(bin, "npm"), `#!/bin/sh\ntouch ${JSON.stringify(marker)}\n`);
  await fs.chmod(path.join(bin, "npm"), 0o755);

  const { archive, checksum } = await makeArchive(root, async (payload) => {
    for (const script of ["bootstrap.sh", "run.sh"]) {
      await fs.writeFile(path.join(payload, "scripts", script), "#!/bin/sh\nexit 0\n");
      await fs.chmod(path.join(payload, "scripts", script), 0o755);
    }
    await fs.writeFile(
      path.join(payload, "scripts/env.sh"),
      '#!/bin/sh\n: "${KG_DEBUGGER_ROOT:?}"\nexport KG_DEBUGGER_ROOT\n',
    );
  });

  const { stdout: realTarOutput } = await execFileAsync("sh", ["-c", "command -v tar"]);
  const tarWrapper = path.join(bin, "tar");
  await fs.writeFile(
    tarWrapper,
    [
      "#!/bin/sh",
      `printf tampered > ${JSON.stringify(archive)}`,
      `exec ${JSON.stringify(realTarOutput.trim())} "$@"`,
      "",
    ].join("\n"),
  );
  await fs.chmod(tarWrapper, 0o755);

  await execFileAsync(installScript, [archive, checksum], {
    cwd: repoRoot,
    env: {
      ...process.env,
      INSTALL_PLAYWRIGHT_DEPS: "0",
      PATH: `${bin}:${process.env.PATH}`,
    },
  });
  await fs.access(marker);
});

for (const kind of ["traversal", "hardlink", "device"]) {
  test(`install smoke rejects an unsafe ${kind} archive member`, async (t) => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), `code-debugger-${kind}-`));
    t.after(() => fs.rm(root, { recursive: true, force: true }));
    const { archive, checksum } = await makeUnsafeMemberArchive(root, kind);

    await assert.rejects(execFileAsync(installScript, [archive, checksum], {
      cwd: repoRoot,
      env: { ...process.env, INSTALL_PLAYWRIGHT_DEPS: "0" },
    }));
  });
}

test("verified extraction keeps archive directories removable", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-mode-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const { archive, checksum } = await makeUnsafeMemberArchive(root, "directory");
  const destination = path.join(root, "extracted");
  await fs.mkdir(destination);

  await execFileAsync("python3.13", [verifyScript, archive, checksum, destination]);

  const extracted = path.join(destination, "code-debugger-v1.2.3");
  const mode = (await fs.stat(extracted)).mode & 0o700;
  assert.equal(mode, 0o700, "owner access is required for deterministic cleanup");
});
