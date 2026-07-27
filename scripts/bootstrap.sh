#!/usr/bin/env sh

set -eu

KG_DEBUGGER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export KG_DEBUGGER_ROOT
cd "$KG_DEBUGGER_ROOT"

if ! command -v python3.13 >/dev/null 2>&1; then
  echo "Python 3.13 is required (python3.13 was not found)." >&2
  exit 1
fi

mkdir -p "$KG_DEBUGGER_ROOT/venv"

if [ ! -x "$KG_DEBUGGER_ROOT/venv/python3.13/bin/python" ]; then
  python3.13 -m venv "$KG_DEBUGGER_ROOT/venv/python3.13"
fi

if [ ! -x "$KG_DEBUGGER_ROOT/venv/node24.14.1/bin/node" ]; then
  if [ -e "$KG_DEBUGGER_ROOT/venv/node24.14.1" ]; then
    echo "venv/node24.14.1 exists but is incomplete; remove it and rerun bootstrap." >&2
    exit 1
  fi

  "$KG_DEBUGGER_ROOT/venv/python3.13/bin/python" - <<'PY'
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

version = "24.14.1"
system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
machine = {
    "arm64": "arm64",
    "aarch64": "arm64",
    "x86_64": "x64",
    "amd64": "x64",
}.get(platform.machine().lower())
if system is None or machine is None:
    raise SystemExit(
        f"unsupported Node.js platform: {platform.system()} {platform.machine()}"
    )

root = Path(os.environ["KG_DEBUGGER_ROOT"])
target = root / "venv" / f"node{version}"
archive_name = f"node-v{version}-{system}-{machine}.tar.gz"
base_url = f"https://nodejs.org/dist/v{version}"

with tempfile.TemporaryDirectory(dir=root / "venv") as temp_dir_name:
    temp_dir = Path(temp_dir_name)
    archive_path = temp_dir / archive_name
    checksums_path = temp_dir / "SHASUMS256.txt"
    urllib.request.urlretrieve(f"{base_url}/{archive_name}", archive_path)
    urllib.request.urlretrieve(f"{base_url}/SHASUMS256.txt", checksums_path)

    expected = next(
        (
            line.split()[0]
            for line in checksums_path.read_text(encoding="utf-8").splitlines()
            if line.split()[-1].lstrip("*") == archive_name
        ),
        None,
    )
    actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if expected is None or actual != expected:
        raise SystemExit("Node.js archive checksum verification failed")

    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(temp_dir, filter="data")
    shutil.move(str(temp_dir / archive_name.removesuffix(".tar.gz")), target)
PY
fi

. "$KG_DEBUGGER_ROOT/scripts/env.sh"

if [ "$(node --version)" != "v24.14.1" ]; then
  echo "Expected Node.js v24.14.1 in venv/node24.14.1." >&2
  exit 1
fi

"$KG_DEBUGGER_ROOT/scripts/generate-dev-cert.sh"

PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  npm ci --registry=https://registry.npmjs.org

./venv/python3.13/bin/python -m pip install \
  --index-url https://pypi.org/simple \
  --cache-dir "$KG_DEBUGGER_ROOT/venv/python3.13/pip-cache" \
  -r requirements.lock \
  -r requirements-dev.lock

PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  npx playwright install chromium
