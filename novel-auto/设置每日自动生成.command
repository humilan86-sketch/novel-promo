#!/bin/bash
# 双击：安装「每天 8 点自动生成」定时任务，并可选设置自动唤醒
cd "$(dirname "$0")" || exit 1
exec ./scripts/install_daily.sh
