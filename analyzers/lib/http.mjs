import path from "node:path";
import ts from "typescript";

import { FRONTEND_EXTS, TYPESCRIPT_VERSION, readSourceText, relPath } from "./shared.mjs";
import {
  createTsSourceFile,
  declarationName,
  expressionName,
  literalString,
  isNestedFunctionBoundary,
  objectProperties,
  returnedExpression,
  sourceForEntry,
  syntaxOrdinal,
  traverseFunctionBody,
  traverseTs,
} from "./ts-util.mjs";
import { BUILTIN_CONVERTERS, URL_PROOF_LIMITS, URL_PROOF_VERSION, validateBoundedUrlProof } from "../contracts.mjs";

export function handleJsCall(
  node,
  entry,
  builder,
  framework,
  ownerKey,
  urlSymbols = new Map(),
  payloadHints = { namedShapes: new Map(), parameterShapes: new Map() },
  fn,
  options = {},
) {
  const callee = expressionName(node.expression);
  const firstArg = node.arguments[0];
  const target = resolveBoundedUrl(firstArg, entry, urlSymbols);

  if (callee === "useAxios" && firstArg && ts.isObjectLiteralExpression(firstArg)) {
    const config = objectProperties(firstArg);
    const methodNode = config.get("method");
    const method = methodNode ? literalString(methodNode)?.toUpperCase() : "GET";
    if (!method) {
      addDynamicMethodTarget(node, entry, builder, framework, ownerKey, fn);
      return;
    }
    const url = resolveBoundedUrl(
      config.get("url"),
      entry,
      urlSymbols,
      options.basePaths ?? [],
    );
    if (url) {
      const payload = requestPayloadFromConfig(config, entry, fn, payloadHints, node);
      addHttpCall(
        builder,
        entry,
        framework,
        ownerKey,
        method,
        url,
        sourceForEntry(entry, builder, node, method),
        payload,
        fn,
        node,
      );
    } else {
      addUnresolvedHttpCall(node, entry, builder, framework, ownerKey, callee, fn, requestPayloadFromConfig(config, entry, fn, payloadHints, node));
    }
    return;
  }

  if (
    callee === "fetch"
    || NUXT_FETCH_CALLEES.has(callee ?? "")
    || callee?.startsWith("axios.")
    || /^(?:api|client|http)\.(?:get|post|put|patch|delete)$/u.test(callee ?? "")
  ) {
    if (!target) {
      addUnresolvedHttpCall(node, entry, builder, framework, ownerKey, callee, fn, requestPayloadFromHttpCall(callee, node, entry, fn, payloadHints));
      return;
    }
    const method = httpMethod(callee, node);
    if (!method) {
      addDynamicMethodTarget(node, entry, builder, framework, ownerKey, fn);
      return;
    }
    const payload = requestPayloadFromHttpCall(callee, node, entry, fn, payloadHints);
    addHttpCall(
      builder,
      entry,
      framework,
      ownerKey,
      method,
      target,
      sourceForEntry(entry, builder, node, method),
      payload,
      fn,
      node,
    );
  }

  if (callee === "navigate" || callee === "router.push" || callee === "navigateTo") {
    const navigationTarget = resolveHttpUrl(firstArg, urlSymbols);
    if (navigationTarget) {
      builder.addEdge({ source: ownerKey, target: `route:${framework}:${navigationTarget}`, kind: "navigates_to", confidence: 0.78, metadata: {} });
    } else {
      const source = sourceForEntry(entry, builder, node, "navigation");
      const unresolved = builder.unresolved([framework, ownerKey, "navigation", String(httpCallOrdinal(fn, node))], "computed navigation target", source, "Navigation target is computed and cannot be resolved statically", { framework });
      builder.addEdge({ source: ownerKey, target: unresolved, kind: "navigates_to", confidence: 0.35, metadata: {} });
    }
  }
}

const NUXT_FETCH_CALLEES = new Set(["$fetch", "useFetch", "useLazyFetch"]);

export function addUnresolvedHttpCall(node, entry, builder, framework, ownerKey, callee, fn, payload = []) {
  const source = sourceForEntry(entry, builder, node, callee ?? "http");
  const method = httpMethod(callee, node);
  if (!method) {
    addDynamicMethodTarget(node, entry, builder, framework, ownerKey, fn);
    return;
  }
  addHttpCall(
    builder,
    entry,
    framework,
    ownerKey,
    method,
    { normalizedPath: "/{u0}", resolution: "unbounded" },
    source,
    payload,
    fn,
    node,
  );
}

