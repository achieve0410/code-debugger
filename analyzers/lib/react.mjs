import path from "node:path";
import ts from "typescript";

import { FRONTEND_EXTS, TYPESCRIPT_VERSION, readSourceText, relPath } from "./shared.mjs";
import {
  collectFunctionInfos,
  componentKind,
  createTsSourceFile,
  dynamicBranchSignals,
  expressionName,
  isComponentName,
  jsxAttribute,
  jsxAttributeComponent,
  jsxAttributeHandlerName,
  jsxAttributeString,
  jsxTagName,
  literalString,
  objectProperties,
  sourceForEntry,
  traverseFunctionBody,
  traverseTs,
} from "./ts-util.mjs";
import { collectHttpUrlSymbols, collectPayloadHints, handleJsCall } from "./http.mjs";

export function collectReact(files, builder) {
  const urlSymbols = collectHttpUrlSymbols(files, builder);
  const parsed = files
    .filter((file) => FRONTEND_EXTS.has(path.extname(file)))
    .map((file) => {
      const text = readSourceText(file, builder);
      if (text === undefined) return undefined;
      return { file, text, lineOffset: 0, sourceFile: createTsSourceFile(file, text) };
    })
    .filter(Boolean);
  const componentCandidates = new Map();
  const functionsByFile = new Map();

  for (const entry of parsed) {
    const functions = collectFunctionInfos(entry);
    functionsByFile.set(entry.file, functions);
    for (const fn of functions) {
      const source = sourceForEntry(entry, builder, fn.node, fn.name);
      const key = `${isComponentName(fn.name) ? "component" : "function"}:${source.path}:${fn.name}`;
      if (isComponentName(fn.name)) {
        const candidates = componentCandidates.get(fn.name) ?? [];
        candidates.push(key);
        componentCandidates.set(fn.name, candidates);
      }
      builder.addNode({
        key,
        kind: isComponentName(fn.name) ? componentKind(fn.name) : "function",
        label: fn.name,
        layer: "frontend",
        source,
        confidence: isComponentName(fn.name) ? 0.88 : 0.75,
        metadata: { framework: "react", parser: "typescript.createSourceFile", typescriptVersion: TYPESCRIPT_VERSION },
      });
    }
  }
  const components = new Map(
    [...componentCandidates.entries()]
      .filter(([, candidates]) => candidates.length === 1)
      .map(([name, candidates]) => [name, candidates[0]]),
  );
  const payloadHints = collectPayloadHints(parsed, functionsByFile);

  for (const entry of parsed) {
    collectReactRoutes(entry, builder, components);

    for (const fn of functionsByFile.get(entry.file) ?? []) {
      const ownerKey = `${isComponentName(fn.name) ? "component" : "function"}:${relPath(builder.root, entry.file)}:${fn.name}`;
      traverseFunctionBody(fn, (node) => {
        if (ts.isJsxOpeningLikeElement(node)) {
          const tag = jsxTagName(node.tagName);
          if (tag && isComponentName(tag) && componentKind(tag) !== "page" && components.has(tag) && components.get(tag) !== ownerKey) {
            builder.addEdge({ source: ownerKey, target: components.get(tag), kind: "renders", confidence: 0.85, metadata: { framework: "react" } });
          }
          if (tag && !isComponentName(tag)) {
            for (const attr of node.attributes.properties) {
              if (!ts.isJsxAttribute(attr) || !/^on[A-Z]/u.test(String(attr.name.text))) continue;
              const event = String(attr.name.text);
              const eventOrdinal = syntaxOrdinal(
                fn,
                attr,
                (candidate) => ts.isJsxAttribute(candidate) && /^on[A-Z]/u.test(String(candidate.name.text)),
              );
              const eventKey = `event:${ownerKey}:${event}:${eventOrdinal}`;
              builder.addNode({
                key: eventKey,
                kind: "ui_event",
                label: `${tag}.${event}`,
                source: sourceForEntry(entry, builder, attr, event),
                confidence: 0.82,
                metadata: { event, element: tag, framework: "react" },
              });
              builder.addEdge({ source: ownerKey, target: eventKey, kind: "contains", confidence: 0.82, metadata: {} });
              const handler = jsxAttributeHandlerName(attr);
              if (handler) {
                const handlerKey = `function:${relPath(builder.root, entry.file)}:${handler}`;
                builder.addEdge({ source: eventKey, target: handlerKey, kind: "handles", confidence: 0.8, metadata: {} });
              }
            }
          }
        }
        if (ts.isCallExpression(node)) {
          handleJsCall(node, entry, builder, "react", ownerKey, urlSymbols, payloadHints, fn);
        }
      });
      const signals = dynamicBranchSignals(fn);
      if (signals.length) {
        const source = { ...sourceForEntry(entry, builder, fn.node, "conditional"), endLine: undefined };
        const unresolved = builder.unresolved(["react", ownerKey, "branch", "0"], "conditional dynamic branch", source, signals[0], { framework: "react" });
        builder.addEdge({ source: ownerKey, target: unresolved, kind: "branches_to", confidence: 0.35, metadata: {} });
      }
    }
  }
}

const DATA_ROUTER_CALLEES = new Set(["createBrowserRouter", "createHashRouter", "createMemoryRouter", "useRoutes"]);

