export const URL_PROOF_VERSION = 1;
export const URL_PROOF_LIMITS = Object.freeze({
  proofs: 10_000,
  placeholders: 32,
  memberCount: 256,
  domainProduct: 4096,
  serializedBytes: 8192,
  segmentIndex: 255,
});
export const BUILTIN_CONVERTERS = Object.freeze(["int", "slug", "str", "uuid"]);
export const EVIDENCE_REASONS = Object.freeze({
  inferred: Object.freeze(["ast_call", "ast_handler_binding", "ast_import_binding", "ast_route_declaration", "ast_symbol_declaration", "finite_url_domain", "literal_url", "request_payload_shape", "external_boundary"]),
  unresolved: Object.freeze(["dynamic_target_unproven", "referenced_target_missing", "unsupported_syntax"]),
});
export const DIAGNOSTIC_CODES = Object.freeze(["source_read_failed", "unsupported_syntax", "unresolved_dynamic_target", "unresolved_referenced_target"]);

const TOKEN = /^[^\u0000-\u001f\u007f]{1,512}$/u;
const PLACEHOLDER = /^\{p([0-9]|[12][0-9]|3[01])\}$/u;

function integer(value, minimum, maximum) {
  return Number.isInteger(value) && typeof value !== "boolean" && value >= minimum && value <= maximum;
}

export function validateBoundedUrlProof(proof) {
  if (!proof || typeof proof !== "object" || Array.isArray(proof)
    || Object.keys(proof).sort().join(",") !== "callKey,normalizedPath,placeholders,version"
    || proof.version !== URL_PROOF_VERSION
    || typeof proof.callKey !== "string" || !TOKEN.test(proof.callKey)
    || typeof proof.normalizedPath !== "string" || !proof.normalizedPath.startsWith("/")) return false;
  if (!Array.isArray(proof.placeholders) || proof.placeholders.length < 1 || proof.placeholders.length > URL_PROOF_LIMITS.placeholders) return false;
  const expected = proof.normalizedPath.split("/").slice(1).flatMap((segment, segmentIndex) => {
    const match = PLACEHOLDER.exec(segment);
    return match ? [[`p${match[1]}`, segmentIndex]] : [];
  });
  if (expected.length !== proof.placeholders.length) return false;
  let product = 1;
  for (let index = 0; index < proof.placeholders.length; index += 1) {
    const item = proof.placeholders[index];
    if (!item || typeof item !== "object" || Array.isArray(item)
      || Object.keys(item).sort().join(",") !== "acceptedConverters,memberCount,segmentIndex,token"
      || item.token !== expected[index][0] || item.segmentIndex !== expected[index][1]
      || !integer(item.segmentIndex, 0, URL_PROOF_LIMITS.segmentIndex)
      || !integer(item.memberCount, 1, URL_PROOF_LIMITS.memberCount)
      || !Array.isArray(item.acceptedConverters) || item.acceptedConverters.length === 0
      || item.acceptedConverters.join(",") !== [...new Set(item.acceptedConverters)].sort().join(",")
      || item.acceptedConverters.some((kind) => !BUILTIN_CONVERTERS.includes(kind))) return false;
    product *= item.memberCount;
  }
  return product <= URL_PROOF_LIMITS.domainProduct
    && Buffer.byteLength(JSON.stringify(proof), "utf8") <= URL_PROOF_LIMITS.serializedBytes;
}
