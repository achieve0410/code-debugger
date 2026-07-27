#!/usr/bin/env sh

set -eu

KG_DEBUGGER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export KG_DEBUGGER_ROOT
KG_DEBUGGER_NODE_HOME="$KG_DEBUGGER_ROOT/venv/node24.14.1"
export KG_DEBUGGER_NODE="$KG_DEBUGGER_NODE_HOME/bin/node"
export PATH="$KG_DEBUGGER_NODE_HOME/bin:$PATH"
export PYTHONPATH="$KG_DEBUGGER_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export npm_config_cache="$KG_DEBUGGER_NODE_HOME/npm-cache"
export PLAYWRIGHT_BROWSERS_PATH="$KG_DEBUGGER_NODE_HOME/playwright-browsers"
export KG_DEBUGGER_CERT="$KG_DEBUGGER_ROOT/pem/cert.pem"
export KG_DEBUGGER_KEY="$KG_DEBUGGER_ROOT/pem/key.pem"
