"""本地 Web 控制台。

流程：贴原文 → 解析（出 3 个黄金三秒）→ 选一个/改文案 → 生成视频 → 预览下载。
支持在界面上选择「素材输入目录」和「输出目录」。
"""
import json
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from . import i18n
from . import projects
from .util import log
from .config import ROOT, load

app = FastAPI(title="小说推文自动化")
_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")))
JOBS: Dict[str, Dict] = {}
LOCK = threading.Lock()

SETTINGS_FILE = ROOT / ".cache" / "web_settings.json"
ALLOWED_FILES = {"final.mp4", "cover.jpg", "publish.txt", "qa_report.txt",
                 "script.json", "meta.json"}


# ---------------------------------------------------------------- 设置
def load_settings() -> Dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: Dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_cfg():
    cfg = load()
    st = load_settings()
    if st.get("language"):
        cfg.set("project.language", st["language"])
        cfg.set("project.font", "")          # 换语言时字体/音色回落到该语言默认
        cfg.set("project.font_path", "")
        if st.get("tts_voice_for") != st["language"]:
            cfg.set("tts.voice", "")
        cfg.set("subtitle.max_chars_per_line", 0)
        i18n.apply(cfg)
    if st.get("assets_dir"):
        cfg.set("assets.dir", st["assets_dir"])
    cfg.set("assets.exclude_categories",
            ["_synthetic"] if st.get("exclude_synthetic", True) else [])
    if st.get("out_dir"):
        cfg.set("project.out_dir", st["out_dir"])
    if st.get("tts_voice"):
        cfg.set("tts.voice", st["tts_voice"])
    if st.get("tts_rate"):
        cfg.set("tts.rate", st["tts_rate"])
    return cfg


# ---------------------------------------------------------------- 任务
def _set(job_id: str, **kw) -> None:
    with LOCK:
        JOBS.setdefault(job_id, {}).update(kw)
        if len(JOBS) > 50:
            for k in sorted(JOBS, key=lambda x: JOBS[x].get("created", ""))[:-50]:
                JOBS.pop(k, None)


def _progress_maker(job_id: str):
    def cb(stage: str, pct: float, msg: str) -> None:
        _set(job_id, stage=stage, pct=round(float(pct), 3), msg=msg,
             updated=time.strftime("%H:%M:%S"))
    return cb


class PrepareReq(BaseModel):
    text: str
    minutes: float = 0
    title: str = ""
    style: str = ""
    person: str = ""
    hook_strength: str = ""
    voice: str = ""
    rate: str = ""


class RenderReq(BaseModel):
    job_id: str
    hook_index: int = 0
    hook_text: str = ""
    segments: Optional[list] = None
    out_dir: str = ""
    voice: str = ""
    rate: str = ""


class IngestReq(BaseModel):
    dir: str = ""
    category: str = ""
    emotion: str = ""


class SettingsReq(BaseModel):
    assets_dir: str = ""
    out_dir: str = ""
    tts_voice: str = ""
    tts_rate: str = ""
    language: str = ""
    exclude_synthetic: Optional[bool] = None


class PickReq(BaseModel):
    kind: str = "assets"  # assets | output


def st_settings_exclude() -> bool:
    return bool(load_settings().get("exclude_synthetic", True))


def _prep_worker(job_id: str, req: PrepareReq) -> None:
    try:
        _set(job_id, status="preparing", stage="script", pct=0.05, msg="解析原文中…")
        cfg = make_cfg()
        if req.voice:
            cfg.set("tts.voice", req.voice)
        if req.rate:
            cfg.set("tts.rate", req.rate)
        from .pipeline import Pipeline
        prep = Pipeline(cfg, progress=_progress_maker(job_id)).prepare(
            req.text, target_minutes=req.minutes or None, title=req.title or "",
            job_id=job_id, style=req.style, person=req.person,
            hook_strength=req.hook_strength)
        _set(job_id, status="prepared", pct=1.0, msg="解析完成",
             result={"job_id": prep["job_id"], "hooks": prep["hooks"],
                     "segments": prep["segments"], "publish": prep["publish"],
                     "meta": prep["meta"], "target_sec": prep["target_sec"]})
    except Exception as e:  # noqa: BLE001
        _set(job_id, status="error", error=str(e), trace=traceback.format_exc()[-2000:])


