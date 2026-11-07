#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo '请先安装 FFmpeg（同时需要 ffprobe）'; exit 1; }
command -v ffprobe >/dev/null || { echo '找不到 ffprobe'; exit 1; }
[ -x .venv/bin/python ] || { echo '请先按 README 创建 Python 3.11 虚拟环境并安装依赖'; exit 1; }
[ -f frontend/dist/index.html ] || { echo '请先执行 cd frontend && npm ci && npm run build'; exit 1; }
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port "${PORT:-8765}" --workers 1
