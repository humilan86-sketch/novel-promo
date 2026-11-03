"""发布物料：标题、话题、置顶评论、发布清单、文件名规范。"""
import re
from pathlib import Path
from typing import Dict, List


def build_publish_text(script: Dict, meta: Dict, cfg) -> str:
    pub = script.get("publish") or {}
    titles: List[str] = [t for t in (pub.get("title_candidates") or []) if str(t).strip()]
    title = str(pub.get("title") or (titles[0] if titles else meta.get("title", "")))
    hashtags = [str(h) for h in (pub.get("hashtags") or [])]
    pinned = str(pub.get("pinned_comment") or "全文整理好了，书名放在评论区置顶。")
    cover = str(pub.get("cover_text") or "")
    series = pub.get("series") or {}

    lines = []
    lines.append("=" * 46)
    lines.append("发布物料  |  %s" % meta.get("job_id", ""))
    lines.append("成品：final.mp4   封面：cover.jpg")
    lines.append("时长：%s   分辨率：%sx%s" % (meta.get("duration_hms", ""),
                                            meta.get("width", 1080), meta.get("height", 1920)))
    if meta.get("degraded"):
        lines.append("⚠️ 本次为离线改写（未接入大模型），文案质量仅供流程验证")
    lines.append("=" * 46)
    lines.append("")
    lines.append("【标题】")
    for i, t in enumerate(titles[:3], 1):
        lines.append("  %d. %s" % (i, t))
    lines.append("  采用：%s" % title)
    lines.append("")
    lines.append("【话题标签】")
    lines.append("  " + " ".join(hashtags))
    lines.append("")
    lines.append("【置顶评论】")
    lines.append("  " + pinned)
    lines.append("")
    if cover:
        lines.append("【封面文案】")
        for l in re.split(r"\\n|\n", cover):
            lines.append("  " + l)
        lines.append("")
    if series:
        lines.append("【集数】第 %s 集" % series.get("part", 1))
        if series.get("next_hint"):
            lines.append("  下集预告：" + str(series.get("next_hint")))
        lines.append("")
    lines.append("【上传清单】")
    lines.append("  1) 打开 TikTok → 上传 → 选 final.mp4")
    lines.append("  2) 标题栏粘贴「采用」那条，末尾加话题标签")
    lines.append("  3) 封面选 cover.jpg（或从视频里挑）")
    lines.append("  4) 发布后第一条评论粘贴「置顶评论」并置顶")
    lines.append("  5) 若为 AI 配音/画面，请按平台要求勾选 AIGC 标注")
    lines.append("")
    lines.append("【质检提醒】")
    lines.append("  - 前 3 秒是否有「想继续看」的冲动？没有就改 hook 重跑（脚本可单独改）")
    lines.append("  - 素材是否与近期作品撞车？见 qa_report 中的素材使用记录")
    return "\n".join(lines) + "\n"


def sanitize_name(name: str) -> str:
    bad = '\\/:*?"<>|'
    for ch in bad:
        name = name.replace(ch, "")
    return (name or "untitled").strip()[:40]
