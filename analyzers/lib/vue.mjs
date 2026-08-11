import path from "node:path";
import ts from "typescript";
import { parse as parseSfc } from "@vue/compiler-sfc";

import { FRONTEND_EXTS, TYPESCRIPT_VERSION, lineOf, readSourceText, relPath } from "./shared.mjs";
import {
  collectFunctionInfos,
  componentKind,
  createTsSourceFile,
  dynamicBranchSignals,
  expressionName,
  literalString,
  objectLiteralStringProperty,
  returnedExpression,
  sourceForEntry,
  traverseFunctionBody,
  traverseTs,
} from "./ts-util.mjs";
import { collectHttpUrlSymbols, collectPayloadHints, handleJsCall } from "./http.mjs";

export function collectVue(files, builder, options = {}) {
  const framework = options.framework ?? "vue";
  const scriptSetupTopLevel = options.scriptSetupTopLevel ?? false;
  const basePaths = options.basePaths ?? [];
  const urlSymbols = collectHttpUrlSymbols(files, builder);
  const vueFiles = files.filter((file) => path.extname(file) === ".vue");
  const scriptEntries = [];
  const templateEntries = [];
  const componentCandidates = new Map();

  for (const file of vueFiles) {
    const text = readSourceText(file, builder);
    if (text === undefined) continue;
    let parsed;
    try {
      parsed = parseSfc(text, { filename: file });
    } catch (error) {
      builder.addDiagnostic({
        code: "file_skipped",
        message: `Vue single-file component could not be parsed: ${error?.name ?? "error"}`,
        source: { path: relPath(builder.root, file) },
      });
      continue;
    }
    const name = path.basename(file, ".vue");
    const source = { path: relPath(builder.root, file), line: 1, endLine: text.split(/\r?\n/u).length, symbol: name };
    const key = `component:${source.path}:${name}`;
    const candidates = componentCandidates.get(name) ?? [];
    candidates.push(key);
    componentCandidates.set(name, candidates);
    builder.addNode({
      key,
      kind: framework === "nuxt" ? "component" : componentKind(name),
      label: name,
      source,
      confidence: 0.88,
      metadata: { framework },
    });

    const script = parsed.descriptor.scriptSetup ?? parsed.descriptor.script;
    if (script) {
      const scriptText = script.content;
      scriptEntries.push({
        file,
        text: scriptText,
        lineOffset: script.loc.start.line - 1,
        sourceFile: createTsSourceFile(`${file}.ts`, scriptText),
      });
    }

    const template = parsed.descriptor.template?.content ?? "";
    templateEntries.push({ file, text, template, componentKey: key });
  }

  for (const file of files.filter((item) => FRONTEND_EXTS.has(path.extname(item)) && isFrontendSourceFile(builder.root, item))) {
    const text = readSourceText(file, builder);
    if (text === undefined) continue;
    scriptEntries.push({
      file,
      text,
      lineOffset: 0,
      sourceFile: createTsSourceFile(file, text),
    });
  }

  const components = new Map(
    [...componentCandidates.entries()]
      .filter(([, candidates]) => candidates.length === 1)
      .map(([name, candidates]) => [name, candidates[0]]),
  );
  const functionsByFile = new Map();
  const functionCandidates = new Map();

  for (const entry of scriptEntries) {
    const functions = collectFunctionInfos(entry);
    functionsByFile.set(entry.file, functions);
    for (const fn of functions) {
      const functionKey = `function:${relPath(builder.root, entry.file)}:${fn.name}`;
      const candidates = functionCandidates.get(fn.name) ?? [];
      candidates.push(functionKey);
      functionCandidates.set(fn.name, candidates);
      if (!builder.nodes.has(functionKey)) {
        builder.addNode({
          key: functionKey,
          kind: "function",
          label: fn.name,
          layer: "frontend",
          source: sourceForEntry(entry, builder, fn.node, fn.name),
          confidence: 0.76,
          metadata: { framework, parser: "typescript.createSourceFile", typescriptVersion: TYPESCRIPT_VERSION },
        });
      }
    }
  }
  const payloadHints = collectPayloadHints(scriptEntries, functionsByFile);

  for (const entry of templateEntries) {
    collectVueTemplate(
      entry.file,
      entry.text,
      entry.template,
      entry.componentKey,
      components,
      functionCandidates,
      builder,
      framework,
    );
  }

  const routerFiles = files.filter(
    (file) => FRONTEND_EXTS.has(path.extname(file)) && /router|routes/u.test(relPath(builder.root, file)),
  );
  for (const file of routerFiles) {
    const text = readSourceText(file, builder);
    if (text === undefined) continue;
    collectVueRouter(file, text, components, builder, framework);
  }

  for (const entry of scriptEntries) {
    for (const fn of functionsByFile.get(entry.file) ?? []) {
      const functionKey = `function:${relPath(builder.root, entry.file)}:${fn.name}`;
      traverseFunctionBody(fn, (node) => {
        if (ts.isCallExpression(node)) {
          handleJsCall(
            node,
            entry,
            builder,
            framework,
            functionKey,
            urlSymbols,
            payloadHints,
            fn,
            { basePaths },
          );
          linkKnownJsFunctionCall(node, entry, builder, functionKey, functionCandidates, framework);
        }
      });
      const signals = dynamicBranchSignals(fn);
      if (signals.length) {
        const branchSource = { ...sourceForEntry(entry, builder, fn.node, "conditional"), endLine: undefined };
        const unresolved = builder.unresolved([framework, branchSource.path, "branch", String(fn.node.getStart(entry.sourceFile))], "conditional dynamic branch", branchSource, signals[0], { framework });
        builder.addEdge({ source: functionKey, target: unresolved, kind: "branches_to", confidence: 0.35, metadata: {} });
      }
    }
  }

  if (scriptSetupTopLevel) {
    // Calls made at <script setup> top level (e.g. useFetch during setup)
    // belong to the component itself, not to a named function.
    for (const entry of scriptEntries.filter((item) => path.extname(item.file) === ".vue")) {
      const componentKey = `component:${relPath(builder.root, entry.file)}:${path.basename(entry.file, ".vue")}`;
      if (!builder.nodes.has(componentKey)) continue;
      traverseFunctionBody({ bodyNode: entry.sourceFile }, (node) => {
        if (ts.isCallExpression(node)) {
          handleJsCall(
            node,
            entry,
            builder,
            framework,
            componentKey,
            urlSymbols,
            payloadHints,
            undefined,
            { basePaths },
          );
          linkKnownJsFunctionCall(node, entry, builder, componentKey, functionCandidates, framework);
        }
      });
    }
  }
}

