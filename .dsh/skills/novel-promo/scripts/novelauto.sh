#!/usr/bin/env bash
# 技能入口：定位 novel-auto 项目并用它的 venv 执行
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if WS="$(cd "$SKILL_DIR/../../.." 2>/dev/null && pwd)"; then :; else WS="$HOME"; fi

for c in "${NOVELAUTO_HOME:-}" \
         "$WS/novel-auto"; do
  [ -n "$c" ] || continue
  if [ -x "$c/.venv/bin/python" ]; then ROOT="$c"; break; fi
done

if [ -z "${ROOT:-}" ]; then
  echo '{"ok":false,"error":"找不到 novel-auto 项目","hint":"设置 NOVELAUTO_HOME=/path/to/novel-auto"}' >&2
  exit 1
fi
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m novelauto "$@"
