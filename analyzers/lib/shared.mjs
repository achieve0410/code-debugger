import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

export const ADAPTER = "execution-flow-static";
export const ADAPTER_VERSION = "1";
export const TYPESCRIPT_VERSION = ts.version;
export const FRONTEND_EXTS = new Set([".js", ".jsx", ".ts", ".tsx"]);
export const PY_EXTS = new Set([".py"]);

export function toPosix(value) {
  return value.split(path.sep).join("/");
}

export function relPath(root, filePath) {
  return toPosix(path.relative(root, filePath))
    .split("/")
    .map((segment) => encodeURIComponent(segment).replace(/[!'()*]/gu, (character) => (
      `%${character.codePointAt(0).toString(16).toUpperCase()}`
    )))
    .join("/");
}

export function readText(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

export const MAX_SOURCE_FILE_BYTES = 1024 * 1024;

export function readSourceText(filePath, builder) {
  const sourcePath = relPath(builder.root, filePath);
  try {
    const stat = fs.statSync(filePath);
    if (stat.size > MAX_SOURCE_FILE_BYTES) {
      builder.addDiagnostic({
        code: "file_skipped",
        message: `source file exceeds ${MAX_SOURCE_FILE_BYTES} bytes and was not analyzed`,
        source: { path: sourcePath },
      });
      return undefined;
    }
    return fs.readFileSync(filePath, "utf8");
  } catch (error) {
    builder.addDiagnostic({
      code: "file_skipped",
      message: `source file could not be read: ${error?.code ?? error?.name ?? "error"}`,
      source: { path: sourcePath },
    });
    return undefined;
  }
}

export function lineOf(source, offset) {
  return source.slice(0, offset).split(/\r?\n/u).length;
}

export function walkFiles(root, skipped = []) {
  const results = [];
  const ignored = new Set([
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".ssh",
    ".aws",
    ".config",
    ".quasar",
    ".next",
    ".nuxt",
    ".cache",
    ".turbo",
    ".svelte-kit",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".pnpm-store",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "out",
    "site-packages",
    "staticfiles",
    "target",
    "vendor",
  ]);

  function visit(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (error) {
      skipped.push({
        code: "directory_skipped",
        message: `directory could not be read: ${error?.code ?? error?.name ?? "error"}`,
        source: { path: relPath(root, dir) },
      });
      return;
    }
    for (const entry of entries) {
      if (ignored.has(entry.name)) continue;
      if (entry.isSymbolicLink()) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        visit(full);
      } else if (entry.isFile()) {
        results.push(full);
      }
    }
  }

  visit(root);
  return results.sort((a, b) => relPath(root, a).localeCompare(relPath(root, b)));
}

export function detectFrontendFrameworks(root, files) {
  let dependencies = {};
  const packageFile = path.join(root, "package.json");
  if (fs.existsSync(packageFile)) {
    try {
      const manifest = JSON.parse(readText(packageFile));
      dependencies = { ...(manifest.dependencies ?? {}), ...(manifest.devDependencies ?? {}) };
    } catch {
      dependencies = {};
    }
  }
  const nuxt =
    "nuxt" in dependencies
    || files.some((file) => /^nuxt\.config\.(?:js|mjs|ts)$/u.test(path.basename(file)));
  return {
    react: "react" in dependencies || files.some((file) => [".jsx", ".tsx"].includes(path.extname(file))),
    vue: "vue" in dependencies || files.some((file) => path.extname(file) === ".vue"),
    nuxt,
  };
}

export function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}