function isFrontendSourceFile(root, file) {
  return relPath(root, file).split("/").includes("src");
}

function collectVueRouter(file, text, components, builder, framework = "vue") {
  const entry = { file, text, lineOffset: 0, sourceFile: createTsSourceFile(file, text) };
  traverseTs(entry.sourceFile, (node) => {
    if (!ts.isObjectLiteralExpression(node)) return;
    let routePath;
    let component;
    for (const prop of node.properties) {
      if (!ts.isPropertyAssignment(prop) || !ts.isIdentifier(prop.name)) continue;
      if (prop.name.text === "path") routePath = literalString(prop.initializer);
      if (prop.name.text === "component") component = routeComponentName(prop.initializer);
    }
    if (routePath !== undefined) {
      const fullPath = fullVueRoutePath(node, routePath);
      const source = sourceForEntry(entry, builder, node, "route");
      if (!isStrictVueRoutePath(fullPath)) {
        builder.unresolved(
          [framework, source.path, "route", syntaxOrdinalInFile(entry, node, ts.isObjectLiteralExpression)],
          "unsupported Vue route",
          source,
          "Vue route path is not a supported static route pattern",
          { framework },
        );
        return;
      }
      const key = `route:${framework}:${fullPath}`;
      if (!builder.routes.has(key)) {
        builder.addRoute({
          path: fullPath,
          key,
          framework,
          source,
          confidence: 0.93,
        });
      }
      if (component && components.has(component)) {
        builder.addEdge({ source: key, target: components.get(component), kind: "renders", confidence: 0.9, metadata: { framework } });
      } else if (component) {
        const unresolved = builder.unresolved(
          [framework, key, "component"],
          "Unresolved",
          source,
          "Vue route component target is not uniquely resolved",
          { framework },
        );
        builder.addEdge({ source: key, target: unresolved, kind: "renders", confidence: 0.35, metadata: {} });
      }
    }
  });
}

