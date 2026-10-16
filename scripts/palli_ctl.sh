#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3 or python is required to run scripts/palli_ctl.py" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$ROOT_DIR/scripts/palli_ctl.py" "$@"