def _render_worker(job_id: str, req: RenderReq) -> None:
    try:
        _set(job_id, status="rendering", stage="tts", pct=0.1, msg="准备配音…")
        cfg = make_cfg()
        if req.voice:
            cfg.set("tts.voice", req.voice)
        if req.rate:
            cfg.set("tts.rate", req.rate)
        from .pipeline import Pipeline
        res = Pipeline(cfg, progress=_progress_maker(job_id)).render(
            req.job_id, hook_index=req.hook_index, hook_text=req.hook_text or "",
            segments=req.segments or None)
        # 用户选了「导出目录」就额外复制一份，成片本身仍放在 output/<job_id>/
        exported = ""
        if req.out_dir.strip():
            try:
                import shutil
                dest = Path(req.out_dir).expanduser()
                dest.mkdir(parents=True, exist_ok=True)
                dest = dest / Path(res["out_dir"]).name
                dest.mkdir(parents=True, exist_ok=True)
                for name in ("final.mp4", "cover.jpg", "publish.txt", "qa_report.txt"):
                    src = Path(res["out_dir"]) / name
                    if src.exists():
                        shutil.copy2(src, dest / name)
                exported = str(dest)
            except Exception as e:  # noqa: BLE001
                log("[warn] 导出到自定义目录失败: %s" % e)
        res["exported_dir"] = exported
        _set(job_id, status="done", pct=1.0, msg="完成", result=res,
             files=["final.mp4", "cover.jpg", "publish.txt", "qa_report.txt"])
    except Exception as e:  # noqa: BLE001
        _set(job_id, status="error", error=str(e), trace=traceback.format_exc()[-2000:])


# ---------------------------------------------------------------- 页面 & API
@app.get("/", response_class=HTMLResponse)
def index():
    return _env.get_template("index.html").render()


@app.get("/api/settings")
def api_settings() -> JSONResponse:
    cfg = make_cfg()
    lang = i18n.lang_of(cfg)
    return JSONResponse({
        "assets_dir": str(cfg.path("assets.dir")),
        "out_dir": str(cfg.path("project.out_dir")),
        "tts_voice": str(cfg.get("tts.voice")),
        "tts_rate": str(cfg.get("tts.rate")),
        "language": lang,
        "languages": [{"code": k, "label": v["label"]} for k, v in i18n.LANGS.items()],
        "voices": [{"id": v, "name": n} for v, n in i18n.LANGS[lang]["voices"]],
        "has_llm": bool(cfg.api_key),
        "minutes_min": cfg.get("target.minutes_min"),
        "minutes_max": cfg.get("target.minutes_max"),
        "style": str(cfg.get("script.style")),
        "person": str(cfg.get("script.person")),
        "hook_strength": str(cfg.get("script.hook_strength")),
        "styles": i18n.style_options(lang),
        "persons": i18n.person_options(lang),
        "hook_strengths": i18n.hook_options(lang),
        "exclude_synthetic": bool(st_settings_exclude()),
    })


@app.post("/api/settings")
def api_save_settings(req: SettingsReq) -> JSONResponse:
    st = load_settings()
    if req.assets_dir:
        st["assets_dir"] = req.assets_dir
    if req.out_dir:
        st["out_dir"] = req.out_dir
    if req.exclude_synthetic is not None:
        st["exclude_synthetic"] = bool(req.exclude_synthetic)
    if req.language:
        st["language"] = req.language
        st["tts_voice"] = ""          # 换语言后音色回落到新语言默认
    if req.tts_voice:
        st["tts_voice"] = req.tts_voice
    if req.tts_rate:
        st["tts_rate"] = req.tts_rate
        st["tts_voice_for"] = st.get("language") or i18n.lang_of(load())
    save_settings(st)
    return JSONResponse({"ok": True, **st})


@app.get("/api/voices")
def api_voices(language: str = "") -> JSONResponse:
    lang = language or i18n.lang_of(load())
    lang = lang if lang in i18n.LANGS else "en"
    return JSONResponse({"language": lang,
                         "voices": [{"id": v, "name": n} for v, n in i18n.LANGS[lang]["voices"]],
                         "default": i18n.LANGS[lang]["default_voice"]})


