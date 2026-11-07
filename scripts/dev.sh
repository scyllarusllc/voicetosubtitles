#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo '请先安装 FFmpeg（同时需要 ffprobe）'; exit 1; }
command -v ffprobe >/dev/null || { echo '找不到 ffprobe'; exit 1; }
command -v npm >/dev/null || { echo '请先安装 Node.js 和 npm'; exit 1; }
[ -x .venv/bin/python ] || { echo '请先按 README 创建 Python 虚拟环境并安装依赖'; exit 1; }
[ -d frontend/node_modules ] || { echo '请先执行 npm --prefix frontend ci'; exit 1; }

backend_pid=''
frontend_pid=''
cleanup() {
  trap - EXIT INT TERM
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765 --reload --reload-dir backend &
backend_pid=$!
# Run Vite directly so cleanup owns its process, rather than an npm wrapper.
node frontend/node_modules/vite/bin/vite.js --config frontend/vite.config.js --host 127.0.0.1 --port 5173 --strictPort frontend &
frontend_pid=$!

# macOS ships Bash 3.2, which does not support wait -n.
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
if ! kill -0 "$backend_pid" 2>/dev/null; then
  wait "$backend_pid"
else
  wait "$frontend_pid"
fi