function addDynamicMethodTarget(node, entry, builder, framework, ownerKey, fn) {
  const source = sourceForEntry(entry, builder, node, "http");
  const unresolved = builder.unresolved(
    [framework, ownerKey, "http-method", String(httpCallOrdinal(fn, node))],
    "computed HTTP method",
    source,
    "dynamic_target_unproven",
  );
  builder.addEdge({
    source: ownerKey,
    target: unresolved,
    kind: "branches_to",
    confidence: 0.3,
    metadata: {},
    evidenceKind: "unresolved",
    reason: "dynamic_target_unproven",
  });
}

export function collectPayloadHints(entries, functionsByFile) {
  const namedShapes = collectNamedObjectShapes(entries);
  const functionsByName = new Map();
  for (const entry of entries) {
    for (const fn of functionsByFile.get(entry.file) ?? []) {
      const candidates = functionsByName.get(fn.name) ?? [];
      candidates.push(fn);
      functionsByName.set(fn.name, candidates);
    }
  }

  const candidatesByFunction = new Map();
  for (const entry of entries) {
    traverseTs(entry.sourceFile, (node) => {
      if (!ts.isCallExpression(node)) return;
      const callee = expressionName(node.expression)?.split(".").at(-1);
      const candidates = callee ? functionsByName.get(callee) ?? [] : [];
      if (candidates.length !== 1) return;
      const [target] = candidates;
      target.parameters.forEach((parameter, index) => {
        if (!parameter || !node.arguments[index]) return;
        const fields = resolvePayloadFields(node.arguments[index], namedShapes);
        if (!fields?.length) return;
        const byParameter = candidatesByFunction.get(target) ?? new Map();
        const shapes = byParameter.get(parameter) ?? new Map();
        shapes.set(JSON.stringify(fields), fields);
        byParameter.set(parameter, shapes);
        candidatesByFunction.set(target, byParameter);
      });
    });
  }

  const parameterShapes = new Map();
  for (const [fn, byParameter] of candidatesByFunction) {
    const resolved = new Map();
    for (const [parameter, shapes] of byParameter) {
      if (shapes.size === 1) resolved.set(parameter, [...shapes.values()][0]);
    }
    if (resolved.size) parameterShapes.set(fn, resolved);
  }
  return { namedShapes, parameterShapes };
}

function collectNamedObjectShapes(entries) {
  const candidates = new Map();
  const add = (name, initializer) => {
    if (!name || !ts.isObjectLiteralExpression(initializer)) return;
    const fields = objectLiteralFields(initializer);
    if (!fields.length) return;
    const shapes = candidates.get(name) ?? new Map();
    shapes.set(JSON.stringify(fields), fields);
    candidates.set(name, shapes);
  };

  for (const entry of entries) {
    traverseTs(entry.sourceFile, (node) => {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
        add(node.name.text, unwrapPayloadExpression(node.initializer));
      } else if (ts.isPropertyAssignment(node)) {
        add(declarationName(node.name), unwrapPayloadExpression(node.initializer));
      }
    });
  }

  return new Map(
    [...candidates.entries()]
      .map(([name, shapes]) => [name, compatibleObjectShape([...shapes.values()])])
      .filter(([, fields]) => fields),
  );
}

function compatibleObjectShape(shapes) {
  if (shapes.length === 1) return shapes[0];
  const largest = [...shapes].sort((left, right) => right.length - left.length)[0];
  const largestNames = new Set(largest.map((field) => field.name));
  if (shapes.every((fields) => fields.every((field) => largestNames.has(field.name)))) {
    return largest;
  }
  return undefined;
}

function objectLiteralFields(node) {
  const fields = [];
  for (const property of node.properties) {
    if (ts.isPropertyAssignment(property)) {
      const name = declarationName(property.name);
      if (name) fields.push({ name, type: payloadValueType(property.initializer) });
    } else if (ts.isShorthandPropertyAssignment(property)) {
      fields.push({ name: property.name.text, type: "dynamic" });
    }
  }
  return fields.sort((left, right) => left.name.localeCompare(right.name));
}

function payloadValueType(node) {
  const value = unwrapPayloadExpression(node);
  if (ts.isStringLiteralLike(value) || ts.isTemplateExpression(value)) return "string";
  if (ts.isNumericLiteral(value)) return "number";
  if (value.kind === ts.SyntaxKind.TrueKeyword || value.kind === ts.SyntaxKind.FalseKeyword) return "boolean";
  if (value.kind === ts.SyntaxKind.NullKeyword) return "null";
  if (ts.isArrayLiteralExpression(value)) return "array";
  if (ts.isObjectLiteralExpression(value)) return "object";
  return "dynamic";
}