@app.post("/api/pick-folder")
def api_pick_folder(req: PickReq) -> JSONResponse:
    """弹出 macOS 选择文件夹对话框（服务在本机运行，所以在浏览器里点也能用）。"""
    prompt = "选择素材文件夹" if req.kind == "assets" else "选择输出目录"
    script = 'POSIX path of (choose folder with prompt "%s")' % prompt
    try:
        p = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, "无法打开选择窗口: %s" % e)
    if p.returncode != 0:
        return JSONResponse({"cancelled": True, "path": ""})
    path = p.stdout.decode("utf-8", "ignore").strip().rstrip("/")
    if not path:
        return JSONResponse({"cancelled": True, "path": ""})
    st = load_settings()
    st["assets_dir" if req.kind == "assets" else "out_dir"] = path
    save_settings(st)
    return JSONResponse({"cancelled": False, "path": path})


@app.post("/api/prepare")
def api_prepare(req: PrepareReq) -> JSONResponse:
    text = req.text.strip()
    if len(text) < 800:
        raise HTTPException(400, "原文太短（<800 字），长视频至少需要 1400 字左右")
    if len(text) > 60000:
        raise HTTPException(400, "原文过长（>6 万字），请分段生成")
    if req.minutes and not (3 <= req.minutes <= 10):
        raise HTTPException(400, "目标时长需在 3-10 分钟之间")
    job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    _set(job_id, status="queued", pct=0.0, stage="script", msg="排队中",
         title=req.title, created=time.strftime("%Y-%m-%d %H:%M:%S"))
    threading.Thread(target=_prep_worker, args=(job_id, req), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.post("/api/render")
def api_render(req: RenderReq) -> JSONResponse:
    with LOCK:
        job = JOBS.get(req.job_id)
    if not job:
        raise HTTPException(404, "任务不存在，请先解析原文")
    _set(req.job_id, status="queued", pct=0.0, msg="排队中", started=time.strftime("%H:%M:%S"))
    threading.Thread(target=_render_worker, args=(req.job_id, req), daemon=True).start()
    return JSONResponse({"job_id": req.job_id})


@app.get("/api/projects")
def api_projects() -> JSONResponse:
    cfg = make_cfg()
    items = [projects.summary(d) for d in projects.scan(cfg)]
    with LOCK:
        live = dict(JOBS)
    for it in items:
        j = live.get(it["job_id"])
        if j:
            it["live_status"] = j.get("status")
            it["live_pct"] = j.get("pct")
            it["live_msg"] = j.get("msg")
    return JSONResponse({"projects": items})


@app.get("/api/projects/{job_id}")
def api_project(job_id: str) -> JSONResponse:
    cfg = make_cfg()
    data = projects.load(cfg, job_id)
    if data:
        with LOCK:
            j = JOBS.get(job_id)
        if j:
            data["live_status"] = j.get("status")
            data["live_pct"] = j.get("pct")
        return JSONResponse(data)
    with LOCK:
        j = JOBS.get(job_id)
    if j and j.get("result"):
        return JSONResponse({"job_id": job_id, **j["result"]})
    raise HTTPException(404, "项目不存在")


@app.delete("/api/projects/{job_id}")
def api_delete_project(job_id: str, keep_output: bool = False) -> JSONResponse:
    """删除项目：中间产物 + 记录，默认连成品一起删。"""
    live = None
    with LOCK:
        live = JOBS.get(job_id)
        if live and live.get("status") in ("queued", "preparing", "rendering"):
            raise HTTPException(409, "任务正在生成中，等它结束再删")
    cfg = make_cfg()
    data = projects.load(cfg, job_id)
    has_work = (cfg.path("project.work_dir") / job_id).exists()
    has_out = (cfg.path("project.out_dir") / job_id).exists()
    if not data and not has_work and not has_out:
        raise HTTPException(404, "项目不存在")
    try:
        res = projects.remove(cfg, job_id, keep_output=keep_output)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with LOCK:
        JOBS.pop(job_id, None)
    return JSONResponse({"ok": True, **res})


@app.get("/api/jobs")
def api_jobs() -> JSONResponse:
    with LOCK:
        items = [dict(v, job_id=k) for k, v in JOBS.items()]
    items.sort(key=lambda x: x.get("created", ""), reverse=True)
    return JSONResponse({"jobs": items})


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> JSONResponse:
    with LOCK:
        job = JOBS.get(job_id)
    if job:
        return JSONResponse(dict(job, job_id=job_id))
    # 服务重启后内存里没有，从磁盘项目记录恢复
    data = projects.load(make_cfg(), job_id)
    if not data:
        raise HTTPException(404, "job 不存在")
    status = data.get("status") or "prepared"
    return JSONResponse({
        "job_id": job_id, "status": status if status != "done" else "done", "pct": 1.0,
        "result": {
            "job_id": job_id,
            "hooks": data.get("hooks") or [],
            "segments": data.get("segments") or [],
            "publish": data.get("publish") or {},
            "target_sec": data.get("target_sec"),
            "meta": {"degraded": data.get("degraded"), "style": data.get("style")},
            "duration_hms": data.get("duration_hms"),
            "duration_sec": data.get("duration_sec"),
            "chapters": data.get("chapters"),
            "size_mb": data.get("size_mb"),
            "lufs": data.get("lufs"),
            "qc": data.get("qc"),
            "out_dir": data.get("out_dir"),
        },
    })


@app.get("/api/assets")
def api_assets() -> JSONResponse:
    cfg = make_cfg()
    from .assets import AssetLibrary
    lib = AssetLibrary(cfg)
    try:
        st = lib.stats()
        st["dir"] = str(cfg.path("assets.dir"))
        return JSONResponse(st)
    finally:
        lib.close()


BLOCKED_PREFIX = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc",
                  "/private/etc", "/dev", "/proc", "/var", "/Applications")