function fullVueRoutePath(node, declaredPath) {
  const parentPath = parentVueRoutePath(node);
  if (declaredPath.startsWith("/")) return normalizeVueRoutePath(declaredPath);
  if (!parentPath) return normalizeVueRoutePath(declaredPath || "/");
  if (!declaredPath) return parentPath;
  return normalizeVueRoutePath(`${parentPath}/${declaredPath}`);
}

function parentVueRoutePath(node) {
  const parent = parentVueRouteObject(node);
  if (!parent) return "";
  const declaredPath = objectLiteralStringProperty(parent, "path");
  return declaredPath === undefined ? "" : fullVueRoutePath(parent, declaredPath);
}

function parentVueRouteObject(node) {
  const array = node.parent;
  if (!array || !ts.isArrayLiteralExpression(array)) return undefined;
  const children = array.parent;
  if (
    !children
    || !ts.isPropertyAssignment(children)
    || !ts.isIdentifier(children.name)
    || children.name.text !== "children"
  ) {
    return undefined;
  }
  return ts.isObjectLiteralExpression(children.parent) ? children.parent : undefined;
}

function normalizeVueRoutePath(routePath) {
  const normalized = `/${routePath}`.replace(/\/+/gu, "/");
  return normalized.length > 1 ? normalized.replace(/\/$/u, "") : normalized;
}

