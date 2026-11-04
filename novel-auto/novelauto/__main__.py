"""命令行入口。

用法：
  python -m novelauto run --input 原文.txt --minutes 6
  python -m novelauto run --text "小说原文……"
  python -m novelauto web --port 8765
  python -m novelauto ingest --dir assets/clips
  python -m novelauto assets synth --count 3 --seconds 90
  python -m novelauto assets stats
  python -m novelauto doctor
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from . import i18n
from .config import ROOT, load


def _apply_rate(cfg, rate: str) -> None:
    if rate:
        cfg.set("tts.rate", rate if rate.endswith("%") else (rate + "%"))


def _apply_lang(cfg, language: str) -> None:
    if not language:
        return
    cfg.set("project.language", language)
    cfg.set("project.font", "")
    cfg.set("project.font_path", "")
    cfg.set("tts.voice", "")
    cfg.set("subtitle.max_chars_per_line", 0)
    i18n.apply(cfg)


def _emit_json(obj) -> None:
    import json
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _progress_for(args):
    if getattr(args, "json", False):
        return None
    return _print_progress


def _print_progress(stage: str, pct: float, msg: str) -> None:
    bar_len = 24
    filled = int(bar_len * pct)
    bar = "█" * filled + "·" * (bar_len - filled)
    sys.stdout.write("\r[%s] %3d%%  %-46s" % (bar, int(pct * 100), msg[:46]))
    sys.stdout.flush()
    if pct >= 1.0:
        sys.stdout.write("\n")


def cmd_run(args) -> int:
    cfg = load()
    _apply_lang(cfg, getattr(args, "language", ""))
    _apply_rate(cfg, getattr(args, "rate", ""))
    if args.offline:
        cfg.set("pipeline.offline", True)
    if args.voice:
        cfg.set("tts.voice", args.voice)
    if args.provider:
        cfg.set("tts.provider", args.provider)
    if args.out_dir:
        cfg.set("project.out_dir", args.out_dir)

    if args.input:
        source = Path(args.input).read_text(encoding="utf-8")
    elif args.text:
        source = args.text
    else:
        print("请用 --input 指定原文文件，或 --text 直接给文本")
        return 2

    from .pipeline import Pipeline
    pipe = Pipeline(cfg, progress=_progress_for(args))
    hook_index = max(0, (args.hook or 1) - 1)
    prep = pipe.prepare(source, args.minutes, args.title or "", args.job_id or "",
                        force=args.force, style=args.style or "",
                        person=args.person or "", hook_strength=args.hook_strength or "")
    res = pipe.render(prep["job_id"], hook_index=hook_index,
                      out_dir=Path(args.out_dir) if args.out_dir else None)
    if getattr(args, "json", False):
        _emit_json({"ok": True, **{k: v for k, v in res.items() if k != "qc"},
                    "qc": res.get("qc")})
        return 0
    print("\n✅ 完成：%s" % res["out_dir"])
    print("   成片  : final.mp4（%s，%sx%s）" % (res["duration_hms"], res["width"], res["height"]))
    print("   封面  : cover.jpg")
    print("   文案  : publish.txt")
    print("   质检  : qa_report.txt（%s）" % {"pass": "通过", "warn": "有提醒", "error": "有硬性问题"}.get(
        res.get("qc", {}).get("status", ""), "—"))
    if res.get("degraded"):
        print("   ⚠️ 本次为离线改写（未配置 LLM_API_KEY），文案质量仅供流程验证")
    return 0


def cmd_prepare(args) -> int:
    cfg = load()
    _apply_lang(cfg, getattr(args, "language", ""))
    _apply_rate(cfg, getattr(args, "rate", ""))
    if args.offline:
        cfg.set("pipeline.offline", True)
    if args.input:
        source = Path(args.input).read_text(encoding="utf-8")
    else:
        source = args.text or ""
    from .pipeline import Pipeline
    prep = Pipeline(cfg, progress=_progress_for(args)).prepare(
        source, target_minutes=args.minutes, title=args.title or "",
        job_id=args.job_id or "", force=args.force,
        style=args.style or "", person=args.person or "",
        hook_strength=args.hook_strength or "")
    if getattr(args, "json", False):
        _emit_json({"ok": True, **prep})
        return 0
    print("\n✅ 解析完成  job_id=%s" % prep["job_id"])
    print("   成片目标：%.0f 秒   分段：%d 句   %d %s" % (
        prep["target_sec"], len(prep["segments"]),
        prep.get("units", 0), prep.get("unit_name", "字")))
    print("\n   3 个黄金三秒（用 --hook 1/2/3 选择）：")
    for i, h in enumerate(prep["hooks"], 1):
        print("     %d. [%s] %s" % (i, h.get("type", ""), h.get("text", "")))
    print("\n   下一步：python -m novelauto render --job-id %s --hook 1" % prep["job_id"])
    return 0


def cmd_render(args) -> int:
    cfg = load()
    _apply_lang(cfg, getattr(args, "language", ""))
    _apply_rate(cfg, getattr(args, "rate", ""))
    if getattr(args, "voice", ""):
        cfg.set("tts.voice", args.voice)
    if args.offline:
        cfg.set("pipeline.offline", True)
    from .pipeline import Pipeline
    res = Pipeline(cfg, progress=_progress_for(args)).render(
        args.job_id, hook_index=max(0, (args.hook or 1) - 1),
        hook_text=args.hook_text,
        out_dir=Path(args.out_dir) if args.out_dir else None)
    if getattr(args, "json", False):
        _emit_json({"ok": True, **{k: v for k, v in res.items() if k != "qc"},
                    "qc": res.get("qc")})
        return 0
    if getattr(args, "json", False):
        _emit_json({"ok": True, **res})
        return 0
    print("\n✅ 完成：%s" % res["out_dir"])
    print("   成片：%s   质检：%s" % (res["duration_hms"], res.get("qc", {}).get("status", "—")))
    return 0


def cmd_web(args) -> int:
    import uvicorn
    from .web import app
    url = "http://127.0.0.1:%d" % args.port
    print("🌐 控制台: %s" % url)
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_ingest(args) -> int:
    cfg = load()
    from .assets import AssetLibrary
    lib = AssetLibrary(cfg)
    try:
        res = lib.ingest(Path(args.dir) if args.dir else None,
                         category=args.category or "", emotion=args.emotion or "")
        st = lib.stats()
        if getattr(args, "json", False):
            _emit_json({"ok": True, **res, "stats": st})
            return 0
        print("入库完成：扫描 %(scanned)d，新增 %(added)d，跳过 %(skipped)d，重复 %(duplicates)d" % res)
        print("素材库：%d 条，总时长 %.1f 分钟" % (st["count"], st["total_seconds"] / 60))
    finally:
        lib.close()
    return 0


def cmd_assets(args) -> int:
    cfg = load()
    from .assets import AssetLibrary, synth_placeholders
    if args.action == "synth":
        made = synth_placeholders(cfg, count=args.count, seconds=args.seconds)
        print("已生成 %d 条测试素材：" % len(made))
        for p in made:
            print("  " + str(p))
        print("接着执行：python -m novelauto ingest")
        return 0
    lib = AssetLibrary(cfg)
    try:
        if args.action == "stats":
            st = lib.stats()
            if getattr(args, "json", False):
                _emit_json({"ok": True, **st})
                return 0
            print("素材库：%d 条，总时长 %.1f 分钟" % (st["count"], st["total_seconds"] / 60))
            for c in st["categories"]:
                print("  %-16s %4d 条  %6.1f 分钟" % (c["category"] or "(未分类)",
                                                      c["n"], c["d"] / 60))
        elif args.action == "list":
            if getattr(args, "json", False):
                _emit_json({"ok": True, "assets": lib.list(limit=args.limit)})
                return 0
            for r in lib.list(limit=args.limit):
                print("%4d  %6.1fs  %s  %s" % (r["id"], r["duration"], r["category"] or "-",
                                               r["path"]))
    finally:
        lib.close()
    return 0


def cmd_projects(args) -> int:
    cfg = load()
    from . import projects as pj
    items = [pj.summary(d) for d in pj.scan(cfg, limit=args.limit)]
    if getattr(args, "json", False):
        _emit_json({"ok": True, "projects": items})
        return 0
    if not items:
        print("还没有项目。")
        return 0
    print("%-28s %-10s %-6s %s" % ("job_id", "状态", "时长", "标题"))
    for it in items:
        print("%-28s %-10s %-6s %s" % (it["job_id"], it["status"], it["duration_hms"] or "-",
                                       it["title"]))
    return 0


def cmd_delete(args) -> int:
    cfg = load()
    from . import projects as pj
    job_id = args.job_id
    if not (cfg.path("project.work_dir") / job_id).exists() and not pj.load(cfg, job_id):
        msg = "项目不存在：%s" % job_id
        if getattr(args, "json", False):
            _emit_json({"ok": False, "error": msg})
            return 1
        print("❌ " + msg)
        return 1
    keep = bool(getattr(args, "keep_output", False))
    if not getattr(args, "yes", False):
        target = "只删中间文件、保留成片" if keep else "中间文件 + 成片一起删"
        msg = "将删除项目 %s（%s）。确认请加 --yes" % (job_id, target)
        if getattr(args, "json", False):
            _emit_json({"ok": False, "error": msg, "job_id": job_id})
            return 1
        print("⚠️  " + msg)
        return 2
    res = pj.remove(cfg, job_id, keep_output=keep)
    if getattr(args, "json", False):
        _emit_json({"ok": True, **res})
        return 0
    print("✅ 已删除项目：%s" % job_id)
    print("   中间产物：%s" % ("已删" if res["work_removed"] else "不存在"))
    if keep:
        print("   成片保留：%s" % res["kept_output"])
    else:
        print("   成片目录：%s" % ("已删" if res["output_removed"] else "不存在"))
    if res.get("note"):
        print("   说明：" + res["note"])
    return 0


def cmd_daily(args) -> int:
    from . import daily
    cfg = load()
    max_tasks = args.max or int(cfg.get("daily.max_per_run", 1) or 1)
    results = daily.run(cfg, max_tasks=max_tasks, dry_run=args.dry_run,
                        notify_=not args.no_notify)
    # 收件箱空（skipped）不算失败
    ok = True if args.dry_run else (bool(results) and all(
        r.get("ok") or r.get("skipped") for r in results))
    if getattr(args, "json", False):
        _emit_json({"ok": ok, "results": results})
        return 0 if ok else 1
    if not results:
        return 0
    if args.dry_run:
        ready = [r for r in results if r.get("dry_run")]
        if not ready:
            print("今天没有待处理的原文（daily/inbox 是空的）")
            return 0
        print("将会处理：")
        for r in ready:
            print("  - %s（标题：%s，开头 #%s）" % (r["file"], r["title"], r["hook"]))
        return 0
    for r in results:
        if r.get("skipped"):
            print("今天没有待处理的原文（daily/inbox 是空的）")
        elif r.get("ok"):
            print("✅ %s → %s（%s）" % (r.get("title"), r.get("out_dir"), r.get("duration_hms", "")))
        else:
            print("❌ %s：%s" % (r.get("file") or r.get("title"), r.get("error")))
    return 0 if ok else 1


def cmd_doctor(args) -> int:
    cfg = load()
    ok = True
    as_json = bool(getattr(args, "json", False))
    if not as_json:
        print("== 环境自检 ==")
    checks = []
    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        checks.append({"name": exe, "ok": bool(p), "detail": p or "未找到"})
        if not as_json:
            print("  %-8s %s" % (exe, p or "❌ 未找到"))
        ok = ok and bool(p)
    if not as_json:
        print("  python   %s" % sys.version.split()[0])
    for mod in ("edge_tts", "numpy", "PIL", "fastapi", "yaml", "httpx"):
        try:
            __import__(mod)
            checks.append({"name": mod, "ok": True, "detail": "ok"})
            if not as_json:
                print("  %-8s ✓" % mod)
        except Exception:
            checks.append({"name": mod, "ok": False, "detail": "未安装"})
            if not as_json:
                print("  %-8s ❌ 未安装" % mod)
            ok = False
    font = Path(str(cfg.get("project.font_path")))
    checks.append({"name": "font", "ok": font.exists(), "detail": str(font)})
    if not as_json:
        print("  字体     %s %s" % (font.name, "✓" if font.exists() else "❌ 不存在（封面/字幕会异常）"))
    key = cfg.api_key
    checks.append({"name": "llm_key", "ok": bool(key), "detail": "已配置" if key else "未配置"})
    if not as_json:
        print("  LLM Key  %s" % ("✓ 已配置" if key else "— 未配置（将使用离线改写）"))
    from . import i18n as _i18n
    _lang = _i18n.lang_of(cfg)
    checks.append({"name": "language", "ok": True, "detail": _lang})
    checks.append({"name": "tts", "ok": True,
                   "detail": "%s / %s / %s" % (cfg.get("tts.provider"), cfg.get("tts.voice"),
                                                cfg.get("tts.rate"))})
    if not as_json:
        print("  语言     %s（%s）" % (_lang, _i18n.LANGS[_lang]["label"]))
        print("  TTS      provider=%s voice=%s rate=%s" % (
            cfg.get("tts.provider"), cfg.get("tts.voice"), cfg.get("tts.rate")))
    from .assets import AssetLibrary
    lib = AssetLibrary(cfg)
    try:
        st = lib.stats()
    finally:
        lib.close()
    if not as_json:
        print("  素材库   %d 条 / %.1f 分钟（可用 %d 条 / %.1f 分钟）  (%s)" % (
            st["count"], st["total_seconds"] / 60,
            st.get("usable_count", st["count"]), st.get("usable_seconds", st["total_seconds"]) / 60,
            cfg.get("assets.dir")))
        if st["count"] == 0:
            print("           → 先放素材到 %s 再 `ingest`，或 `assets synth` 造测试素材" % cfg.get("assets.dir"))
    free = shutil.disk_usage(str(ROOT))
    checks.append({"name": "assets", "ok": st.get("usable_count", st["count"]) > 0,
                   "detail": "%d 条可用 / %.1f 分钟" % (
                       st.get("usable_count", st["count"]),
                       st.get("usable_seconds", st["total_seconds"]) / 60)})
    checks.append({"name": "disk_free_gb", "ok": free.free > 20 * 1073741824,
                   "detail": round(free.free / 1073741824, 1)})
    if as_json:
        _emit_json({"ok": ok, "lang": _i18n.lang_of(cfg), "checks": checks,
                    "minutes_min": cfg.get("target.minutes_min"),
                    "minutes_max": cfg.get("target.minutes_max")})
        return 0 if ok else 1
    print("  磁盘     剩余 %.1f GB" % (free.free / 1073741824))
    print("== %s ==" % ("通过" if ok else "有缺失，见上"))
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="novelauto", description="TikTok 小说推文自动化生产")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑一条完整流水线")
    r.add_argument("--input", "-i", help="原文 txt 路径")
    r.add_argument("--text", "-t", help="直接把原文传进来")
    r.add_argument("--minutes", "-m", type=float, help="目标时长（分钟），默认按 5-8 分钟自动估")
    r.add_argument("--title", help="标题/项目名（用于目录命名）")
    r.add_argument("--job-id", help="指定 job id（重跑已有项目用）")
    r.add_argument("--voice", help="覆盖音色，如 zh-CN-YunjianNeural")
    r.add_argument("--provider", help="覆盖 TTS provider: edge/say/volc/minimax")
    r.add_argument("--out-dir", help="覆盖输出根目录")
    r.add_argument("--offline", action="store_true", help="强制离线改写（不调 LLM）")
    r.add_argument("--force", action="store_true", help="忽略已有 script.json 重新改写")
    r.add_argument("--hook", type=int, default=1, choices=[1, 2, 3], help="用第几个黄金三秒")
    r.add_argument("--rate", help="语速百分比，如 -10%% / 0%% / +8%%")
    r.add_argument("--json", action="store_true", help="机器可读 JSON 输出（给 AI/脚本用）")
    r.add_argument("--language", choices=["en", "zh"], help="内容语言（默认 en 海外版）")
    r.add_argument("--style", choices=["auto", "thriller", "romance", "revenge", "sweet", "drama"])
    r.add_argument("--person", choices=["auto", "first", "third"])
    r.add_argument("--hook-strength", dest="hook_strength", choices=["normal", "strong", "max"])

    r.set_defaults(func=cmd_run)

    pr = sub.add_parser("prepare", help="只做改写，输出 3 个黄金三秒供选择")
    pr.add_argument("--input", "-i")
    pr.add_argument("--text", "-t")
    pr.add_argument("--minutes", "-m", type=float)
    pr.add_argument("--title")
    pr.add_argument("--job-id")
    pr.add_argument("--offline", action="store_true")
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--language", choices=["en", "zh"])
    pr.add_argument("--style", choices=["auto", "thriller", "romance", "revenge", "sweet", "drama"])
    pr.add_argument("--person", choices=["auto", "first", "third"])
    pr.add_argument("--hook-strength", dest="hook_strength", choices=["normal", "strong", "max"])
    pr.add_argument("--voice", help="音色，如 en-US-JennyNeural / zh-CN-YunxiNeural")
    pr.add_argument("--rate", help="语速百分比，如 -10%% / 0%% / +8%%")
    pr.add_argument("--json", action="store_true", help="机器可读 JSON 输出")

    pr.set_defaults(func=cmd_prepare)

    rd = sub.add_parser("render", help="用已有剧本渲染（可换开头/输出目录）")
    rd.add_argument("--job-id", required=True)
    rd.add_argument("--hook", type=int, default=1, choices=[1, 2, 3])
    rd.add_argument("--hook-text", help="直接用自定义开头文字")
    rd.add_argument("--voice", help="音色，如 en-US-JennyNeural / zh-CN-YunxiNeural")
    rd.add_argument("--rate", help="语速百分比，如 -10%% / 0%% / +8%%")
    rd.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    rd.add_argument("--out-dir")
    rd.add_argument("--offline", action="store_true")
    rd.add_argument("--language", choices=["en", "zh"])
    rd.set_defaults(func=cmd_render)

    w = sub.add_parser("web", help="启动本地 Web 控制台")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-browser", action="store_true")
    w.set_defaults(func=cmd_web)

    i = sub.add_parser("ingest", help="扫描素材目录并入库")
    i.add_argument("--dir", help="素材目录，默认 config 里的 assets.dir")
    i.add_argument("--category", default="")
    i.add_argument("--emotion", default="")
    i.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    i.set_defaults(func=cmd_ingest)

    a = sub.add_parser("assets", help="素材库操作")
    a.add_argument("action", choices=["synth", "stats", "list"])
    a.add_argument("--count", type=int, default=3)
    a.add_argument("--seconds", type=float, default=90)
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    a.set_defaults(func=cmd_assets)

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    d.set_defaults(func=cmd_doctor)

    pj = sub.add_parser("projects", help="列出已保存的项目")
    pj.add_argument("--limit", type=int, default=50)
    pj.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    pj.set_defaults(func=cmd_projects)

    dl = sub.add_parser("delete", help="删除项目（中间产物 + 记录，可保留成片）")
    dl.add_argument("--job-id", required=True, help="项目 id（见 projects 命令）")
    dl.add_argument("--keep-output", action="store_true", help="保留成片，只删中间文件")
    dl.add_argument("--yes", action="store_true", help="确认执行（不加只提示）")
    dl.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    dl.set_defaults(func=cmd_delete)

    dy = sub.add_parser("daily", help="每日自动生成：处理 daily/inbox 里的原文")
    dy.add_argument("--max", type=int, default=0, help="最多处理几篇（默认取 daily.max_per_run）")
    dy.add_argument("--dry-run", action="store_true", help="只看会处理哪些文件，不真正生成")
    dy.add_argument("--no-notify", action="store_true", help="不发系统通知")
    dy.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    dy.set_defaults(func=cmd_daily)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print("\n❌ " + str(e))
        return 2
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except Exception as e:  # noqa: BLE001
        if os.getenv("NOVELAUTO_DEBUG"):
            raise
        print("\n❌ 运行失败：" + str(e))
        print("   想看完整堆栈：在命令前加 NOVELAUTO_DEBUG=1")
        return 1


if __name__ == "__main__":
    sys.exit(main())
