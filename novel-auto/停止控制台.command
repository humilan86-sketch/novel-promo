#!/bin/bash
# 双击停止后台控制台服务
cd "$(dirname "$0")" || exit 1
PID_FILE=".cache/console.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")" && rm -f "$PID_FILE"
  echo "✅ 控制台已停止"
else
  pkill -f "novelauto web" 2>/dev/null
  rm -f "$PID_FILE"
  echo "控制台没有在运行（已清理）"
fi
sleep 1