function unwrapPayloadExpression(node) {
  let current = node;
  while (
    ts.isParenthesizedExpression(current)
    || ts.isAsExpression(current)
    || ts.isTypeAssertionExpression(current)
    || ts.isNonNullExpression(current)
  ) {
    current = current.expression;
  }
  if (
    ts.isCallExpression(current)
    && expressionName(current.expression) === "JSON.stringify"
    && current.arguments[0]
  ) {
    return unwrapPayloadExpression(current.arguments[0]);
  }
  return current;
}

function payloadRootName(node) {
  const value = unwrapPayloadExpression(node);
  if (ts.isIdentifier(value)) return value.text;
  if (ts.isPropertyAccessExpression(value) || ts.isElementAccessExpression(value)) {
    let current = value.expression;
    while (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
      current = current.expression;
    }
    return ts.isIdentifier(current) ? current.text : undefined;
  }
  return undefined;
}

function resolvePayloadFields(node, namedShapes) {
  const value = unwrapPayloadExpression(node);
  if (ts.isObjectLiteralExpression(value)) return objectLiteralFields(value);
  const name = payloadRootName(value);
  return name ? namedShapes.get(name) : undefined;
}

function requestPayloadFromConfig(config, entry, fn, payloadHints, callNode) {
  const mappings = [
    ["data", "data"],
    ["form", "form"],
    ["formData", "form"],
    ["body", "body"],
    ["params", "params"],
    ["query", "params"],
  ];
  return mappings
    .filter(([property]) => config.has(property))
    .map(([property, kind]) => requestPayloadPart(
      kind,
      config.get(property),
      entry,
      fn,
      payloadHints,
      callNode,
    ));
}

function requestPayloadFromHttpCall(callee, node, entry, fn, payloadHints) {
  const parts = [];
  if (callee === "fetch" || NUXT_FETCH_CALLEES.has(callee ?? "")) {
    const init = node.arguments[1];
    if (init && ts.isObjectLiteralExpression(init)) {
      parts.push(...requestPayloadFromConfig(objectProperties(init), entry, fn, payloadHints, node));
    }
    return parts;
  }

  if (/\.(?:post|put|patch)$/u.test(callee ?? "") && node.arguments[1]) {
    parts.push(requestPayloadPart("data", node.arguments[1], entry, fn, payloadHints, node));
  }
  const configIndex = /\.(?:post|put|patch)$/u.test(callee ?? "") ? 2 : 1;
  const config = node.arguments[configIndex];
  if (config && ts.isObjectLiteralExpression(config)) {
    const properties = objectProperties(config);
    if (properties.has("params")) {
      parts.push(requestPayloadPart("params", properties.get("params"), entry, fn, payloadHints, node));
    }
  }
  return parts;
}

function requestPayloadPart(kind, expression, entry, fn, payloadHints, callNode) {
  const value = unwrapPayloadExpression(expression);
  const name = payloadRootName(value);
  const isParameter = Boolean(name && fn?.parameters.includes(name));
  let fields = isParameter
    ? payloadHints.parameterShapes.get(fn)?.get(name)
    : resolvePayloadFields(value, payloadHints.namedShapes);
  const deleted = name && fn ? deletedPayloadFields(fn, name, callNode) : new Set();
  return { kind, fields: (fields ?? []).filter((field) => !deleted.has(field.name)) };
}

function deletedPayloadFields(fn, payloadName, beforeNode) {
  const deleted = new Set();
  traverseFunctionBody(fn, (node) => {
    if (node.getStart() >= beforeNode.getStart() || node.kind !== ts.SyntaxKind.DeleteExpression) return;
    const target = node.expression;
    if (ts.isPropertyAccessExpression(target) && expressionName(target.expression) === payloadName) {
      deleted.add(target.name.text);
    } else if (
      ts.isElementAccessExpression(target)
      && expressionName(target.expression) === payloadName
      && target.argumentExpression
    ) {
      const name = literalString(target.argumentExpression);
      if (name) deleted.add(name);
    }
  });
  return deleted;
}

