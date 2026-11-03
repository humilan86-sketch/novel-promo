#!/bin/bash
# 双击这个文件：启动本地控制台（贴原文 → 生成 → 预览/下载/复制文案）
cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo "  小说推文自动化 · 控制台"
echo "==============================================="
echo

if [ ! -x ".venv/bin/python" ]; then
  echo "❌ 还没装好运行环境。"
  echo "   请在终端执行一次："
  echo "   python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  echo
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

# 已经开着就直接打开浏览器
if curl -s -m 2 -o /dev/null http://127.0.0.1:8765/; then
  echo "✅ 控制台已经在运行，直接打开浏览器…"
  open "http://127.0.0.1:8765/"
  sleep 1
  exit 0
fi

echo "🌐 浏览器会自动打开 http://127.0.0.1:8765"
echo "   用完关闭这个终端窗口即可停止服务。"
echo

./.venv/bin/python -m novelauto web --port 8765 --no-browser &
SERVER_PID=$!

for i in $(seq 1 30); do
  sleep 0.5
  if curl -s -m 1 -o /dev/null http://127.0.0.1:8765/; then break; fi
done
open "http://127.0.0.1:8765/"

wait $SERVER_PID
echo
echo "服务已停止。"
