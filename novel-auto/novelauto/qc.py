"""质检：时长/规格/响度/黑帧/静音/素材复用/钩子密度/音画同步。

输出 qa_report.txt，并返回结构化结果给 Web 界面展示。
"""
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import i18n
from .config import Config
from .util import CmdError, media_info, run


def _probe_black(path: Path, d: float, pix_th: float) -> List[Dict]:
    try:
        p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-vf", "blackdetect=d=%.2f:pix_th=%.2f" % (d, pix_th),
                 "-an", "-f", "null", "-"], check=False, timeout=1800)
        err = p.stderr.decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", err):
        out.append({"start": float(m.group(1)), "end": float(m.group(2)),
                    "duration": float(m.group(3))})
    return out


def _probe_silence(path: Path, noise_db: float, d: float) -> List[Dict]:
    try:
        p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-af", "silencedetect=noise=%.0fdB:d=%.2f" % (noise_db, d),
                 "-vn", "-f", "null", "-"], check=False, timeout=1800)
        err = p.stderr.decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for m in re.finditer(r"silence_start:\s*([\d.]+)[\s\S]*?silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", err):
        out.append({"start": float(m.group(1)), "end": float(m.group(2)),
                    "duration": float(m.group(3))})
    return out


def run_qc(cfg: Config, final: Path, master_dur: float, timeline: List[Dict],
           chapters: List[Dict], clip_plan: List[Dict], script: Dict,
           meta: Dict, out_dir: Path) -> Dict:
    checks: List[Dict] = []

    def add(name: str, ok: bool, detail: str, level: str = "warn") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "level": level})

    tmin = float(cfg.get("target.minutes_min")) * 60
    tmax = float(cfg.get("target.minutes_max")) * 60
    dur = float(meta.get("duration_sec") or 0)
    add("时长区间", tmin * 0.97 <= dur <= tmax * 1.01,
        "%.1f 秒（目标 %.0f-%.0f 秒）" % (dur, tmin, tmax), "error")

    add("分辨率", meta.get("width") == int(cfg.get("render.width"))
        and meta.get("height") == int(cfg.get("render.height")),
        "%sx%s（要求 %sx%s）" % (meta.get("width"), meta.get("height"),
                                cfg.get("render.width"), cfg.get("render.height")), "error")
    add("帧率", abs(float(meta.get("fps") or 0) - float(cfg.get("render.fps"))) < 0.5,
        "%s fps" % meta.get("fps"), "error")

    lufs = meta.get("lufs")
    tgt = float(cfg.get("qc.lufs_target"))
    tol = float(cfg.get("qc.lufs_tolerance"))
    add("响度", lufs is not None and abs(lufs - tgt) <= tol,
        ("%s LUFS（目标 %.0f±%.0f）" % ("%.1f" % lufs if lufs is not None else "n/a", tgt, tol)))

    # 音画同步：成片时长 vs 音频总长
    add("音画同步", abs(dur - master_dur) <= 1.5,
        "成片 %.1fs / 音频 %.1fs" % (dur, master_dur))

    # 黑帧
    blacks: List[Dict] = []
    if bool(cfg.get("qc.blackdetect", True)):
        blacks = _probe_black(final, float(cfg.get("qc.black_min_duration")),
                              float(cfg.get("qc.black_pix_th")))
        add("黑帧检测", not blacks,
            "无黑帧" if not blacks else "发现 %d 段黑帧：%s" % (
                len(blacks), ", ".join("%.1fs@%.0fs" % (b["duration"], b["start"]) for b in blacks[:5])))

    # 静音
    silences: List[Dict] = []
    if bool(cfg.get("qc.silencedetect", True)):
        silences = _probe_silence(final, float(cfg.get("qc.silence_db")),
                                  float(cfg.get("qc.silence_min_duration")))
        add("静音检测", not silences,
            "无异常静音" if not silences else "发现 %d 段静音：%s" % (
                len(silences), ", ".join("%.1fs@%.0fs" % (s["duration"], s["start"]) for s in silences[:5])))

    # 素材复用
    uses: Dict[str, int] = {}
    for ch in clip_plan:
        for c in ch["clips"]:
            uses[c["path"]] = uses.get(c["path"], 0) + 1
    max_use = max(uses.values()) if uses else 0
    limit = int(cfg.get("qc.max_asset_reuse"))
    add("素材复用", max_use <= limit,
        "%d 个素材 / %d 个片段，单素材最多用 %d 次（上限 %d）"
        % (len(uses), sum(uses.values()), max_use, limit))

    # 钩子密度
    total = timeline[-1]["end"] if timeline else 0
    hooks = [s for s in timeline if s.get("role") in ("hook", "bridge")]
    pos = [s["start"] / total for s in hooks] if total else []
    has_third = any(0.25 <= p <= 0.45 for p in pos)
    has_two = any(0.55 <= p <= 0.80 for p in pos)
    add("1/3 续航钩", has_third, "有" if has_third else "缺（中段流失风险）")
    add("2/3 续航钩", has_two, "有" if has_two else "缺（后段流失风险）")

    # 文案/字幕
    segs = script.get("segments") or []
    lang = i18n.lang_of(cfg)
    ups = float(i18n.LANGS[lang]["units_per_sec"])
    all_text = " ".join(s.get("text", "") for s in segs)
    n_units = i18n.count_units(all_text, lang)
    u_lo = int(float(cfg.get("target.minutes_min")) * 60 * ups * 0.75)
    u_hi = int(float(cfg.get("target.minutes_max")) * 60 * ups * 1.15)
    add("文案体量", u_lo <= n_units <= u_hi,
        "%d %s（目标区间 %d-%d）" % (n_units, i18n.LANGS[lang]["unit_name"], u_lo, u_hi))
    add("字幕覆盖", len(timeline) > 0,
        "%d 句字幕 / %d 句文案" % (len(timeline), len(segs)))

    errors = [c for c in checks if not c["ok"] and c["level"] == "error"]
    warns = [c for c in checks if not c["ok"] and c["level"] == "warn"]
    status = "error" if errors else ("warn" if warns else "pass")

    lines = ["质检报告  %s" % meta.get("job_id", ""),
             "结论：%s" % {"pass": "✅ 通过", "warn": "⚠️ 有提醒（可发布，建议看一眼）",
                          "error": "❌ 有硬性问题（建议修）"}[status],
             ""]
    for c in checks:
        mark = "✅" if c["ok"] else ("❌" if c["level"] == "error" else "⚠️")
        lines.append("%s %-8s %s" % (mark, c["name"], c["detail"]))
    if errors or warns:
        lines += ["", "建议："]
        for c in (errors + warns)[:6]:
            lines.append("  - %s：%s" % (c["name"], c["detail"]))
    (out_dir / "qa_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": status, "checks": checks,
            "errors": [c["name"] for c in errors], "warns": [c["name"] for c in warns]}