export function collectHttpUrlSymbols(files, builder) {
  const candidates = new Map();
  const entries = files
    .filter((file) => FRONTEND_EXTS.has(path.extname(file)))
    .filter((file) => relPath(builder.root, file).split("/").includes("api"))
    .map((file) => {
      const text = readSourceText(file, builder);
      if (text === undefined) return undefined;
      return { file, text, sourceFile: createTsSourceFile(file, text) };
    })
    .filter(Boolean);
  const declarations = [];

  for (const entry of entries) {
    traverseTs(entry.sourceFile, (node) => {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
        declarations.push({ name: node.name.text, declaration: node.initializer });
      } else if (ts.isFunctionDeclaration(node) && node.name && node.body) {
        declarations.push({ name: node.name.text, declaration: node });
      }
    });
  }

  const resolved = new Map();
  for (let pass = 0; pass < declarations.length + 1; pass += 1) {
    let changed = false;
    for (const declaration of declarations) {
      const url = resolveDeclaredHttpUrl(declaration.declaration, resolved);
      if (!url || (!url.includes("/") && !/^https?:\/\//u.test(url))) continue;
      const values = candidates.get(declaration.name) ?? new Set();
      values.add(url);
      candidates.set(declaration.name, values);
      if (values.size === 1 && resolved.get(declaration.name) !== url) {
        resolved.set(declaration.name, url);
        changed = true;
      } else if (values.size > 1) {
        resolved.delete(declaration.name);
      }
    }
    if (!changed) break;
  }
  return resolved;
}

function resolveDeclaredHttpUrl(node, symbols) {
  if (!node) return undefined;
  if (
    (ts.isArrowFunction(node) || ts.isFunctionExpression(node) || ts.isFunctionDeclaration(node))
    && node.body
    && ts.isBlock(node.body)
  ) {
    const localSymbols = new Map(symbols);
    for (const statement of node.body.statements) {
      if (ts.isVariableStatement(statement)) {
        for (const declaration of statement.declarationList.declarations) {
          if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue;
          const value = resolveHttpUrl(declaration.initializer, localSymbols);
          if (value !== undefined) localSymbols.set(declaration.name.text, value);
        }
      }
      if (ts.isReturnStatement(statement) && statement.expression) {
        return resolveHttpUrl(statement.expression, localSymbols);
      }
    }
    return undefined;
  }
  return resolveHttpUrl(returnedExpression(node), symbols);
}

export function resolveHttpUrl(node, symbols) {
  if (!node) return undefined;
  if (ts.isStringLiteralLike(node)) return node.text;
  if (ts.isParenthesizedExpression(node)) return resolveHttpUrl(node.expression, symbols);
  if (ts.isIdentifier(node)) return symbols.get(node.text);
  if (ts.isCallExpression(node)) {
    const callee = expressionName(node.expression);
    return callee ? symbols.get(callee) : undefined;
  }
  if (ts.isTemplateExpression(node)) {
    let result = node.head.text;
    for (const span of node.templateSpans) {
      const resolved = resolveHttpUrl(span.expression, symbols);
      result += `${resolved ?? `{${expressionName(span.expression) ?? "dynamic"}}`}${span.literal.text}`;
    }
    return result;
  }
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = resolveHttpUrl(node.left, symbols);
    const right = resolveHttpUrl(node.right, symbols);
    return left !== undefined && right !== undefined ? left + right : undefined;
  }
  return undefined;
}
function resolveBoundedUrl(node, entry, symbols, basePaths = []) {
  const literal = node && !ts.isTemplateExpression(node) ? resolveHttpUrl(node, symbols) : undefined;
  if (literal !== undefined) {
    return literalTarget(literal)
      ?? configuredBaseTarget(literal, basePaths)
      ?? { normalizedPath: "/{u0}", resolution: "unbounded" };
  }
  if (!node || !ts.isTemplateExpression(node)) return undefined;
  const segments = [node.head.text];
  const placeholders = [];
  for (const [ordinal, span] of node.templateSpans.entries()) {
    const domain = boundedDomain(span.expression, entry);
    if (!domain) return { normalizedPath: unboundedTemplate(node), resolution: "unbounded" };
    const token = `p${ordinal}`;
    segments.push(`{${token}}`, span.literal.text);
    placeholders.push({ token, values: domain });
  }
  const normalizedPath = segments.join("");
  if (!safeTemplatePath(normalizedPath) || placeholders.length === 0) {
    return { normalizedPath: unboundedTemplate(node), resolution: "unbounded" };
  }
  let product = 1;
  const records = [];
  for (const placeholder of placeholders) {
    product *= placeholder.values.length;
    const acceptedConverters = BUILTIN_CONVERTERS.filter((kind) => (
      placeholder.values.every((value) => acceptsConverter(kind, value))
    ));
    if (!acceptedConverters.length || product > URL_PROOF_LIMITS.domainProduct) {
      return { normalizedPath: unboundedTemplate(node), resolution: "unbounded" };
    }
    records.push({ token: placeholder.token, memberCount: placeholder.values.length, acceptedConverters });
  }
  const pathSegments = normalizedPath.split("/").slice(1);
  return {
    normalizedPath,
    resolution: "bounded_template",
    placeholders: records.map((record) => ({ ...record, segmentIndex: pathSegments.indexOf(`{${record.token}}`) })),
  };
}

