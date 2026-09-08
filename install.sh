#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "=== Paodan V4.2.1 install ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "需要先安装 Python 3.11+"
  exit 1
fi
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -c constraints.txt
echo "=== install complete ==="
echo "启动命令：python scripts/open_dashboard.py"
