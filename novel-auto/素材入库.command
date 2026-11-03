#!/bin/bash
# 双击这个文件：把素材文件夹里的视频扫描入库（建索引、算指纹、去重）
cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo "  小说推文自动化 · 素材入库"
echo "==============================================="
echo

if [ ! -x ".venv/bin/python" ]; then
  echo "❌ 还没装好运行环境，先双击「启动控制台.command」看提示。"
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

DIR="$1"
if [ -z "$DIR" ]; then
  DIR=$(osascript -e 'POSIX path of (choose folder with prompt "选择素材文件夹（默认 assets/clips）")' 2>/dev/null)
fi
[ -z "$DIR" ] && DIR="assets/clips"

echo "扫描目录：$DIR"
echo

./.venv/bin/python -m novelauto ingest --dir "$DIR"

echo
./.venv/bin/python -m novelauto assets stats
echo
read -n 1 -s -r -p "按任意键关闭窗口…"