function literalTarget(value) {
  if (typeof value !== "string" || !value.startsWith("/")) return externalTarget(value);
  if (!isCanonicalLocalOriginPath(value)) return undefined;
  const [rawPath, rawQuery = ""] = value.split("?", 2);
  try {
    const url = new URL(value, "http://fixture.invalid");
    if (url.username || url.password || url.hash || url.pathname !== rawPath || !safeTemplatePath(rawPath)) return undefined;
    return {
      normalizedPath: rawPath,
      resolution: "literal",
      queryFieldCount: rawQuery ? url.searchParams.size : 0,
      hasSensitiveQuery: [...url.searchParams.keys()].some(isSensitiveQueryName),
    };
  } catch {
    return undefined;
  }
}

function configuredBaseTarget(value, basePaths) {
  if (
    typeof value !== "string"
    || !value
    || value.startsWith("/")
    || value.includes("//")
    || value.includes("://")
    || /[{}:?#\s\\]/u.test(value)
    || value.includes("%")
    || value.split("/").some((segment) => segment === "." || segment === "..")
    || basePaths.length !== 1
  ) {
    return undefined;
  }
  const base = basePaths[0];
  if (
    typeof base !== "string"
    || !base.startsWith("/")
    || base.startsWith("//")
    || base.slice(1).includes("//")
    || /[{}:?#\s\\]/u.test(base)
    || base.includes("%")
    || base.split("/").some((segment) => segment === "." || segment === "..")
  ) {
    return undefined;
  }
  const normalizedBase = base === "/" ? "" : base.replace(/\/+$/u, "");
  return literalTarget(`${normalizedBase}/${value}`);
}

function isCanonicalLocalOriginPath(value) {
  if (value.startsWith("//") || value.includes("#") || /[\s\\]/u.test(value)) return false;
  const [rawPath] = value.split("?", 1);
  return Boolean(rawPath) && !/%(?![0-9a-f]{2})/iu.test(value)
    && rawPath.split("/").every((segment) => segment !== "." && segment !== "..");
}

function externalTarget(value) {
  if (typeof value !== "string" || !/^https?:\/\//iu.test(value)) return undefined;
  try {
    const url = new URL(value);
    const scheme = url.protocol.slice(0, -1).toLowerCase();
    const encodedHost = url.hostname.toLowerCase();
    const host = encodedHost.startsWith("[") && encodedHost.endsWith("]")
      ? encodedHost.slice(1, -1)
      : encodedHost;
    if (
      (scheme !== "http" && scheme !== "https")
      || url.username
      || url.password
      || url.hash
      || !isSafeExternalHost(host)
    ) return undefined;
    const port = url.port ? Number(url.port) : undefined;
    if (port !== undefined && (!Number.isInteger(port) || port < 1 || port > 65535)) return undefined;
    return {
      normalizedPath: "/",
      resolution: "literal",
      external: {
        scheme,
        host,
        ...(port ? { port } : {}),
        pathPresent: url.pathname !== "/",
        queryFieldCount: url.searchParams.size,
        hasSensitiveQuery: [...url.searchParams.keys()].some(isSensitiveQueryName),
      },
    };
  } catch {
    return undefined;
  }
}

function isSafeExternalHost(host) {
  if (!host || host.endsWith(".")) return false;
  if (host.includes(":")) {
    if (!/^[0-9a-f:]+$/u.test(host)) return false;
    try {
      return new URL(`http://[${host}]`).hostname === `[${host}]`;
    } catch {
      return false;
    }
  }
  return host.split(".").every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u.test(label));
}

function isSensitiveQueryName(name) {
  const normalized = name.replace(/([a-z])([A-Z])/gu, "$1_$2").toLowerCase();
  return /(?:^|[_-])(authorization|cookie|password|passwd|pwd|secret|token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|credential|credentials|private[_-]?key|client[_-]?secret|session|sessionid|csrf|xsrf|baggage)(?:$|[_-])/u.test(normalized);
}

function unboundedTemplate(node) {
  const text = ts.isTemplateExpression(node) ? node.head.text : "/";
  const prefix = text.startsWith("/") ? text.split("/").slice(0, -1).join("/") || "" : "";
  return `${prefix}/{u0}`.replace(/\/+/gu, "/");
}

function safeTemplatePath(value) {
  return typeof value === "string" && value.startsWith("/")
    && !/[?#\\]/u.test(value) && !/%2f|%5c|%2e/iu.test(value)
    && value.split("/").every((segment) => segment !== "." && segment !== ".." && !/\s/u.test(segment));
}

function boundedDomain(node, entry) {
  if (!node) return undefined;
  let domainNode = node;
  if (ts.isCallExpression(node)) {
    if (expressionName(node.expression) !== "encodeURIComponent"
      || !isUnshadowedGlobalEncoder(node, entry.sourceFile)) return undefined;
    domainNode = node.arguments[0];
  }
  const values = finiteDomain(domainNode, entry.sourceFile);
  if (!values || values.length === 0 || values.length > URL_PROOF_LIMITS.memberCount) return undefined;
  const unique = [...new Set(values)].sort();
  return unique.every(isSafeDomainMember) ? unique : undefined;
}

function isUnshadowedGlobalEncoder(node, sourceFile) {
  for (let scope = node.expression.parent; scope; scope = scope.parent) {
    if (directBindings(scope, "encodeURIComponent", node.expression, sourceFile, true).length) return false;
  }
  let mutated = false;
  traverseTs(sourceFile, (candidate) => {
    if (mutated) return;
    if (
      ts.isBinaryExpression(candidate)
      && candidate.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
      && candidate.operatorToken.kind <= ts.SyntaxKind.LastAssignment
      && assignsGlobalEncoder(candidate.left)
    ) {
      mutated = true;
    } else if (
      (ts.isPrefixUnaryExpression(candidate) || ts.isPostfixUnaryExpression(candidate))
      && (isGlobalEncoderReference(candidate.operand)
        || (ts.isIdentifier(candidate.operand) && candidate.operand.text === "encodeURIComponent"))
      && (candidate.operator === ts.SyntaxKind.PlusPlusToken || candidate.operator === ts.SyntaxKind.MinusMinusToken)
    ) {
      mutated = true;
    }
  });
  return !mutated;
}
function assignsGlobalEncoder(node) {
  if (ts.isParenthesizedExpression(node)) return assignsGlobalEncoder(node.expression);
  if (ts.isIdentifier(node)) return node.text === "encodeURIComponent";
  if (isGlobalEncoderReference(node)) return true;
  if (ts.isObjectLiteralExpression(node)) {
    return node.properties.some((property) => (
      ts.isShorthandPropertyAssignment(property)
        ? property.name.text === "encodeURIComponent"
        : ts.isPropertyAssignment(property)
          ? assignsGlobalEncoder(property.initializer)
          : false
    ));
  }
  if (ts.isArrayLiteralExpression(node)) return node.elements.some((element) => assignsGlobalEncoder(element));
  return false;
}

function isGlobalEncoderReference(node) {
  const object = ts.isPropertyAccessExpression(node)
    ? node.expression
    : ts.isElementAccessExpression(node)
      ? node.expression
      : undefined;
  const property = ts.isPropertyAccessExpression(node)
    ? node.name
    : ts.isElementAccessExpression(node)
      ? node.argumentExpression
      : undefined;
  return Boolean(
    object
    && ts.isIdentifier(object)
    && ["globalThis", "global", "self", "window"].includes(object.text)
    && property
    && ((ts.isIdentifier(property) && property.text === "encodeURIComponent")
      || (ts.isStringLiteralLike(property) && property.text === "encodeURIComponent"))
  );
}

function finiteDomain(node, sourceFile) {
  if (!node) return undefined;
  const literal = scalarLiteralValue(node);
  if (literal !== undefined) return [literal];
  if (ts.isParenthesizedExpression(node)) return finiteDomain(node.expression, sourceFile);
  if (ts.isConditionalExpression(node)) {
    const left = finiteDomain(node.whenTrue, sourceFile);
    const right = finiteDomain(node.whenFalse, sourceFile);
    return left && right ? [...new Set([...left, ...right])] : undefined;
  }
  if (ts.isIdentifier(node)) {
    const declaration = findLexicalBinding(node.text, node, sourceFile);
    if (!isImmutableUniqueScalarDeclaration(declaration, node, sourceFile)) return undefined;
    return finiteDomain(declaration.initializer, sourceFile);
  }
  return undefined;
}

function scalarLiteralValue(node) {
  if (ts.isStringLiteralLike(node)) return node.text;
  if (ts.isNumericLiteral(node)) {
    const source = node.getText();
    if (!/^(?:0|[1-9][0-9]*)$/u.test(source)) return undefined;
    const runtime = String(Number(source));
    return /^(?:0|[1-9][0-9]*)$/u.test(runtime) ? runtime : undefined;
  }
  return undefined;
}

function isImmutableUniqueScalarDeclaration(declaration, reference, sourceFile) {
  if (!declaration || !ts.isVariableDeclaration(declaration) || !ts.isIdentifier(declaration.name) || !declaration.initializer) return false;
  const declarationList = declaration.parent;
  if (!ts.isVariableDeclarationList(declarationList) || (declarationList.flags & ts.NodeFlags.Const) === 0) return false;
  const name = declaration.name.text;
  let mutable = false;
  traverseTs(sourceFile, (candidate) => {
    if (mutable || candidate === declaration.name) return;
    if (
      ts.isBinaryExpression(candidate)
      && ts.isIdentifier(candidate.left)
      && candidate.left.text === name
      && candidate.left.getStart(sourceFile) !== declaration.name.getStart(sourceFile)
      && candidate.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
      && candidate.operatorToken.kind <= ts.SyntaxKind.LastAssignment
    ) {
      mutable = true;
    } else if (
      (ts.isPrefixUnaryExpression(candidate) || ts.isPostfixUnaryExpression(candidate))
      && ts.isIdentifier(candidate.operand)
      && candidate.operand.text === name
      && (candidate.operator === ts.SyntaxKind.PlusPlusToken || candidate.operator === ts.SyntaxKind.MinusMinusToken)
    ) {
      mutable = true;
    }
  });
  return !mutable && reference.getStart(sourceFile) > declaration.getStart(sourceFile);
}

function findLexicalBinding(name, reference, sourceFile, includeFuture = false) {
  for (let scope = reference.parent; scope; scope = scope.parent) {
    const bindings = directBindings(scope, name, reference, sourceFile, includeFuture);
    if (bindings.length) return bindings.length === 1 ? bindings[0] : undefined;
  }
  return undefined;
}

function directBindings(scope, name, reference, sourceFile, includeFuture) {
  const bindings = [];
  const add = (candidate) => {
    if (candidate && ts.isIdentifier(candidate) && candidate.text === name
      && (includeFuture || candidate.getStart(sourceFile) < reference.getStart(sourceFile))) bindings.push(candidate.parent);
  };
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node)) add(node.name);
    if (node !== scope && isNestedFunctionBoundary(node)) return;
    if (ts.isVariableDeclaration(node) || ts.isClassDeclaration(node)
      || ts.isImportSpecifier(node) || ts.isImportClause(node) || ts.isNamespaceImport(node) || ts.isBindingElement(node)) add(node.name);
    if (ts.isCatchClause(node)) add(node.variableDeclaration?.name);
    ts.forEachChild(node, visit);
  };
  if ((ts.isFunctionExpression(scope) || ts.isClassExpression(scope))) add(scope.name);
  if (ts.isFunctionLike(scope)) scope.parameters.forEach((parameter) => {
    add(parameter.name);
    ts.forEachChild(parameter.name, visit);
  });
  if (ts.isSourceFile(scope) || ts.isBlock(scope) || ts.isModuleBlock(scope) || ts.isCatchClause(scope)) ts.forEachChild(scope, visit);
  return bindings;
}

