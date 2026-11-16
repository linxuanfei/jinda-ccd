#!/usr/bin/env bash
# VisionApp 启动脚本（macOS / Linux）
# 首次运行会自动创建虚拟环境并安装依赖。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3.11}"
if ! command -v "$PY" >/dev/null 2>&1; then
  PY="python3"
fi

if [ ! -d .venv ]; then
  echo "== 创建虚拟环境 =="
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f .venv/.deps_installed ]; then
  echo "== 安装依赖 =="
  python -m pip install -U pip
  python -m pip install -r requirements.txt
  touch .venv/.deps_installed
fi

exec python tools/visionapp_gui.py "$@"
