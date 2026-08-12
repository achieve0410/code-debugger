import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const repoRoot = path.resolve(import.meta.dirname, "../..");

test("release notes do not label historical run links exact-main", async () => {
  const notes = await fs.readFile(
    path.join(repoRoot, "docs/releases/v0.2.3.md"),
    "utf8",
  );

  assert.doesNotMatch(notes, /Exact-main (?:CI|CodeQL):/u);
  assert.match(notes, /Pre-release source CI:/u);
  assert.match(notes, /Pre-release source CodeQL:/u);
  assert.match(notes, /The release workflow re-runs the complete exact-archive/u);
});