function isSafeDomainMember(value) {
  return typeof value === "string"
    && value !== "."
    && value !== ".."
    && /^[A-Za-z0-9._~-]+$/u.test(value);
}

function acceptsConverter(kind, value) {
  if (!isSafeDomainMember(value)) return false;
  if (kind === "int") return /^[0-9]+$/u.test(value);
  if (kind === "slug") return /^[-A-Za-z0-9_]+$/u.test(value);
  if (kind === "uuid") return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u.test(value);
  return true;
}

export function httpMethod(callee, node) {
  if (callee?.endsWith(".post")) return "POST";
  if (callee?.endsWith(".put")) return "PUT";
  if (callee?.endsWith(".patch")) return "PATCH";
  if (callee?.endsWith(".delete")) return "DELETE";
  const init = node.arguments[1];
  if (init && ts.isObjectLiteralExpression(init)) {
    for (const prop of init.properties) {
      if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name) && prop.name.text === "method") {
        return literalString(prop.initializer)?.toUpperCase();
      }
      if (ts.isShorthandPropertyAssignment(prop) && prop.name.text === "method") {
        return undefined;
      }
    }
  }
  return "GET";
}

function httpCallOrdinal(fn, node) {
  return syntaxOrdinal(fn, node, (candidate) => (
    ts.isCallExpression(candidate) && isHttpCallExpression(candidate)
  ));
}