function collectReactRoutes(entry, builder, components) {
  traverseTs(entry.sourceFile, (node) => {
    if (isRouteJsxNode(node) && !hasRouteJsxAncestor(node)) {
      walkJsxRouteTree(node, "", entry, builder, components);
    }
    if (ts.isCallExpression(node) && DATA_ROUTER_CALLEES.has(expressionName(node.expression) ?? "")) {
      const routesArg = node.arguments[0];
      if (routesArg && ts.isArrayLiteralExpression(routesArg)) {
        for (const element of routesArg.elements) {
          if (ts.isObjectLiteralExpression(element)) {
            walkRouteObject(element, "", entry, builder, components);
          }
        }
      }
    }
  });
}

function isRouteJsxNode(node) {
  if (ts.isJsxSelfClosingElement(node)) return jsxTagName(node.tagName) === "Route";
  if (ts.isJsxElement(node)) return jsxTagName(node.openingElement.tagName) === "Route";
  return false;
}

function hasRouteJsxAncestor(node) {
  let current = node.parent;
  while (current) {
    if (isRouteJsxNode(current)) return true;
    current = current.parent;
  }
  return false;
}

function walkJsxRouteTree(node, parentPath, entry, builder, components) {
  const attributes = ts.isJsxSelfClosingElement(node) ? node.attributes : node.openingElement.attributes;
  const declaredPath = jsxAttributeString(attributes, "path");
  const isIndex = jsxAttribute(attributes, "index") !== undefined;
  const fullPath = reactRouteFullPath(declaredPath, isIndex, parentPath);
  const component = jsxAttributeComponent(attributes, "element");
  registerReactRoute(fullPath, declaredPath, parentPath, component, node, entry, builder, components, 0.95);

  const nextParent = fullPath ?? parentPath;
  if (ts.isJsxElement(node)) {
    for (const child of node.children) {
      if (isRouteJsxNode(child)) walkJsxRouteTree(child, nextParent, entry, builder, components);
    }
  }
}

function walkRouteObject(objectNode, parentPath, entry, builder, components) {
  const props = objectProperties(objectNode);
  const declaredPath = literalString(props.get("path"));
  const isIndex = props.get("index")?.kind === ts.SyntaxKind.TrueKeyword;
  const fullPath = reactRouteFullPath(declaredPath, isIndex, parentPath);
  const component = routeObjectComponentName(props);
  registerReactRoute(fullPath, declaredPath, parentPath, component, objectNode, entry, builder, components, 0.93);

  const nextParent = fullPath ?? parentPath;
  const children = props.get("children");
  if (children && ts.isArrayLiteralExpression(children)) {
    for (const element of children.elements) {
      if (ts.isObjectLiteralExpression(element)) {
        walkRouteObject(element, nextParent, entry, builder, components);
      }
    }
  }
}

function routeObjectComponentName(props) {
  const element = props.get("element");
  if (element) {
    if (ts.isJsxElement(element)) return jsxTagName(element.openingElement.tagName);
    if (ts.isJsxSelfClosingElement(element)) return jsxTagName(element.tagName);
  }
  const component = props.get("Component");
  return component ? expressionName(component) : undefined;
}

function reactRouteFullPath(declaredPath, isIndex, parentPath) {
  if (declaredPath !== undefined) return joinReactRoutePaths(parentPath, declaredPath);
  if (isIndex) return parentPath || "/";
  return undefined;
}

function joinReactRoutePaths(parentPath, declaredPath) {
  if (declaredPath.startsWith("/")) return normalizeReactRoutePath(declaredPath);
  if (!parentPath) return normalizeReactRoutePath(declaredPath || "/");
  if (!declaredPath) return parentPath;
  return normalizeReactRoutePath(`${parentPath}/${declaredPath}`);
}

function normalizeReactRoutePath(routePath) {
  const normalized = `/${routePath}`.replace(/\/+/gu, "/");
  return normalized.length > 1 ? normalized.replace(/\/$/u, "") : normalized;
}

function registerReactRoute(fullPath, declaredPath, parentPath, component, node, entry, builder, components, confidence) {
  if (fullPath === undefined) return;
  const source = sourceForEntry(entry, builder, node, "route");
  if (!isStrictRoutePath(fullPath)) {
    builder.unresolved(
      ["react", source.path, "route", syntaxOrdinalInFile(entry, node, isRouteDeclaration)],
      "unsupported React route",
      source,
      "React route path is not a supported static route pattern",
      { framework: "react" },
    );
    return;
  }
  const key = `route:react:${fullPath}`;
  if (!builder.routes.has(key)) {
    builder.addRoute({
      path: fullPath,
      key,
      framework: "react",
      source,
      confidence,
    });
  }
  if (component && components.has(component)) {
    builder.addEdge({ source: key, target: components.get(component), kind: "renders", confidence, metadata: {} });
  } else if (component) {
    const unresolved = builder.unresolved(
      ["react", key, "component"],
      "Unresolved",
      source,
      "React route component target is not uniquely resolved",
      { framework: "react" },
    );
    builder.addEdge({ source: key, target: unresolved, kind: "renders", confidence: 0.35, metadata: {} });
  }
}

function isStrictRoutePath(routePath) {
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

function isRouteDeclaration(node) {
  return isRouteJsxNode(node) || ts.isObjectLiteralExpression(node);
}

function syntaxOrdinal(fn, node, matches) {
  let ordinal = 0;
  let result = 0;
  traverseFunctionBody(fn, (candidate) => {
    if (!matches(candidate)) return;
    if (candidate === node) result = ordinal;
    ordinal += 1;
  });
  return result;
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
