#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { detectFrontendFrameworks, FRONTEND_EXTS, relPath, walkFiles } from "./lib/shared.mjs";
import { FragmentBuilder } from "./lib/builder.mjs";
import { collectReact } from "./lib/react.mjs";
import { collectVue } from "./lib/vue.mjs";
import { collectNuxt } from "./lib/nuxt.mjs";

function usage() {
  console.error("Usage: node analyzers/index.mjs --repository <namespace> [--frontend-only] [--base-path <path>]... <project-root>");
}

function main() {
  const args = process.argv.slice(2);
  let repository;
  const basePaths = [];
  const rootArgs = [];
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
    if (value === "--frontend-only") continue;
    if (value === "--repository" || value === "--base-path") {
      const optionValue = args[index + 1];
      if (!optionValue || optionValue.startsWith("--")) {
        usage(); process.exit(2);
      }
      if (value === "--repository") repository = optionValue;
      else basePaths.push(optionValue);
      index += 1;
      continue;
    }
    if (value.startsWith("--")) {
      usage(); process.exit(2);
    }
    rootArgs.push(value);
  }
  const rootArg = rootArgs.length === 1 ? rootArgs[0] : undefined;
  if (!repository || !/^[a-z][a-z0-9._-]{0,63}$/u.test(repository) || !rootArg) {
    usage(); process.exit(2);
  }
  const root = path.resolve(rootArg);
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory() || fs.lstatSync(root).isSymbolicLink()) {
    console.error("Project root does not exist or is not a directory"); process.exit(2);
  }
  const builder = new FragmentBuilder(root, repository);
  const walkDiagnostics = [];
  const files = walkFiles(root, walkDiagnostics);
  for (const diagnostic of walkDiagnostics) builder.addDiagnostic(diagnostic);
  const frameworks = detectFrontendFrameworks(root, files);
  if (!frameworks.react && !frameworks.vue && !frameworks.nuxt) {
    const source = files.find((file) => FRONTEND_EXTS.has(path.extname(file)));
    if (source) {
      builder.addDiagnostic({
        code: "unsupported_syntax",
        source: { path: relPath(root, source) },
      });
    }
  }
  if (frameworks.react) collectReact(files, builder);
  if (frameworks.nuxt) {
    collectVue(files, builder, {
      framework: "nuxt",
      scriptSetupTopLevel: true,
      basePaths,
    });
    collectNuxt(files, builder);
  } else if (frameworks.vue) collectVue(files, builder);
  process.stdout.write(`${JSON.stringify(builder.fragment(), null, 2)}\n`);
}
main();