function isHttpCallExpression(node) {
  const callee = expressionName(node.expression);
  return callee === "useAxios"
    || callee === "fetch"
    || NUXT_FETCH_CALLEES.has(callee ?? "")
    || callee?.startsWith("axios.")
    || /^(?:api|client|http)\.(?:get|post|put|patch|delete)$/u.test(callee ?? "");
}
export function addHttpCall(builder, entry, framework, ownerKey, method, target, source, payload = [], fn, callNode) {
  const ordinal = httpCallOrdinal(fn, callNode);
  return addHttpCallWithOrdinal(builder, entry, framework, ownerKey, method, target, source, payload, ordinal);
}

function addHttpCallWithOrdinal(builder, entry, framework, ownerKey, method, target, source, payload = [], ordinal = 0) {
  const normalizedPath = target.normalizedPath;
  const key = `http:${framework}:${ownerKey}:${method}:${normalizedPath}:${ordinal}`;
  const metadata = target.external
    ? {
      method,
      urlResolution: "literal",
      normalizedPath: "/",
      queryFieldCount: 0,
      hasSensitiveQuery: false,
    }
    : {
      method,
      urlResolution: target.resolution,
      normalizedPath,
      ...(target.resolution === "literal" ? { endpointId: `${method} ${normalizedPath}` } : {}),
      queryFieldCount: target.queryFieldCount ?? 0,
      hasSensitiveQuery: target.hasSensitiveQuery ?? false,
    };
  builder.addNode({
    key,
    kind: "http_call",
    label: `${method} ${normalizedPath}`,
    layer: "http",
    source,
    identity: `http:${ownerKey}:${method}:${normalizedPath}:${ordinal}`,
    evidenceKind: "inferred",
    reason: target.resolution === "bounded_template" ? "finite_url_domain" : "literal_url",
    confidence: 0.86,
    metadata,
  });
  if (target.resolution === "bounded_template") {
    const proof = {
      version: URL_PROOF_VERSION,
      callKey: key,
      normalizedPath,
      placeholders: target.placeholders,
    };
    if (validateBoundedUrlProof(proof)) builder.boundedUrlProofs.set(key, proof);
  }
  builder.addEdge({ source: ownerKey, target: key, kind: "calls", confidence: 0.82, metadata: {} });
  const structuralQuery = !payload.length && (target.queryFieldCount ?? 0) > 0;
  if (!payload.length && !structuralQuery) {
    if (target.external) addExternalTopology(builder, key, source, method, target.external);
    else addUnboundedTopology(builder, key, source);
    return;
  }
  const kinds = structuralQuery
    ? ["query"]
    : [...new Set(payload.map((part) => ({ data: "body", body: "body", form: "form", params: "query" }[part.kind] ?? "body")))].sort();
  const bodyParts = payload.filter((part) => part.kind !== "params");
  const bodyFields = bodyParts.flatMap((part) => part.fields);
  const queryFields = payload.filter((part) => part.kind === "params").flatMap((part) => part.fields);
  const allFields = [...bodyFields, ...queryFields];
  const payloadKey = `request_payload:${key}`;
  builder.addNode({
    key: payloadKey,
    kind: "request_payload",
    label: "Request payload",
    layer: "http",
    source,
    identity: `request_payload:${key}`,
    evidenceKind: "inferred",
    reason: "request_payload_shape",
    confidence: allFields.length ? 0.82 : 0.68,
    metadata: {
      payloadKinds: kinds,
      bodyShape: bodyParts.length ? (bodyFields.length ? "object" : "unknown") : "none",
      bodyFieldCount: bodyFields.length,
      queryFieldCount: structuralQuery ? target.queryFieldCount : queryFields.length,
      hasSensitiveFields: structuralQuery ? target.hasSensitiveQuery : allFields.some((field) => isSensitiveQueryName(field.name)),
    },
  });
  builder.addEdge({ source: key, target: payloadKey, kind: "carries", confidence: 0.82, metadata: { payloadKinds: kinds } });
  if (target.external) addExternalTopology(builder, payloadKey, source, method, target.external);
  else addUnboundedTopology(builder, payloadKey, source);
}

