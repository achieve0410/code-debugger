#!/usr/bin/env sh

set -eu
. "$(dirname "$0")/env.sh"
cd "$KG_DEBUGGER_ROOT"

npm run build
exec ./venv/python3.13/bin/python -m kg_debugger.app "$@"
