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

TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/code-debugger-install.XXXXXX")
cleanup() {
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT HUP INT TERM

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
INSTALL_ROOT=$(
  "$PYTHON" "$ROOT/scripts/verify-release-archive.py" \
    "$ARCHIVE" \
    "$CHECKSUM" \
    "$TEMP_ROOT"
)
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
