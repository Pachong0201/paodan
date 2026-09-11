#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "[ERROR] 未找到 Python 3.11+"
  exit 1
fi
echo "Paodan V5.0.2 Local Workbench"
exec "$PY" scripts/open_workbench.py