function isStrictVueRoutePath(routePath) {
  return routePath.startsWith("/")
    && !/[?#\\\s]/u.test(routePath)
    && routePath.split("/").every((segment) => (
      !segment || (
        segment !== "."
        && segment !== ".."
        && segment !== "*"
        && (!segment.startsWith(":") || /^:[A-Za-z_][A-Za-z0-9_]*$/u.test(segment))
      )
    ));
}

function syntaxOrdinalInFile(entry, node, matches) {
  let ordinal = 0;
  let result = 0;
  traverseTs(entry.sourceFile, (candidate) => {
    if (!matches(candidate)) return;
    if (candidate === node) result = ordinal;
    ordinal += 1;
  });
  return result;
}

function linkKnownJsFunctionCall(node, entry, builder, ownerKey, functionCandidates, framework = "vue") {
  const callee = expressionName(node.expression);
  const name = callee?.split(".").at(-1);
  if (!name) return;

  const localKey = `function:${relPath(builder.root, entry.file)}:${name}`;
  const candidates = functionCandidates.get(name) ?? [];
  const target = builder.nodes.has(localKey)
    ? localKey
    : candidates.length === 1
      ? candidates[0]
      : undefined;
  if (!target || target === ownerKey) return;
  builder.addEdge({
    source: ownerKey,
    target,
    kind: "calls",
    confidence: target === localKey ? 0.84 : 0.72,
    metadata: { framework, resolution: target === localKey ? "same_file" : "unique_symbol" },
  });
}

function routeComponentName(node) {
  const direct = expressionName(node);
  if (direct) return direct;
  if (ts.isArrowFunction(node)) return importedComponentName(returnedExpression(node));
  return importedComponentName(node);
}

function importedComponentName(node) {
  if (!node || !ts.isCallExpression(node) || node.expression.kind !== ts.SyntaxKind.ImportKeyword) return undefined;
  const target = literalString(node.arguments[0]);
  return target ? path.basename(target).replace(/\.[^.]+$/u, "") : undefined;
}

function collectVueTemplate(file, fullText, template, componentKey, components, functionCandidates, builder, framework = "vue") {
  const sourcePath = relPath(builder.root, file);
  const tagRegex = /<([A-Z][A-Za-z0-9]*)(?:\s|>|\/)/gu;
  for (const match of template.matchAll(tagRegex)) {
    const name = match[1];
    const target = components.get(name);
    if (target && target !== componentKey && builder.nodes.get(target)?.kind !== "page") {
      builder.addEdge({ source: componentKey, target, kind: "renders", confidence: 0.8, metadata: { framework } });
    }
  }

  const templateOffset = Math.max(0, fullText.indexOf(template));
  const elementRegex = /<([a-z][\w-]*|[A-Z][\w]*)([^>]*)>/gu;
  for (const elementMatch of template.matchAll(elementRegex)) {
    const [, element, attributes] = elementMatch;
    const eventRegex = /@([\w:-]+)((?:\.[\w-]+)*)="([^"]+)"/gu;
    for (const eventMatch of attributes.matchAll(eventRegex)) {
      const [, event, modifiers, handlerExpression] = eventMatch;
      const handler = handlerExpression.replace(/\(.*$/u, "");
      const attributeOffset = elementMatch[0].indexOf(eventMatch[0]);
      const attributePosition = (elementMatch.index ?? 0) + Math.max(0, attributeOffset);
      const absoluteOffset = templateOffset + attributePosition;
      const source = { path: sourcePath, line: lineOf(fullText, absoluteOffset), symbol: `@${event}` };
      const eventOrdinal = templateSyntaxOrdinal(template, attributePosition, eventRegex);
      const eventKey = `event:${componentKey}:${event}:${eventOrdinal}`;
      builder.addNode({
        key: eventKey,
        kind: "ui_event",
        label: `${element}.@${event}`,
        source,
        confidence: 0.82,
        metadata: { event, element, modifiers: modifiers.split(".").filter(Boolean), framework },
      });
      builder.addEdge({ source: componentKey, target: eventKey, kind: "contains", confidence: 0.82, metadata: {} });
      const localHandlerKey = `function:${sourcePath}:${handler}`;
      const candidates = functionCandidates.get(handler) ?? [];
      let handlerKey = localHandlerKey;
      if (!builder.nodes.has(localHandlerKey)) {
        if (candidates.length === 1) {
          [handlerKey] = candidates;
        } else {
          const reason = candidates.length > 1
            ? "Vue template handler matches multiple source functions"
            : "Vue template handler target was not found in analyzed source";
          handlerKey = builder.unresolved(
            [framework, componentKey, "handler", handler, String(eventOrdinal)],
            "Unresolved",
            source,
            reason,
            { framework },
          );
        }
      }
      builder.addEdge({ source: eventKey, target: handlerKey, kind: "handles", confidence: 0.8, metadata: {} });
    }
  }

  const dynamicRegex = /<component\s+[^>]*:is=/gu;
  for (const match of template.matchAll(dynamicRegex)) {
    const position = match.index ?? 0;
    const absoluteOffset = templateOffset + position;
    const source = { path: sourcePath, line: lineOf(fullText, absoluteOffset), symbol: "dynamic-component" };
    const dynamicOrdinal = templateSyntaxOrdinal(template, position, dynamicRegex);
    const unresolved = builder.unresolved([framework, componentKey, "dynamic-component", String(dynamicOrdinal)], "dynamic component", source, "Vue dynamic component target cannot be resolved statically", { framework });
    builder.addEdge({ source: componentKey, target: unresolved, kind: "branches_to", confidence: 0.35, metadata: {} });
  }
}
function templateSyntaxOrdinal(template, position, syntaxClass) {
  let ordinal = 0;
  for (const match of template.matchAll(syntaxClass)) {
    if ((match.index ?? 0) >= position) return ordinal;
    ordinal += 1;
  }
  return ordinal;
}
