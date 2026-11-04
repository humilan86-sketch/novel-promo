#!/bin/bash
# 由 launchd 通过「终端」调用的每日生成入口
# （macOS 不允许后台任务直接读 ~/Documents，交给有权限的 Terminal 来跑）
set -uo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ" || exit 1
mkdir -p daily/logs
LOG="daily/logs/daily_$(date +%Y-%m-%d).log"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 每日生成开始 ====="
  ./.venv/bin/python -m novelauto daily
  code=$?
  echo "===== 结束（exit=${code}）====="
  echo "成片在 output/ 里；日志：$LOG"
  echo "这个窗口可以关闭了。"
} 2>&1 | tee -a "$LOG"
