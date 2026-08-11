import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
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

async function readJson(relativePath) {
  return JSON.parse(await fs.readFile(path.join(repoRoot, relativePath), "utf8"));
}

function countBy(values) {
  return values.reduce((counts, value) => {
    counts[value] = (counts[value] ?? 0) + 1;
    return counts;
  }, {});
}

function stableSummary(fragment) {
  return {
    routes: fragment.routes.map((route) => route.key).sort(),
    nodeKinds: Object.fromEntries(Object.entries(countBy(fragment.nodes.map((node) => node.kind))).sort()),
    edgeKinds: Object.fromEntries(Object.entries(countBy(fragment.edges.map((edge) => edge.kind))).sort()),
    diagnostics: [...new Set(fragment.diagnostics.map((diagnostic) => diagnostic.code))].sort(),
    boundedUrlProofs: fragment.boundedUrlProofs.length,
  };
}

test("an oversized or broken SFC does not prevent healthy files from being analyzed", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-robustness-"));
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
    const diagnostic = fragment.diagnostics.find(
      (item) => item.source?.path === "src/huge.ts",
    );
    assert.match(diagnostic?.id ?? "", /^d_[0-9a-f]{64}$/u);
    assert.deepEqual(
      { ...diagnostic, id: undefined },
      {
        id: undefined,
        code: "source_read_failed",
        severity: "warning",
        message: "A source file could not be read.",
        repository: "fixture",
        source: {
          repository: "fixture",
          path: "src/huge.ts",
        },
      },
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("malformed framework sources emit diagnostics without inferred facts", async (t) => {
  for (const sample of [
    {
      framework: "react",
      file: "src/Broken.tsx",
      dependencies: { react: "19.2.8" },
      source: "export function Broken() { return <div>; }\n",
    },
    {
      framework: "vue",
      file: "src/Broken.vue",
      dependencies: { vue: "3.5.40" },
      source: "<template><button @click=\"go\"></template>\n<script setup>function go() { return true; }</script>\n",
    },
  ]) {
    await t.test(sample.framework, async () => {
      const root = await fs.mkdtemp(path.join(os.tmpdir(), `code-debugger-malformed-${sample.framework}-`));
      try {
        await fs.mkdir(path.join(root, "src"), { recursive: true });
        await fs.writeFile(
          path.join(root, "package.json"),
          JSON.stringify({ dependencies: sample.dependencies }),
        );
        await fs.writeFile(path.join(root, sample.file), sample.source);

        const fragment = await runAnalyzerRoot(root, ["--frontend-only"]);
        assert.equal(
          fragment.nodes.some((node) => node.source.path === sample.file),
          false,
          "malformed source must not emit inferred nodes",
        );
        const diagnostic = fragment.diagnostics.find(
          (item) => item.source?.path === sample.file,
        );
        assert.match(diagnostic?.id ?? "", /^d_[0-9a-f]{64}$/u);
        assert.deepEqual(
          { ...diagnostic, id: undefined },
          {
            id: undefined,
            code: "unsupported_syntax",
            severity: "warning",
            message: "Unsupported syntax was left unresolved.",
            repository: "fixture",
            source: {
              repository: "fixture",
              path: sample.file,
            },
          },
        );
      } finally {
        await fs.rm(root, { recursive: true, force: true });
      }
    });
  }
});

test("plain TypeScript projects emit an explicit unsupported diagnostic", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-plain-ts-"));
  try {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "src", "plain.ts"),
      "export async function loadItems() { return fetch('/api/items'); }\n",
    );

    const fragment = await runAnalyzerRoot(root, ["--frontend-only"]);
    assert.deepEqual(stableSummary(fragment), {
      routes: [],
      nodeKinds: {},
      edgeKinds: {},
      diagnostics: ["unsupported_syntax"],
      boundedUrlProofs: 0,
    });
    const [diagnostic] = fragment.diagnostics;
    assert.equal(
      diagnostic.id,
      "d_32965cff5f83ce7da58df5a2bd507e5800cf151760306deb4cf3eb04a7af7aa2",
    );
    assert.deepEqual(
      { ...diagnostic, id: undefined },
      {
        id: undefined,
        code: "unsupported_syntax",
        severity: "warning",
        message: "Unsupported syntax was left unresolved.",
        repository: "fixture",
        source: {
          repository: "fixture",
          path: "src/plain.ts",
        },
      },
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("analyzer conformance manifest matches fixture workflow and stable frontend output", async () => {
  const manifest = await readJson("fixtures/analyzer-conformance.json");
  assert.deepEqual(Object.keys(manifest).sort(), [
    "docs",
    "fixtureCases",
    "schemaVersion",
    "supportMatrix",
    "workflow",
  ]);
  assert.equal(manifest.schemaVersion, 1);
  assert.deepEqual(manifest.workflow, {
    fixtureRoot: "fixtures/",
    expectedRoot: "fixtures/expected/",
    focusedCommand: "npm run test:js",
    fullCommand: "./scripts/check.sh",
    frontendAnalyzerCommand: "node analyzers/index.mjs --repository <namespace> --frontend-only <fixture-root>",
  });
  assert.deepEqual(manifest.docs, ["docs/analyzer-conformance.md", "CONTRIBUTING.md"]);
  assert.equal(manifest.supportMatrix.length, 4);
  assert.deepEqual(manifest.supportMatrix.map((row) => row.framework), ["React", "Vue 3", "Nuxt 3+", "Django"]);
  assert.equal(JSON.stringify(manifest).includes("plugin"), false, "manifest must not define a plugin surface");

  for (const doc of manifest.docs) {
    await fs.access(path.join(repoRoot, doc));
  }
  await fs.access(path.join(repoRoot, manifest.workflow.expectedRoot));
  for (const row of manifest.supportMatrix) {
    await fs.access(path.join(repoRoot, row.fixture));
    assert.equal(row.status, "supported-bounded");
    assert.ok(row.limitations.some((limitation) => limitation.includes("Unresolved")));
  }

  for (const fixtureCase of manifest.fixtureCases) {
    assert.equal(fixtureCase.frontendOnly, true);
    const fragment = await runAnalyzerRoot(path.join(repoRoot, fixtureCase.root), ["--frontend-only"]);
    assert.deepEqual(stableSummary(fragment), fixtureCase.summary, fixtureCase.id);
  }
});

test("analyzer conformance docs describe the maintained static workflow and limits", async () => {
  const manifest = await readJson("fixtures/analyzer-conformance.json");
  const docs = await Promise.all(manifest.docs.map(async (relativePath) => (
    [relativePath, await fs.readFile(path.join(repoRoot, relativePath), "utf8")]
  )));
  const joined = docs.map(([, text]) => text).join("\n");

  for (const expected of [
    "fixtures/",
    "fixtures/expected/",
    "fixtures/analyzer-conformance.json",
    "npm run test:js",
    "./scripts/check.sh",
    "node analyzers/index.mjs --repository <namespace> --frontend-only <fixture-root>",
    "React",
    "Vue 3",
    "Nuxt 3+",
    "Django",
    "Unresolved",
    "`server/api` routes are terminal",
    "not a plugin API",
  ]) {
    assert.ok(joined.includes(expected), `missing conformance documentation for ${expected}`);
  }

  for (const forbidden of ["remote corpus", "dynamic plugin", "loader system"]) {
    assert.ok(joined.includes(forbidden), `docs must explicitly reject ${forbidden}`);
  }
});

test("release metadata stays consistent across package, Python, README, and security docs", async () => {
  const packageJson = await readJson("package.json");
  const packageLock = await readJson("package-lock.json");
  const pyproject = await fs.readFile(path.join(repoRoot, "pyproject.toml"), "utf8");
  const init = await fs.readFile(path.join(repoRoot, "src/kg_debugger/__init__.py"), "utf8");
  const readme = await fs.readFile(path.join(repoRoot, "README.md"), "utf8");
  const security = await fs.readFile(path.join(repoRoot, "SECURITY.md"), "utf8");
  const releasing = await fs.readFile(path.join(repoRoot, "docs/releasing.md"), "utf8");

  const version = packageJson.version;
  const releaseLine = `${version.split(".").slice(0, 2).join(".")}.x`;
  assert.equal(packageLock.version, version);
  assert.equal(packageLock.packages[""].version, version);
  assert.equal(pyproject.match(/^version = "([^"]+)"$/m)?.[1], version);
  assert.equal(init.match(/^__version__ = "([^"]+)"$/m)?.[1], version);
  assert.ok(readme.includes(`Project status: early \`v${releaseLine}\``));
  assert.ok(readme.includes(`code-debugger v${version}`));
  assert.ok(security.includes(`| ${releaseLine} | Yes |`));

  for (const releaseFile of [
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "src/kg_debugger/__init__.py",
    "README.md",
    "SECURITY.md",
  ]) {
    assert.ok(releasing.includes(releaseFile), `release checklist must mention ${releaseFile}`);
  }
});

test("window.location branches stay unresolved without keyword-based heuristics", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "code-debugger-branch-signal-"));
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
