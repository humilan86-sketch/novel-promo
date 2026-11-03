"""流水线编排，分两步：

1. prepare()：只做剧本改写，产出 3 个可选「黄金三秒」+ 分段稿 + 发布文案（快，十几秒）
2. render() ：选好开头后，配音 → 分章 → 字幕 → 素材 → 渲染 → 拼接 → 物料 → 质检

这样既能「先看开头再生成」，也能改稿/换开头后只重跑渲染（配音有缓存，很快）。
"""
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import audio as A
from . import i18n
from . import projects
from .assets import AssetLibrary
from .config import Config
from .publish import build_publish_text, sanitize_name
from .qc import run_qc
from .render import concat_chapters, make_cover, render_chapter
from .scriptwriter import ScriptWriter, normalize_for_tts
from .subtitles import make_subtitle_pngs
from .tts import TTS, TTSFailed, rate_pct
from .util import ensure_dir, human_dur, log, media_info, read_json, run, write_json

STAGES = [
    ("script", 0.12, "改写剧本"),
    ("tts", 0.30, "合成配音"),
    ("timeline", 0.40, "对齐时间轴/分章"),
    ("subtitle", 0.45, "生成字幕"),
    ("assets", 0.50, "调度素材"),
    ("render", 0.92, "渲染合成"),
    ("publish", 0.98, "生成发布物料"),
]


# ---------------------------------------------------------------- 工具函数

def _pause_for(text: str, role: str) -> float:
    t = text.rstrip()
    if role == "bridge":
        return 0.45
    if t.endswith(("…", "……")):
        return 0.38
    if t.endswith(("！", "？")):
        return 0.30
    if t.endswith(("。", ".", "!", "?")):
        return 0.26
    if t.endswith(("，", "、", ",", "；", "；")):
        return 0.14
    return 0.16


def _truncate_source(text: str, max_chars: int) -> str:
    """超长原文按目标字数截断，尽量停在段落/句子边界。"""
    han = 0
    for i, ch in enumerate(text):
        if "\u4e00" <= ch <= "\u9fff":
            han += 1
        if han >= max_chars:
            cut = max(text.rfind("\n", 0, i), text.rfind("。", 0, i),
                      text.rfind("！", 0, i), text.rfind("？", 0, i))
            return text[: cut + 1 if cut > max_chars * 0.6 else i]
    return text


