#!/bin/bash
# 双击：让 Mac 每天 07:55 自动从睡眠中醒来，8 点自动任务才能准时跑
set -uo pipefail
cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo "  设置每天自动唤醒"
echo "==============================================="
echo
echo "会把「每天 07:55 唤醒」写进系统电源计划（需要输入一次开机密码）。"
echo "注意：只能唤醒「睡眠」状态；关机状态无法自动开机。"
echo
read -r -p "继续？[y/N] " ans
if [ "${ans:-N}" != "y" ] && [ "${ans:-N}" != "Y" ]; then
  echo "已取消。"; read -n 1 -s -r -p "按任意键关闭…"; exit 0
fi
sudo pmset repeat wakeorpoweron MTWRFSU 07:55:00 && echo "✅ 已设置每天 07:55 自动唤醒"
echo
echo "查看当前电源计划：pmset -g sched"
read -n 1 -s -r -p "按任意键关闭…"
