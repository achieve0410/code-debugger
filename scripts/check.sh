#!/usr/bin/env sh

set -eu
. "$(dirname "$0")/env.sh"
cd "$KG_DEBUGGER_ROOT"

npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
