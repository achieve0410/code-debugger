import path from "node:path";

import { PY_EXTS, escapeRegex, lineOf, readSourceText, relPath } from "./shared.mjs";
import { linkHttpCallsToDjango } from "./http.mjs";

export function collectDjango(files, builder) {
  const pyFiles = files.filter((file) => PY_EXTS.has(path.extname(file)));
  const textByFile = new Map();
  for (const file of pyFiles) {
    const text = readSourceText(file, builder);
    if (text !== undefined) textByFile.set(file, text);
  }
  const viewFunctions = new Map();
  const helperFunctions = new Map();
  const modelClasses = new Map();

  for (const [file, text] of textByFile) {
    const sourcePath = relPath(builder.root, file);
    for (const match of text.matchAll(/^class\s+([A-Z]\w*)\([^)]*models\.Model[^)]*\):/gmu)) {
      const name = match[1];
      const key = `model:${sourcePath}:${name}`;
      modelClasses.set(name, key);
      builder.addNode({
        key,
        kind: "model",
        label: name,
        layer: "data",
        source: { path: sourcePath, line: lineOf(text, match.index), symbol: name },
        confidence: 0.9,
        metadata: { framework: "django" },
      });
    }
    for (const match of text.matchAll(/^def\s+(\w+)\(/gmu)) {
      const name = match[1];
      const key = sourcePath.endsWith("views.py") ? `django_view:${sourcePath}:${name}` : `function:${sourcePath}:${name}`;
      const node = {
        key,
        kind: sourcePath.endsWith("views.py") ? "django_view" : "function",
        label: name,
        layer: "backend",
        source: { path: sourcePath, line: lineOf(text, match.index), symbol: name },
        confidence: 0.82,
        metadata: { framework: "django" },
      };
      builder.addNode(node);
      if (sourcePath.endsWith("views.py")) viewFunctions.set(name, key);
      else helperFunctions.set(name, key);
    }
  }

  for (const [file, text] of textByFile) {
    const sourcePath = relPath(builder.root, file);
    collectDjangoUrls(file, text, viewFunctions, builder);
    collectDjangoCalls(file, text, sourcePath, viewFunctions, helperFunctions, modelClasses, builder);
  }

  linkHttpCallsToDjango(builder);
}

function collectDjangoUrls(file, text, viewFunctions, builder) {
  const sourcePath = relPath(builder.root, file);
  const regex = /path\(\s*["']([^"']+)["']\s*,\s*(?:views\.)?(\w+)/gu;
  for (const match of text.matchAll(regex)) {
    const [, routePath, viewName] = match;
    if (viewName === "include") continue;
    const fullPath = `/${routePath}`;
    const key = `django_url:${fullPath}`;
    builder.addNode({
      key,
      kind: "django_url_pattern",
      label: fullPath,
      layer: "backend",
      source: { path: sourcePath, line: lineOf(text, match.index), symbol: fullPath },
      confidence: 0.9,
      metadata: { method: "GET", path: fullPath, framework: "django" },
    });
    const viewKey = viewFunctions.get(viewName) ?? `django_view:${viewName}`;
    builder.addEdge({ source: key, target: viewKey, kind: "resolves_to", confidence: 0.88, metadata: { view: viewName } });
  }
}

function collectDjangoCalls(file, text, sourcePath, viewFunctions, helperFunctions, modelClasses, builder) {
  const functionBlocks = blocksForFunctions(text);
  for (const block of functionBlocks) {
    const owner = sourcePath.endsWith("views.py") ? viewFunctions.get(block.name) : helperFunctions.get(block.name);
    if (!owner) continue;
    for (const [name, target] of helperFunctions) {
      if (new RegExp(`\\b${escapeRegex(name)}\\s*\\(`, "u").test(block.body) && target !== owner) {
        builder.addEdge({ source: owner, target, kind: "calls", confidence: 0.72, metadata: { framework: "django" } });
      }
    }
    for (const [modelName, modelKey] of modelClasses) {
      const queryRegex = new RegExp(`\\b${escapeRegex(modelName)}\\.objects\\.(filter|get|all|create)\\s*\\(`, "u");
      const queryMatch = block.body.match(queryRegex);
      if (queryMatch) {
        const queryKey = `query:${sourcePath}:${block.name}:${modelName}.${queryMatch[1]}`;
        builder.addNode({
          key: queryKey,
          kind: "query_boundary",
          label: `${modelName}.objects.${queryMatch[1]}`,
          layer: "data",
          source: { path: sourcePath, line: block.startLine, symbol: `${modelName}.objects.${queryMatch[1]}` },
          confidence: 0.78,
          metadata: { model: modelName, operation: queryMatch[1], framework: "django" },
        });
        builder.addEdge({ source: owner, target: queryKey, kind: "accesses", confidence: 0.74, metadata: {} });
        builder.addEdge({ source: queryKey, target: modelKey, kind: "accesses", confidence: 0.82, metadata: {} });
      }
    }
    const externalRegex = /requests\.(get|post|put|delete)\(\s*["']([^"']+)["']/u;
    const externalMatch = block.body.match(externalRegex);
    if (externalMatch) {
      const method = externalMatch[1].toUpperCase();
      const url = externalMatch[2];
      const externalKey = `external:${method}:${url}`;
      builder.addNode({
        key: externalKey,
        kind: "external_service",
        label: `${method} ${url}`,
        layer: "external",
        source: { path: sourcePath, line: block.startLine, symbol: "requests" },
        confidence: 0.72,
        metadata: { method, url, boundaryOnly: true },
      });
      builder.addEdge({ source: owner, target: externalKey, kind: "calls", confidence: 0.7, metadata: { boundaryOnly: true } });
    }
    const boundaryRegex = /return\s+\{[^}]*["']method["']:\s*["'](GET|POST|PUT|DELETE)["'][^}]*["']url["']:\s*([A-Z_][A-Z0-9_]*)/u;
    const boundaryMatch = block.body.match(boundaryRegex);
    if (boundaryMatch) {
      const method = boundaryMatch[1];
      const constant = boundaryMatch[2];
      const valueMatch = text.match(new RegExp(`^${escapeRegex(constant)}\\s*=\\s*["']([^"']+)["']`, "mu"));
      const url = valueMatch?.[1];
      if (url) {
        const externalKey = `external:${method}:${url}`;
        builder.addNode({
          key: externalKey,
          kind: "external_service",
          label: `${method} ${url}`,
          layer: "external",
          source: { path: sourcePath, line: block.startLine, symbol: block.name },
          confidence: 0.72,
          metadata: { method, url, boundaryOnly: true },
        });
        builder.addEdge({ source: owner, target: externalKey, kind: "calls", confidence: 0.7, metadata: { boundaryOnly: true } });
      }
    }
    if (/getattr\(|import_string\(|globals\(\)|settings\./u.test(block.body)) {
      const source = { path: sourcePath, line: block.startLine, symbol: block.name };
      const unresolved = builder.unresolved(["django", sourcePath, block.name], "dynamic Django target", source, "Django function contains a dynamic target that cannot be resolved statically", { framework: "django" });
      builder.addEdge({ source: owner, target: unresolved, kind: "branches_to", confidence: 0.35, metadata: {} });
    }
  }
}

function blocksForFunctions(text) {
  const matches = [...text.matchAll(/^def\s+(\w+)\([^)]*\):/gmu)];
  return matches.map((match, index) => {
    const next = matches[index + 1]?.index ?? text.length;
    return {
      name: match[1],
      startLine: lineOf(text, match.index),
      body: text.slice(match.index, next),
    };
  });
}
