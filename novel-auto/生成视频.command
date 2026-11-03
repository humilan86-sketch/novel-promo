#!/bin/bash
# 双击这个文件生成视频；也可以把 .txt 文件直接拖到这个图标上。
cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo "  小说推文自动化 · 生成一条视频"
echo "==============================================="
echo

if [ ! -x ".venv/bin/python" ]; then
  echo "❌ 还没装好运行环境，先双击「启动控制台.command」看提示。"
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

FILE="$1"
if [ -z "$FILE" ]; then
  FILE=$(osascript -e 'POSIX path of (choose file with prompt "选择小说原文（txt）")' 2>/dev/null)
  if [ -z "$FILE" ]; then
    echo "没有选择文件，退出。"
    read -n 1 -s -r -p "按任意键退出…"
    exit 0
  fi
fi

if [ ! -f "$FILE" ]; then
  echo "❌ 文件不存在：$FILE"
  read -n 1 -s -r -p "按任意键退出…"
  exit 1
fi

echo "原文：$FILE"
echo
read -r -p "目标时长（分钟，5-8，直接回车默认 6）： " MINUTES
[ -z "$MINUTES" ] && MINUTES=6
read -r -p "项目名（直接回车用书名/自动命名）： " TITLE

if [ -n "$TITLE" ]; then
  ./.venv/bin/python -m novelauto run --input "$FILE" --minutes "$MINUTES" --title "$TITLE"
else
  ./.venv/bin/python -m novelauto run --input "$FILE" --minutes "$MINUTES"
fi

STATUS=$?
echo
if [ $STATUS -eq 0 ]; then
  echo "✅ 完成，正在打开成品目录…"
  open output/
else
  echo "❌ 生成失败（退出码 ${STATUS}），往上翻看报错信息。"
fi

echo
read -n 1 -s -r -p "按任意键关闭窗口…"