def _force_trim(lines: List[str], max_units: int, keep: set, lang: str = "zh") -> List[str]:
    """按预算强制删句：保留开头钩子、结尾断点和桥接句，中段均匀抽掉。"""
    total = i18n.count_units(" ".join(lines), lang)
    if total <= max_units:
        return lines
    keep_idx = set()
    for i, l in enumerate(lines):
        if i < 3 or i >= len(lines) - 2 or l in keep:
            keep_idx.add(i)
    drop_pool = [i for i in range(len(lines)) if i not in keep_idx]
    if not drop_pool:
        return lines
    need = total - max_units
    dropped = 0
    step = max(1, len(drop_pool) // max(1, need // 8 + 1))
    drop_set = set()
    for i in drop_pool[::step]:
        if dropped >= need:
            break
        drop_set.add(i)
        dropped += i18n.count_units(lines[i], lang)
    return [l for i, l in enumerate(lines) if i not in drop_set]


def measure_lufs(path: Path) -> Optional[float]:
    try:
        p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-af", "ebur128=framelog=quiet", "-f", "null", "-"], check=False)
        m = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", p.stderr.decode("utf-8", "ignore"))
        return float(m[-1]) if m else None
    except Exception:
        return None


def _timings(marks: Dict) -> Dict:
    keys = ["t0", "tts_done", "render_done", "concat_done"]
    out = {}
    prev = None
    for k in keys:
        if k not in marks:
            continue
        if prev is not None:
            out[k.replace("_done", "")] = round(marks[k] - prev, 1)
        prev = marks[k]
    out["total"] = round(time.time() - marks["t0"], 1)
    return out


def _title_of(script: Dict) -> str:
    pub = script.get("publish") or {}
    titles = [t for t in (pub.get("title_candidates") or []) if str(t).strip()]
    return str(pub.get("title") or (titles[0] if titles else ""))


def _clean(v) -> str:
    return "" if v is None else str(v).strip()


# ---------------------------------------------------------------- 主类

class Pipeline:
    def __init__(self, cfg: Config, progress: Optional[Callable] = None):
        self.cfg = cfg
        self.progress_cb = progress
        self.job_id = ""
        self.work = None  # type: Optional[Path]
        self.out = None   # type: Optional[Path]

    # ------------------------------------------------------------ 进度
    def _progress(self, stage: str, pct: float, msg: str = "") -> None:
        if self.progress_cb:
            try:
                self.progress_cb(stage, pct, msg)
            except Exception:
                pass

    def _stage_dir(self, name: str) -> Path:
        return ensure_dir(self.work / name)

    # ------------------------------------------------------------ 第一步：剧本
    def prepare(self, source_text: str, target_minutes: Optional[float] = None,
                title: str = "", job_id: str = "", force: bool = False,
                style: str = "", person: str = "", hook_strength: str = "") -> Dict:
        cfg = self.cfg
        if style:
            cfg.set("script.style", style)
        if person:
            cfg.set("script.person", person)
        if hook_strength:
            cfg.set("script.hook_strength", hook_strength)
        minutes_min = float(cfg.get("target.minutes_min"))
        minutes_max = float(cfg.get("target.minutes_max"))
        lang = i18n.lang_of(cfg)
        ups = float(i18n.LANGS[lang]["units_per_sec"])
        unit_name = i18n.LANGS[lang]["unit_name"]

        source_text = source_text.strip()
        units = i18n.count_units(source_text, lang)
        min_units = int(minutes_min * 60 * ups * 0.82)
        if units < min_units:
            raise ValueError(
                "原文太短：只有 %d %s，%d 分钟成片至少需要约 %d %s。"
                "请补原文，或把 target.minutes_min 调低。" % (
                    units, unit_name, int(minutes_min), min_units, unit_name))

        est_min = units / ups / 60.0
        target_min = float(target_minutes) if target_minutes else max(minutes_min, min(minutes_max, est_min))
        target_min = max(minutes_min, min(minutes_max, target_min))
        target_sec = target_min * 60.0

        max_units = int(target_sec * ups * 1.2)
        if units > max_units:
            source_text = i18n.truncate(source_text, max_units, lang)
            log("[warn] 原文 %d %s，超出 %d 分钟需要的量，已截断到约 %d %s"
                  % (units, unit_name, int(target_min), max_units, unit_name))

        if not job_id:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            job_id = "%s_%s" % (stamp, sanitize_name(title or source_text[:12]))
        self.job_id = job_id
        self.work = ensure_dir(cfg.path("project.work_dir") / job_id)
        script_path = self.work / "script.json"

        if script_path.exists() and not force and read_json(script_path):
            script = read_json(script_path)
            self._progress("script", 0.12, "复用已有剧本")
        else:
            self._progress("script", 0.02, "长文切块改写中…")
            writer = ScriptWriter(cfg)
            script = writer.rewrite(source_text, target_sec)
            script["meta"] = {
                "job_id": job_id,
                "source_chars": len(source_text),
                "target_sec": round(target_sec, 1),
                "degraded": bool(script.get("degraded")),
                "style": str(cfg.get("script.style")),
                "person": str(cfg.get("script.person")),
                "hook_strength": str(cfg.get("script.hook_strength")),
                "language": lang,
                "rate": str(cfg.get("tts.rate")),
                "voice": str(cfg.get("tts.voice")),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            write_json(script_path, script)
            self._progress("script", 0.12,
                           "剧本完成（%d 句%s）" % (len(script["segments"]),
                                                   "，离线模式" if script["meta"]["degraded"] else ""))

        hooks = script.get("hooks") or [{"type": "默认", "text": script["segments"][0]["text"]}]
        total_units = i18n.count_units(" ".join(x.get("text", "") for x in script.get("segments", [])), lang)
        prev = projects.load(cfg, job_id) or {}
        keep_done = prev.get("status") == "done"
        projects.update(
            cfg, job_id,
            title=title or _title_of(script),
            status="done" if keep_done else "prepared",
            language=lang,
            style=str(cfg.get("script.style")),
            person=str(cfg.get("script.person")),
            hook_strength=str(cfg.get("script.hook_strength")),
            target_sec=round(target_sec, 1),
            hooks=hooks,
            segments=script.get("segments", []),
            publish=script.get("publish", {}),
            degraded=bool((script.get("meta") or {}).get("degraded")),
            rate=str(cfg.get("tts.rate")),
            voice=str(cfg.get("tts.voice")),
            created=(script.get("meta") or {}).get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return {
            "job_id": job_id,
            "work": str(self.work),
            "hooks": hooks,
            "segments": script.get("segments", []),
            "publish": script.get("publish", {}),
            "meta": script.get("meta", {}),
            "target_sec": target_sec,
            "language": lang,
            "units": total_units,
            "unit_name": i18n.LANGS[lang]["unit_name"],
        }

    # ------------------------------------------------------------ 第二步：渲染
    def render(self, job_id: str, hook_index: Optional[int] = None,
               hook_text: Optional[str] = None,
               segments: Optional[List] = None,
               out_dir: Optional[Path] = None, force: bool = False) -> Dict:
        t0 = time.time()
        marks = {"t0": t0}

        def mark(name: str) -> None:
            marks[name] = time.time()

        cfg = self.cfg
        self.job_id = job_id
        self.work = ensure_dir(cfg.path("project.work_dir") / job_id)
        self.out = ensure_dir(Path(out_dir) if out_dir else cfg.path("project.out_dir") / job_id)
        script = read_json(self.work / "script.json")
        if not script:
            raise ValueError("找不到剧本，请先执行「解析原文」")

        lang = i18n.lang_of(cfg)
        ups = float(i18n.LANGS[lang]["units_per_sec"])
        target_sec = float((script.get("meta") or {}).get("target_sec") or 360)
        minutes_min = float(cfg.get("target.minutes_min"))
        minutes_max = float(cfg.get("target.minutes_max"))
        tolerance = float(cfg.get("target.tolerance"))

        # --- 应用用户改动的开头 / 分段 ---
        raw_segments = self._apply_user_edits(script, hook_index, hook_text, segments)
        writer = ScriptWriter(cfg, lang)
        bridges = [str(b) for b in (script.get("bridges") or []) if str(b).strip()]
        segs = self._to_segments(raw_segments, bridges)

        # --- 配音 + 时长收敛 ---
        tts = self._make_tts()
        max_rounds = int(cfg.get("target.max_rounds"))
        master, timeline = None, []
        rnd = 0
        for rnd in range(max_rounds):
            self._progress("tts", 0.13, "合成配音（第 %d 轮）…" % (rnd + 1))
            files = self._synthesize(tts, segs,
                                     lambda i, n: self._progress(
                                         "tts", 0.13 + 0.17 * i / max(1, n),
                                         "配音 %d/%d" % (i, n)))
            master, timeline = A.build_master(files, segs)
            if not timeline:
                raise RuntimeError("配音时间轴为空：所有句子都合成失败")
            total = timeline[-1]["end"]
            lo = max(minutes_min * 60 * (1 - tolerance), target_sec * (1 - tolerance))
            hi = min(minutes_max * 60 * 1.01, target_sec * (1 + tolerance))
            if lo <= total <= hi or rnd == max_rounds - 1:
                if total < lo:
                    log("[warn] 实际时长 %.1fs 低于目标 %.1fs，离线模式无法扩写" % (total, lo))
                break
            ratio = target_sec / max(1.0, total)
            self._progress("tts", 0.30,
                           "时长 %.1fs 偏离目标，调整到 %.2f 倍后重合成" % (total, ratio))
            old_lines = [s["display_text"] for s in segs]
            old_units = i18n.count_units(" ".join(old_lines), lang)
            new_lines = writer.adjust(old_lines, ratio, bridges)
            new_units = i18n.count_units(" ".join(new_lines), lang)
            if ratio > 1 and new_units < old_units * 1.05:
                # 模型没按比例扩写，加码再要一次
                new_lines = writer.adjust(old_lines, min(1.8, ratio * 1.3), bridges)
                new_units = i18n.count_units(" ".join(new_lines), lang)
            budget_units = int(target_sec * ups * 1.02)
            if i18n.count_units(" ".join(new_lines), lang) > budget_units:
                new_lines = _force_trim(new_lines, budget_units,
                                        set(bridges) | set(i18n.bridges(lang)), lang)
            if not new_lines or new_lines == old_lines:
                break
            segs = self._to_segments([{"text": t, "role": self._role_at(i, len(new_lines))}
                                      for i, t in enumerate(new_lines)], bridges)

        if not timeline or master is None:
            raise RuntimeError("配音阶段没有产出任何音频（检查 TTS 配置/网络）")
        total = timeline[-1]["end"]
        A.write_wav(self.work / "master.wav", master)
        mark("tts_done")

        # --- 分章 ---
        self._progress("timeline", 0.31, "按音频时间轴分章")
        chapters = A.split_chapters(
            timeline,
            cmin=float(cfg.get("pipeline.chapter_min_sec")),
            cmax=float(cfg.get("pipeline.chapter_max_sec")),
        )
        if not chapters:
            raise RuntimeError("分章失败：时间轴为空")
        write_json(self.work / "timeline.json", {"total": round(total, 3), "chapters": chapters})

        adjusted_script = {
            "meta": dict(script.get("meta", {}), actual_duration_sec=round(total, 3),
                         rounds=rnd + 1, target_sec=round(target_sec, 1)),
            "segments": [{"text": s["display_text"], "role": s["role"]} for s in segs],
            "publish": script.get("publish", {}),
            "hooks": script.get("hooks", []),
            "bridges": bridges,
        }
        write_json(self.work / "script.json", adjusted_script)

        # --- 字幕 + 素材 + 渲染 ---
        lib = AssetLibrary(cfg)
        chapter_files: List[Path] = []
        clip_plan: List[Dict] = []
        try:
            for ci, ch in enumerate(chapters):
                ci_dir = ensure_dir(self._stage_dir("render") / ("ch%02d" % ch["idx"]))
                subs = make_subtitle_pngs(ch["segments"], cfg, ci_dir)
                self._progress("subtitle", 0.32 + 0.03 * ci / len(chapters),
                               "字幕 %d/%d" % (ci + 1, len(chapters)))
                wav = A.slice_wav(master, ch["start"], ch["end"], ci_dir / "audio.wav")
                clips = lib.pick([ch["duration"]], prefer_category="")
                clip_plan.append({"chapter": ch["idx"], "duration": ch["duration"], "clips": clips})
                self._progress("assets", 0.33 + 0.02 * ci / len(chapters),
                               "素材 %d/%d" % (ci + 1, len(chapters)))
                out = render_chapter(cfg, clips, wav, subs, ci_dir / "chapter.mp4", ch["duration"])
                chapter_files.append(out)
                self._progress("render", 0.35 + 0.55 * (ci + 1) / len(chapters),
                               "渲染章节 %d/%d（%s）" % (ci + 1, len(chapters), human_dur(ch["duration"])))
            lib.mark_used([c for ch in clip_plan for c in ch["clips"]])
        finally:
            lib.close()
        mark("render_done")

        # --- 拼接 ---
        self._progress("render", 0.92, "拼接成片")
        chapter_total = sum(float(ch["duration"]) for ch in chapters)
        final = concat_chapters(chapter_files, self.out / "final.mp4", chapter_total, cfg)
        mark("concat_done")

        # --- 物料 ---
        self._progress("publish", 0.94, "生成封面与发布文案")
        cover_text = str((script.get("publish") or {}).get("cover_text") or "")
        cover_lines = ([l for l in re.split(r"\\n|\n", cover_text) if l.strip()]
                       or i18n.OFFLINE_PUBLISH.get(lang, i18n.OFFLINE_PUBLISH["en"])["cover_default"])
        try:
            first_clip = clip_plan[0]["clips"][0] if clip_plan and clip_plan[0]["clips"] else None
            if first_clip:
                make_cover(cfg, Path(first_clip["path"]), float(first_clip["start"]) + 1.0,
                           cover_lines, self.out / "cover.jpg",
                           tip=str(i18n.LANGS[lang]["cover_tip"]))
            else:
                make_cover(cfg, final, 2.0, cover_lines, self.out / "cover.jpg",
                           tip=str(i18n.LANGS[lang]["cover_tip"]))
        except Exception as e:  # noqa: BLE001
            log("[warn] 封面生成失败（不影响成片）: %s" % e)

        info = media_info(final)
        lufs = measure_lufs(final)
        meta = {
            "job_id": job_id, "title": _title_of(script),
            "duration_hms": human_dur(info["duration"]),
            "duration_sec": round(info["duration"], 2),
            "width": info["width"], "height": info["height"],
            "fps": round(info["fps"], 2),
            "size_mb": round((self.out / "final.mp4").stat().st_size / 1048576, 1),
            "lufs": lufs,
            "degraded": bool((script.get("meta") or {}).get("degraded")),
            "chapters": len(chapters),
            "chars": sum(len(s["display_text"]) for s in segs),
            "voice": str(cfg.get("tts.voice")),
            "rate": str(cfg.get("tts.rate")),
            "language": lang,
            "elapsed_sec": round(time.time() - t0, 1),
            "timings": _timings(marks),
        }
        qc = run_qc(cfg, final, total, timeline, chapters, clip_plan,
                    adjusted_script, meta, self.out)
        meta["qc_status"] = qc["status"]

        projects.update(
            cfg, job_id,
            status="done",
            voice=str(cfg.get("tts.voice")),
            rate=str(cfg.get("tts.rate")),
            language=lang,
            out_dir=str(self.out),
            duration_sec=meta["duration_sec"],
            duration_hms=meta["duration_hms"],
            size_mb=meta["size_mb"],
            lufs=meta["lufs"],
            qc=qc["status"],
            chapters=meta["chapters"],
            segments=[{"text": s["display_text"], "role": s["role"]} for s in segs],
            hooks=script.get("hooks", []),
            publish=adjusted_script.get("publish", {}),
        )
        (self.out / "publish.txt").write_text(
            build_publish_text(adjusted_script, meta, cfg), encoding="utf-8")
        write_json(self.out / "meta.json", meta)
        write_json(self.work / "clip_plan.json", clip_plan)
        write_json(self.out / "script.json", adjusted_script)
        self._progress("publish", 1.0, "完成")
        return {"job_id": job_id, "out_dir": str(self.out), "final": str(final),
                "hooks": script.get("hooks", []), "qc": qc, **meta}

    # ------------------------------------------------------------ 一步跑完（兼容旧用法）
    def run(self, source_text: str, target_minutes: Optional[float] = None,
            title: str = "", job_id: str = "", hook_index: int = 0,
            out_dir: Optional[Path] = None, force: bool = False) -> Dict:
        prep = self.prepare(source_text, target_minutes, title, job_id, force=force)
        return self.render(prep["job_id"], hook_index=hook_index, out_dir=out_dir)

    # ------------------------------------------------------------ 辅助
    def _apply_user_edits(self, script: Dict, hook_index: Optional[int],
                          hook_text: Optional[str],
                          segments: Optional[List]) -> List[Dict]:
        raw = [dict(s) for s in (script.get("segments") or [])]
        if not raw:
            raise ValueError("剧本分段为空")
        if segments:
            new = []
            for s in segments:
                if isinstance(s, str):
                    if s.strip():
                        new.append({"text": s.strip(), "role": "body"})
                elif isinstance(s, dict) and str(s.get("text", "")).strip():
                    new.append({"text": str(s["text"]).strip(), "role": s.get("role", "body")})
            if new:
                new[0]["role"] = "hook"
                new[-1]["role"] = "ending"
                raw = new
        hooks = script.get("hooks") or []
        chosen = _clean(hook_text)
        if not chosen and hooks and hook_index is not None:
            idx = max(0, min(len(hooks) - 1, int(hook_index)))
            chosen = str(hooks[idx].get("text") or "")
        if chosen:
            raw[0]["text"] = chosen
            raw[0]["role"] = "hook"
        return raw

    def _synthesize(self, tts, segs: List[Dict], progress=None) -> List[Path]:
        """配音；edge 失败自动降级到 macOS say，保证不断产。"""
        lines = [s["text"] for s in segs]
        try:
            return tts.synth_all(lines, progress=progress)
        except TTSFailed as e:
            failed = dict(e.failed)
            log("[warn] %d 句配音失败（%s），降级 macOS say 兜底"
                  % (len(failed), list(failed.values())[0] if failed else ""))
            fallback = TTS(self._cfg_with("tts.provider", "say"))
            out: List[Optional[Path]] = [None] * len(lines)
            for i in range(len(lines)):
                out[i] = fallback.synth_one(lines[i]) if i in failed else tts.synth_one(lines[i])
            return [p for p in out if p is not None]

    def _cfg_with(self, key: str, value) -> Config:
        import copy
        raw = copy.deepcopy(self.cfg.raw)
        parts = key.split(".")
        cur = raw
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
        return Config(raw)

    def _make_tts(self) -> TTS:
        try:
            return TTS(self.cfg)
        except Exception as e:  # noqa: BLE001
            log("[warn] TTS 初始化失败，改用 macOS say: %s" % e)
            return TTS(self._cfg_with("tts.provider", "say"))

    def _to_segments(self, raw: List[Dict], bridges: List[str] = None) -> List[Dict]:
        lang = i18n.lang_of(self.cfg)
        known_bridges = set(bridges or []) | set(i18n.bridges(lang))
        out = []
        for s in raw:
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            role = str(s.get("role", "body"))
            if text in known_bridges:
                role = "bridge"
            out.append({
                "display_text": text,
                "text": normalize_for_tts(text, lang),
                "role": role,
                "pause_after": _pause_for(text, role),
            })
        return out

    @staticmethod
    def _role_at(i: int, n: int) -> str:
        if i == 0:
            return "hook"
        if i == n - 1:
            return "ending"
        return "body"
