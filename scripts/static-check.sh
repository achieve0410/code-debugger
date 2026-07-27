#!/usr/bin/env sh

set -eu
. "$(dirname "$0")/env.sh"
cd "$KG_DEBUGGER_ROOT"

node --check analyzers/index.mjs

for script in scripts/*.sh; do
  sh -n "$script"
done

./venv/python3.13/bin/python - <<'PY'
import ast
from pathlib import Path

files = sorted(Path("src").rglob("*.py")) + sorted(Path("tests/python").rglob("*.py"))
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print(f"validated {len(files)} Python files")
PY
./venv/python3.13/bin/python -m ruff check src/kg_debugger tests/python
./venv/python3.13/bin/python -m mypy src/kg_debugger