function addExternalTopology(builder, sourceKey, source, method, external) {
  const authority = external.host.includes(":")
    ? `[${external.host}]${external.port ? `:${external.port}` : ""}`
    : `${external.host}${external.port ? `:${external.port}` : ""}`;
  const key = `external:${method}:${external.scheme}:${authority}`;
  builder.addNode({
    key,
    kind: "external_service",
    label: `${method} ${external.scheme}://${authority}`,
    layer: "external",
    source,
    identity: key,
    evidenceKind: "inferred",
    reason: "external_boundary",
    confidence: 0.86,
    metadata: { method, ...external, boundaryOnly: true },
  });
  builder.addEdge({
    source: sourceKey,
    target: key,
    kind: "resolves_to",
    confidence: 0.86,
    metadata: { resolutionTier: "external_boundary" },
    evidenceKind: "inferred",
    reason: "external_boundary",
  });
}
function addUnboundedTopology(builder, sourceKey, source) {
  const key = `unresolved:${sourceKey}`;
  builder.addNode({
    key,
    kind: "unresolved_target",
    label: "Unresolved",
    layer: "unresolved",
    source,
    evidenceKind: "unresolved",
    reason: "dynamic_target_unproven",
    confidence: 0.3,
    metadata: { reasonCode: "dynamic_target_unproven" },
  });
  builder.addEdge({
    source: sourceKey,
    target: key,
    kind: "resolves_to",
    confidence: 0.3,
    metadata: { resolutionTier: "unbounded" },
    evidenceKind: "unresolved",
    reason: "dynamic_target_unproven",
  });
}
