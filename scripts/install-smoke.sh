#!/usr/bin/env sh

set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: scripts/install-smoke.sh <archive.tar.gz> <archive.tar.gz.sha256>" >&2
  exit 2
fi

ARCHIVE=$1
CHECKSUM=$2
PYTHON=${PYTHON:-python3.13}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "python3.13 is required to verify and install the release archive." >&2
  exit 1
fi
if [ ! -f "$ARCHIVE" ] || [ ! -f "$CHECKSUM" ]; then
  echo "release archive and checksum must both exist." >&2
  exit 1
fi

ARCHIVE=$(
  "$PYTHON" - "$ARCHIVE" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve(strict=True))
PY
)
CHECKSUM=$(
  "$PYTHON" - "$CHECKSUM" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve(strict=True))
PY
)

"$PYTHON" - "$ARCHIVE" "$CHECKSUM" <<'PY'
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

archive = Path(sys.argv[1])
checksum = Path(sys.argv[2])
line = checksum.read_text(encoding="ascii").strip()
match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
if match is None or match.group(2) != archive.name:
    raise SystemExit("invalid release checksum file")

actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != match.group(1):
    raise SystemExit("release checksum verification failed")
PY

ARCHIVE_NAME=$(basename "$ARCHIVE")
case "$ARCHIVE_NAME" in
  code-debugger-v*.tar.gz) ;;
  *)
    echo "invalid release archive name: $ARCHIVE_NAME" >&2
    exit 1
    ;;
esac
INSTALL_ROOT_NAME=${ARCHIVE_NAME%.tar.gz}

TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/code-debugger-install.XXXXXX")
cleanup() {
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT HUP INT TERM

tar -xzf "$ARCHIVE" -C "$TEMP_ROOT"
INSTALL_ROOT="$TEMP_ROOT/$INSTALL_ROOT_NAME"
if [ ! -x "$INSTALL_ROOT/scripts/bootstrap.sh" ] || [ ! -x "$INSTALL_ROOT/scripts/run.sh" ]; then
  echo "release archive is missing executable bootstrap or run scripts." >&2
  exit 1
fi

cd "$INSTALL_ROOT"
./scripts/bootstrap.sh
KG_DEBUGGER_ROOT=$INSTALL_ROOT
export KG_DEBUGGER_ROOT
. "$INSTALL_ROOT/scripts/env.sh"

case "${INSTALL_PLAYWRIGHT_DEPS:-0}" in
  0) ;;
  1) npx playwright install-deps chromium ;;
  *)
    echo "INSTALL_PLAYWRIGHT_DEPS must be 0 or 1." >&2
    exit 2
    ;;
esac

npm run test:install
