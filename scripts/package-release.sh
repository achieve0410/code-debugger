#!/usr/bin/env sh

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT_DIR=${1:-"$ROOT/release-artifacts"}
RELEASE_TAG=${2:-}
PYTHON=${PYTHON:-python3.13}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "python3.13 is required to validate release metadata." >&2
  exit 1
fi

cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "release archive requires a clean tracked worktree." >&2
  exit 1
fi

VERSION=$(
  "$PYTHON" - "$ROOT" <<'PY'
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
package = json.loads((root / "package.json").read_text(encoding="utf-8"))
package_lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
init_source = (root / "src/kg_debugger/__init__.py").read_text(encoding="utf-8")
init_match = re.fullmatch(r'__version__ = "([^"]+)"\n?', init_source)
if init_match is None:
    raise SystemExit("invalid src/kg_debugger/__init__.py version declaration")

versions = {
    "package.json": package.get("version"),
    "package-lock.json": package_lock.get("version"),
    "package-lock root package": package_lock.get("packages", {}).get("", {}).get("version"),
    "pyproject.toml": pyproject.get("project", {}).get("version"),
    "src/kg_debugger/__init__.py": init_match.group(1),
}
if (
    not all(isinstance(value, str) and value for value in versions.values())
    or len(set(versions.values())) != 1
):
    detail = ", ".join(f"{name}={value!r}" for name, value in versions.items())
    raise SystemExit(f"version mismatch: {detail}")
print(next(iter(versions.values())))
PY
)

if [ -n "$RELEASE_TAG" ] && [ "$RELEASE_TAG" != "v$VERSION" ]; then
  echo "release tag mismatch: expected v$VERSION, got $RELEASE_TAG" >&2
  exit 1
fi

FORBIDDEN=$(
  git ls-files -z | "$PYTHON" -c '
import sys

for raw in sys.stdin.buffer.read().split(b"\0"):
    if not raw:
        continue
    path = raw.decode("utf-8", "strict")
    if (
        path.startswith(("venv/", "node_modules/", "pem/", ".kg-debugger/", "web/dist/"))
        or path in {".env", ".env.local"}
        or path.endswith((".pem", ".key"))
    ):
        print(path)
'
)
if [ -n "$FORBIDDEN" ]; then
  echo "forbidden release path is tracked:" >&2
  printf '%s\n' "$FORBIDDEN" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(CDPATH= cd -- "$OUTPUT_DIR" && pwd)
ARCHIVE_NAME="code-debugger-v$VERSION.tar.gz"
CHECKSUM_NAME="$ARCHIVE_NAME.sha256"
ARCHIVE="$OUTPUT_DIR/$ARCHIVE_NAME"
CHECKSUM="$OUTPUT_DIR/$CHECKSUM_NAME"

if [ -e "$ARCHIVE" ] || [ -e "$CHECKSUM" ]; then
  echo "release artifact already exists: $ARCHIVE_NAME" >&2
  exit 1
fi

git archive \
  --format=tar.gz \
  --prefix="code-debugger-v$VERSION/" \
  --output="$ARCHIVE" \
  HEAD

(
  cd "$OUTPUT_DIR"
  shasum -a 256 "$ARCHIVE_NAME" >"$CHECKSUM_NAME"
)

printf '%s\n%s\n' "$ARCHIVE" "$CHECKSUM"
