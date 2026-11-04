#!/bin/bash
# 安装「每天自动生成」定时任务（launchd）+ 可选设置自动唤醒
# 用法：双击项目根目录的「设置每日自动生成.command」，或在终端运行这个脚本。
set -uo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
PY="$PROJ/.venv/bin/python"
LABEL="com.huqi.novel-promo.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "==============================================="
echo "  小说推文 · 每天自动生成"
echo "==============================================="
echo

if [ ! -x "$PY" ]; then
  echo "❌ 还没装好运行环境：$PY"
  echo "   先按 README 建 .venv 并安装依赖。"
  echo
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

SILENT=0
[ "${1:-}" = "--silent" ] && SILENT=1

TIME="$(cd "$PROJ" && "$PY" -c "from novelauto.config import load; print(load().get('daily.time','08:00'))" 2>/dev/null)"
TIME="${TIME:-08:00}"
HOUR="${TIME%%:*}"
MIN="${TIME##*:}"
# 去掉前导零（plist 里用整数）
HOUR="$((10#$HOUR))"
MIN="$((10#$MIN))"

if [ "$SILENT" = "1" ]; then
  PROG_ARGS="<string>$PY</string><string>-m</string><string>novelauto</string><string>daily</string>"
else
  PROG_ARGS="<string>/usr/bin/open</string><string>-a</string><string>Terminal</string><string>$PROJ/scripts/daily_run.command</string>"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJ/daily/inbox" "$PROJ/daily/done" "$PROJ/daily/failed" "$PROJ/daily/logs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <!-- macOS 不允许后台任务直接读取 ~/Documents：
       默认让「终端」来启动（会弹窗口，能看到进度）；--silent 则直接跑 -->
  <key>ProgramArguments</key>
  <array>
    $PROG_ARGS
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>$MIN</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$PROJ/daily/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJ/daily/logs/launchd.err.log</string>
  <key>RunAtLoad</key>
  <false/>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
EOF

if [ ! -s "$PLIST" ]; then
  echo "❌ 写不了 $PLIST（检查磁盘权限）"
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1
if launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null; then
  launchctl enable "gui/$UID/$LABEL" 2>/dev/null
  if [ "$SILENT" = "1" ]; then
    echo "✅ 定时任务已装好（静默模式）：每天 $TIME 自动跑"
    echo "   ⚠️ 静默模式要求已把 .venv/bin/python 加入「完全磁盘访问权限」，否则会静默失败"
  else
    echo "✅ 定时任务已装好：每天 $TIME 自动跑（会弹一个终端窗口显示进度）"
  fi
else
  echo "⚠️  plist 已写入，但 launchctl 加载失败："
  launchctl bootstrap "gui/$UID" "$PLIST" 2>&1 | sed 's/^/     /'
  echo "    可试：launchctl bootstrap gui/$UID \"$PLIST\""
fi
echo "   文件：$PLIST"
echo "   日志：$PROJ/daily/logs/"
echo

echo "要不要顺便设置「每天自动唤醒」（到点把 Mac 从睡眠中叫醒）？"
echo "  需要输入一次你的开机密码（sudo）。"
read -r -p "  设置吗？[y/N] " ans
if [ "${ans:-N}" = "y" ] || [ "${ans:-N}" = "Y" ]; then
  # 比生成时间早 5 分钟唤醒，给网络和系统留时间
  WAKE_HOUR="$HOUR"; WAKE_MIN="$((MIN - 5))"
  if [ "$WAKE_MIN" -lt 0 ]; then
    WAKE_MIN="$((WAKE_MIN + 60))"
    WAKE_HOUR="$(( (WAKE_HOUR + 23) % 24 ))"
  fi
  WAKE_TIME="$(printf '%02d:%02d:00' "$WAKE_HOUR" "$WAKE_MIN")"
  sudo pmset repeat wakeorpoweron MTWRFSU "$WAKE_TIME" && echo "✅ 每天 $WAKE_TIME 自动唤醒已设置"
else
  echo "跳过自动唤醒。可稍后运行「开启自动唤醒.command」。"
fi

echo
echo "测试一下（只看会处理哪些文件，不真的生成）："
echo "  cd \"$PROJ\" && ./.venv/bin/python -m novelauto daily --dry-run"
echo "把原文 .txt 放进：$PROJ/daily/inbox/"
echo
read -n 1 -s -r -p "按任意键关闭…"
