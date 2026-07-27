import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(import.meta.dirname, "../..");
const analyzer = path.join(repoRoot, "analyzers/index.mjs");

async function runAnalyzerRoot(root, options = []) {
  const { stdout } = await execFileAsync(process.execPath, [
    analyzer,
    "--repository",
    "fixture",
    ...options,
    root,
  ], {
    cwd: repoRoot,
    maxBuffer: 1024 * 1024 * 8,
  });
  return JSON.parse(stdout);
}

test("an oversized or broken SFC does not prevent healthy files from being analyzed", async () => {
  const root = await fs.mkdtemp(path.join(repoRoot, ".tmp-robustness-"));
  try {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "package.json"),
      JSON.stringify({ dependencies: { vue: "3.5.40", react: "19.2.8" } }),
    );
    await fs.writeFile(
      path.join(root, "src", "Good.vue"),
      "<template><button @click=\"go\">Go</button></template>\n<script setup>\nfunction go() { return true; }\n</script>\n",
    );
    await fs.writeFile(path.join(root, "src", "huge.ts"), `const filler = "${"x".repeat(1024 * 1024 + 16)}";\n`);

    const fragment = await runAnalyzerRoot(root, ["--frontend-only"]);
    assert.ok(
      fragment.nodes.some((node) => (
        node.kind === "component"
        && node.source.path === "src/Good.vue"
        && node.metadata.frameworkOwners.includes("vue")
      )),
      "healthy files must still be analyzed",
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("window.location branches stay unresolved without keyword-based heuristics", async () => {
  const root = await fs.mkdtemp(path.join(repoRoot, ".tmp-branch-signal-"));
  try {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "package.json"),
      JSON.stringify({ dependencies: { react: "19.2.8" } }),
    );
    await fs.writeFile(
      path.join(root, "src", "Panel.tsx"),
      [
        "export function Panel() {",
        "  const banner = window.location.hash;",
        "  return <div>{banner}</div>;",
        "}",
        "export function InnocentDynamicName() {",
        "  const label = \"Dynamic label text\";",
        "  return label.length > 3 ? <b>{label}</b> : <i>{label}</i>;",
        "}",
      ].join("\n"),
    );

    const fragment = await runAnalyzerRoot(root, ["--frontend-only"]);
    const branches = fragment.edges.filter((edge) => edge.kind === "branches_to");
    assert.equal(branches.length, 1, "only window.location syntax produces a dynamic branch");
    const source = fragment.nodes.find((node) => node.key === branches[0].source);
    const target = fragment.nodes.find((node) => node.key === branches[0].target);
    assert.equal(source?.source.path, "src/Panel.tsx");
    assert.equal(target?.kind, "unresolved_target");
    assert.equal(target?.source.path, "src/Panel.tsx");
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
