import path from "node:path";
import ts from "typescript";

import { lineOf, readSourceText, relPath, toPosix } from "./shared.mjs";
import {
  createTsSourceFile,
  expressionName,
  literalString,
  objectProperties,
  traverseTs,
} from "./ts-util.mjs";

const NUXT_CONFIG_RE = /^nuxt\.config\.(?:js|mjs|ts)$/u;

export function findNuxtConfigFiles(files) {
  return files.filter((file) => NUXT_CONFIG_RE.test(path.basename(file)));
}

export function collectNuxt(files, builder) {
  for (const configFile of findNuxtConfigFiles(files)) {
    const appRoot = path.dirname(configFile);
    const config = readNuxtConfig(configFile, builder);
    if (config.pagesEnabled === false) {
      builder.addDiagnostic({
        code: "nuxt_pages_disabled",
        message: "nuxt.config disables the pages module; file-based routes were not generated",
        source: { path: relPath(builder.root, configFile) },
      });
      continue;
    }
    const pagesDir = resolvePagesDir(files, appRoot, config.srcDir);
    if (!pagesDir) {
      builder.addDiagnostic({
        code: "nuxt_pages_missing",
        message: "no pages directory was found for this Nuxt app; file-based routes were not generated",
        source: { path: relPath(builder.root, configFile) },
      });
      continue;
    }

    const pageFiles = files.filter(
      (file) => file.startsWith(pagesDir + path.sep) && path.extname(file) === ".vue",
    );
    for (const file of pageFiles) {
      const routePath = joinNuxtBase(config.baseURL, nuxtRoutePath(path.relative(pagesDir, file)));
      const sourcePath = relPath(builder.root, file);
      const source = { path: sourcePath, line: 1, symbol: "route" };
      if (!isStrictNuxtRoutePath(routePath)) {
        builder.unresolved(
          ["nuxt", sourcePath, "page-route"],
          "unsupported Nuxt route",
          source,
          "Nuxt file route is not a supported static route pattern",
          { framework: "nuxt" },
        );
        continue;
      }
      const key = `route:nuxt:${routePath}`;
      if (!builder.routes.has(key)) {
        builder.addRoute({
          path: routePath,
          key,
          framework: "nuxt",
          source,
          confidence: 0.95,
          metadata: { generator: "nuxt-pages" },
        });
      }
      const componentKey = `component:${sourcePath}:${path.basename(file, ".vue")}`;
      if (builder.nodes.has(componentKey)) {
        builder.addEdge({ source: key, target: componentKey, kind: "renders", confidence: 0.95, metadata: { framework: "nuxt" } });
      }
    }

    collectNuxtLinkNavigation(files, appRoot, builder);
  }
}

function readNuxtConfig(configFile, builder) {
  const result = { srcDir: undefined, baseURL: "", pagesEnabled: undefined };
  const text = readSourceText(configFile, builder);
  if (text === undefined) return result;
  const sourceFile = createTsSourceFile(configFile, text);
  traverseTs(sourceFile, (node) => {
    if (!ts.isCallExpression(node) || expressionName(node.expression) !== "defineNuxtConfig") return;
    const arg = node.arguments[0];
    if (!arg || !ts.isObjectLiteralExpression(arg)) return;
    const props = objectProperties(arg);
    const srcDir = props.get("srcDir");
    if (srcDir) {
      const literal = literalString(srcDir);
      if (literal === undefined) {
        builder.addDiagnostic({
          code: "nuxt_config_dynamic",
          message: "nuxt.config srcDir is not a static literal; the default pages location is assumed",
          source: { path: relPath(builder.root, configFile) },
        });
      } else {
        result.srcDir = literal;
      }
    }
    const pages = props.get("pages");
    if (pages) result.pagesEnabled = pages.kind !== ts.SyntaxKind.FalseKeyword;
    const app = props.get("app");
    if (app && ts.isObjectLiteralExpression(app)) {
      const base = objectProperties(app).get("baseURL");
      if (base) result.baseURL = literalString(base) ?? "";
    }
  });
  return result;
}

function resolvePagesDir(files, appRoot, srcDir) {
  const candidates = srcDir
    ? [path.join(appRoot, srcDir, "pages")]
    : [path.join(appRoot, "pages"), path.join(appRoot, "app", "pages"), path.join(appRoot, "src", "pages")];
  return candidates.find((dir) => files.some((file) => file.startsWith(dir + path.sep)));
}

export function nuxtRoutePath(relFromPages) {
  const segments = toPosix(relFromPages)
    .replace(/\.vue$/u, "")
    .split("/")
    .map((segment) => {
      if (segment === "index") return "";
      return segment
        .replace(/\[\.\.\.([^\]]+)\]/gu, ":$1*")
        .replace(/\[([^\]]+)\]/gu, ":$1");
    })
    .filter((segment) => segment !== "");
  return `/${segments.join("/")}`;
}

function joinNuxtBase(baseURL, routePath) {
  if (!baseURL || baseURL === "/") return routePath;
  const joined = `/${baseURL}/${routePath}`.replace(/\/+/gu, "/");
  return joined.length > 1 ? joined.replace(/\/$/u, "") : joined;
}

function collectNuxtLinkNavigation(files, appRoot, builder) {
  const linkRegex = /<(?:NuxtLink|nuxt-link)\s[^>]*to="([^"]+)"/gu;
  for (const file of files) {
    if (!file.startsWith(appRoot + path.sep) || path.extname(file) !== ".vue") continue;
    const text = readSourceText(file, builder);
    if (text === undefined) continue;
    const sourcePath = relPath(builder.root, file);
    const componentKey = `component:${sourcePath}:${path.basename(file, ".vue")}`;
    if (!builder.nodes.has(componentKey)) continue;
    let ordinal = 0;
    for (const match of text.matchAll(linkRegex)) {
      const target = match[1];
      const source = { path: sourcePath, line: lineOf(text, match.index ?? 0), symbol: "navigation" };
      if (isStrictNuxtRoutePath(target) && builder.routes.has(`route:nuxt:${target}`)) {
        builder.addEdge({
          source: componentKey,
          target: `route:nuxt:${target}`,
          kind: "navigates_to",
          confidence: 0.9,
          metadata: { framework: "nuxt" },
        });
      } else {
        const unresolved = builder.unresolved(
          ["nuxt", componentKey, "navigation", String(ordinal)],
          "Unresolved",
          source,
          "NuxtLink target is not a known static Nuxt route",
          { framework: "nuxt" },
        );
        builder.addEdge({ source: componentKey, target: unresolved, kind: "navigates_to", confidence: 0.35, metadata: {} });
      }
      ordinal += 1;
    }
  }
}


function isStrictNuxtRoutePath(routePath) {
  return routePath.startsWith("/")
    && !/[?#\\\s]/u.test(routePath)
    && routePath.split("/").every((segment) => (
      !segment || (
        segment !== "."
        && segment !== ".."
        && (!segment.startsWith(":") || /^:[A-Za-z_][A-Za-z0-9_]*\*?$/u.test(segment))
      )
    ));
}
