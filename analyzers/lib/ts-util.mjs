import path from "node:path";
import ts from "typescript";

import { lineOf, relPath } from "./shared.mjs";

export function scriptKind(filePath) {
  const ext = path.extname(filePath);
  if (ext === ".tsx") return ts.ScriptKind.TSX;
  if (ext === ".jsx") return ts.ScriptKind.JSX;
  if (ext === ".ts") return ts.ScriptKind.TS;
  return ts.ScriptKind.JS;
}

export function createTsSourceFile(filePath, text) {
  return ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, scriptKind(filePath));
}

export function hasTsParseDiagnostics(sourceFile) {
  return (sourceFile.parseDiagnostics?.length ?? 0) > 0;
}

export function sourceForEntry(entry, builder, node, symbol) {
  const start = node.getStart(entry.sourceFile);
  const end = node.getEnd();
  return {
    repository: builder.repository,
    path: relPath(builder.root, entry.file),
    line: (entry.lineOffset ?? 0) + lineOf(entry.text, start),
    endLine: (entry.lineOffset ?? 0) + lineOf(entry.text, end),
    ...(symbol ? { symbol } : {}),
  };
}

export function collectFunctionInfos(entry) {
  const functions = [];
  traverseTs(entry.sourceFile, (node) => {
    if (ts.isFunctionDeclaration(node) && node.name && node.body) {
      functions.push(functionInfo(entry, node.name.text, node, node.body));
      return;
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && isFunctionExpressionLike(node.initializer) && ts.isBlock(node.initializer.body)) {
      functions.push(functionInfo(entry, node.name.text, node, node.initializer.body));
      return;
    }
    if (ts.isMethodDeclaration(node) && node.body) {
      const name = declarationName(node.name);
      if (name) functions.push(functionInfo(entry, name, node, node.body));
    }
  });
  return functions.sort((a, b) => a.node.getStart(entry.sourceFile) - b.node.getStart(entry.sourceFile));
}

export function functionInfo(entry, name, node, bodyNode) {
  return {
    name,
    node,
    bodyNode,
    body: entry.text.slice(bodyNode.getStart(entry.sourceFile), bodyNode.getEnd()),
    parameters: (node.parameters ?? []).map((parameter) => (
      ts.isIdentifier(parameter.name) ? parameter.name.text : undefined
    )),
  };
}

export function isFunctionExpressionLike(node) {
  return ts.isArrowFunction(node) || ts.isFunctionExpression(node);
}

export function traverseTs(node, visitor) {
  visitor(node);
  ts.forEachChild(node, (child) => traverseTs(child, visitor));
}

export function traverseFunctionBody(fn, visitor) {
  const walk = (node) => {
    if (node !== fn.bodyNode && isNestedFunctionBoundary(node)) return;
    visitor(node);
    ts.forEachChild(node, walk);
  };
  ts.forEachChild(fn.bodyNode, walk);
}
export function syntaxOrdinal(fn, node, matches) {
  if (!fn) return 0;
  let ordinal = 0;
  let result = 0;
  traverseFunctionBody(fn, (candidate) => {
    if (!matches(candidate)) return;
    if (candidate === node) result = ordinal;
    ordinal += 1;
  });
  return result;
}

export function isNestedFunctionBoundary(node) {
  return ts.isFunctionDeclaration(node)
    || ts.isFunctionExpression(node)
    || ts.isMethodDeclaration(node)
    || (ts.isVariableDeclaration(node) && node.initializer && isFunctionExpressionLike(node.initializer));
}

export function declarationName(node) {
  if (ts.isIdentifier(node) || ts.isStringLiteralLike(node)) return node.text;
  return undefined;
}

export function jsxTagName(tagName) {
  if (ts.isIdentifier(tagName)) return tagName.text;
  if (ts.isPropertyAccessExpression(tagName)) return tagName.name.text;
  return undefined;
}

export function jsxAttributeString(attributes, name) {
  const attr = jsxAttribute(attributes, name);
  if (!attr?.initializer) return undefined;
  if (ts.isStringLiteral(attr.initializer)) return attr.initializer.text;
  if (ts.isJsxExpression(attr.initializer)) return literalString(attr.initializer.expression);
  return undefined;
}

export function jsxAttributeComponent(attributes, name) {
  const attr = jsxAttribute(attributes, name);
  if (!attr?.initializer || !ts.isJsxExpression(attr.initializer)) return undefined;
  const expression = attr.initializer.expression;
  if (ts.isJsxElement(expression)) return jsxTagName(expression.openingElement.tagName);
  if (ts.isJsxSelfClosingElement(expression)) return jsxTagName(expression.tagName);
  return undefined;
}

export function jsxAttributeHandlerName(attr) {
  if (!attr.initializer || !ts.isJsxExpression(attr.initializer) || !attr.initializer.expression) return undefined;
  const expression = attr.initializer.expression;
  if (ts.isIdentifier(expression)) return expression.text;
  return undefined;
}

export function jsxAttribute(attributes, name) {
  for (const prop of attributes.properties) {
    if (ts.isJsxAttribute(prop) && prop.name.text === name) return prop;
  }
  return undefined;
}

export function expressionName(node) {
  if (!node) return undefined;
  if (ts.isIdentifier(node)) return node.text;
  if (ts.isPropertyAccessExpression(node)) return `${expressionName(node.expression)}.${node.name.text}`;
  if (ts.isCallExpression(node)) return expressionName(node.expression);
  return undefined;
}

export function literalString(node) {
  if (!node) return undefined;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  return undefined;
}

export function objectProperties(node) {
  const result = new Map();
  for (const prop of node.properties) {
    if (!ts.isPropertyAssignment(prop)) continue;
    const name = declarationName(prop.name);
    if (name) result.set(name, prop.initializer);
  }
  return result;
}

export function objectLiteralStringProperty(node, name) {
  for (const prop of node.properties) {
    if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name) && prop.name.text === name) {
      return literalString(prop.initializer);
    }
  }
  return undefined;
}

export function returnedExpression(node) {
  if (!node) return undefined;
  if (ts.isArrowFunction(node) || ts.isFunctionExpression(node) || ts.isFunctionDeclaration(node)) {
    if (!node.body) return undefined;
    if (!ts.isBlock(node.body)) return node.body;
    const statement = node.body.statements.find((item) => ts.isReturnStatement(item) && item.expression);
    return statement?.expression;
  }
  return node;
}

export function dynamicBranchSignals(fn) {
  const signals = [];
  traverseFunctionBody(fn, (node) => {
    if (ts.isPropertyAccessExpression(node) && expressionName(node) === "window.location") {
      signals.push("Conditional branch contains a dynamic target that cannot be resolved statically");
    }
    if (
      ts.isCallExpression(node)
      && node.expression.kind === ts.SyntaxKind.ImportKeyword
      && node.arguments[0]
      && !ts.isStringLiteralLike(node.arguments[0])
    ) {
      signals.push("Dynamic import target is computed and cannot be resolved statically");
    }
  });
  return signals;
}

export function componentKind(name) {
  return /Page$/u.test(name) ? "page" : "component";
}

export function isComponentName(name) {
  return /^[A-Z]/u.test(name) && name !== "Route" && name !== "Routes" && name !== "Fragment";
}