def _check_ingest_dir(raw: str) -> Optional[Path]:
    """允许项目目录、用户目录、外接硬盘；只拦系统目录。"""
    if not raw.strip():
        return None
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        raise HTTPException(400, "目录不存在：%s" % p)
    if not p.is_dir():
        raise HTTPException(400, "这不是文件夹：%s" % p)
    for prefix in BLOCKED_PREFIX:
        try:
            p.relative_to(Path(prefix))
            raise HTTPException(400, "为了安全，不允许扫描系统目录：%s" % p)
        except ValueError:
            continue
    return p


@app.post("/api/ingest")
def api_ingest(req: IngestReq) -> JSONResponse:
    cfg = make_cfg()
    target_dir = _check_ingest_dir(req.dir)
    from .assets import AssetLibrary
    lib = AssetLibrary(cfg)
    try:
        res = lib.ingest(target_dir, category=req.category, emotion=req.emotion)
        return JSONResponse(dict(res, stats=lib.stats()))
    finally:
        lib.close()


@app.post("/api/assets/purge")
def api_purge() -> JSONResponse:
    cfg = make_cfg()
    from .assets import AssetLibrary
    lib = AssetLibrary(cfg)
    try:
        res = lib.purge_synthetic()
        return JSONResponse(dict(res, stats=lib.stats()))
    finally:
        lib.close()


@app.post("/api/assets/synth")
def api_synth() -> JSONResponse:
    cfg = make_cfg()
    from .assets import AssetLibrary, synth_placeholders
    made = synth_placeholders(cfg, count=3, seconds=120)
    lib = AssetLibrary(cfg)
    try:
        res = lib.ingest()
    finally:
        lib.close()
    return JSONResponse({"made": [str(p) for p in made], "ingest": res})


@app.get("/files/{job_id}/{name}")
def files(job_id: str, name: str):
    """只允许下载 output/<job>/ 下的白名单文件，彻底挡住路径穿越。"""
    if name not in ALLOWED_FILES:
        raise HTTPException(404, "文件不存在")
    out_root = (ROOT / "output").resolve()
    p = (out_root / job_id / name).resolve()
    try:
        p.relative_to(out_root)
    except ValueError:
        raise HTTPException(403, "非法路径")
    if not p.is_file():
        raise HTTPException(404, "文件不存在")
    media = "video/mp4" if name.endswith(".mp4") else None
    return FileResponse(str(p), media_type=media, filename=name)
