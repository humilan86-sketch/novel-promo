#!/bin/bash
# 双击：取消「每天 8 点自动生成」定时任务（不动已经生成的成片）
cd "$(dirname "$0")" || exit 1
LABEL="com.huqi.novel-promo.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "==============================================="
echo "  关闭「每天自动生成」"
echo "==============================================="
launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 && echo "✅ 定时任务已停止"
rm -f "$PLIST" && echo "✅ 已删除 $PLIST"

read -r -p "要一起取消「每天自动唤醒」吗？（需要密码）[y/N] " ans
if [ "${ans:-N}" = "y" ] || [ "${ans:-N}" = "Y" ]; then
  sudo pmset repeat cancel && echo "✅ 自动唤醒已取消"
fi

echo
echo "已经生成的成片不受影响，还在 output/ 里。"
read -n 1 -s -r -p "按任意键关闭…"
