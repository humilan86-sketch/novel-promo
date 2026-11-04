"""每日自动生成：run once / cron-friendly。

流程：daily/inbox 里取原文 → prepare（3 版开头）→ render（选一版）→ 归档 + 通知。
由 launchd 每天定时调用（见 scripts/install_daily.sh），也可以手动：
    ./.venv/bin/python -m novelauto daily --dry-run
    ./.venv/bin/python -m novelauto daily
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config, load

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}
LOCK_STALE_SEC = 12 * 3600


# ---------------------------------------------------------------- 小工具

def _d(cfg: Config, key: str, default: str) -> Path:
    raw = cfg.get("daily." + key, default) or default
    p = Path(os.path.expanduser(str(raw)))
    return p if p.is_absolute() else Path(cfg.root) / p


def _ensure_dirs(cfg: Config) -> None:
    for key, default in (("inbox", "daily/inbox"), ("done", "daily/done"),
                         ("failed", "daily/failed"), ("logs", "daily/logs")):
        _d(cfg, key, default).mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def notify(title: str, message: str) -> None:
    """macOS 通知中心；没有 osascript 的环境静默跳过。"""
    try:
        script = 'display notification %s with title %s' % (
            json.dumps(message[:400], ensure_ascii=False),
            json.dumps(title[:120], ensure_ascii=False),
        )
        subprocess.run(["osascript", "-e", script], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def wait_network(timeout: int) -> bool:
    """刚被唤醒时网络可能还没就绪，等到能连上再跑。"""
    if timeout <= 0:
        return True
    hosts = [("api.deepseek.com", 443), ("speech.platform.bing.com", 443),
             ("1.1.1.1", 443)]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for host, port in hosts:
            try:
                with socket.create_connection((host, port), timeout=5):
                    return True
            except OSError:
                pass
        time.sleep(10)
    return False


class _Awake:
    """跑任务时阻止系统休眠（caffeinate 随子进程退出自动结束）。"""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self):
        if self.enabled:
            try:
                self.proc = subprocess.Popen(
                    ["caffeinate", "-dims"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                self.proc = None
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass


class _Lock:
    """防止定时任务和手动执行同时跑（同时写素材库/输出目录会互相干扰）。"""

    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0
            if age < LOCK_STALE_SEC:
                raise RuntimeError("另一个自动生成任务正在运行（锁：%s）" % self.path)
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(self.fd, ("%d %s\n" % (os.getpid(), _now())).encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            try:
                self.path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- 原文

def split_title(text: str, fallback: str) -> tuple:
    """支持在正文第一行写 `title: 书名` 指定标题。"""
    lines = text.lstrip("\ufeff").splitlines()
    title = ""
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        m = re.match(r"^\s*(?:title|标题)\s*[:：]\s*(.+?)\s*$", lines[0], re.I)
        if m:
            title = m.group(1)
            lines = lines[1:]
    body = "\n".join(lines).strip()
    if not title:
        title = re.sub(r"^\s*\d+\s*[-_.、\s]*", "", fallback).strip() or fallback
    return title[:80], body


def pending_files(cfg: Config) -> List[Path]:
    inbox = _d(cfg, "inbox", "daily/inbox")
    if not inbox.is_dir():
        return []
    files = [p for p in inbox.iterdir()
             if p.is_file() and not p.name.startswith(".")
             and p.suffix.lower() in TEXT_SUFFIXES]
    return sorted(files, key=lambda p: p.name.lower())


def _archive(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ("%s_%s" % (_stamp(), src.name))
    n = 1
    while dest.exists():
        dest = dest_dir / ("%s_%d_%s" % (_stamp(), n, src.name))
        n += 1
    shutil.move(str(src), str(dest))
    return dest


def _hook_for_today(cfg: Config) -> int:
    raw = cfg.get("daily.hook", 1)
    if str(raw).lower() in ("auto", "rotate", "轮换"):
        # 每天轮换 1/2/3，避免天天同一个开头套路
        return (datetime.now().timetuple().tm_yday % 3) + 1
    try:
        return max(1, min(3, int(raw)))
    except (TypeError, ValueError):
        return 1


def _minutes(cfg: Config) -> Optional[float]:
    try:
        v = float(cfg.get("daily.minutes", 0) or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _style_kwargs(cfg: Config) -> Dict[str, str]:
    def pick(key: str) -> str:
        v = str(cfg.get("daily." + key, "auto") or "auto").strip().lower()
        return "" if v in ("", "auto", "自动") else v
    return {"style": pick("style"), "person": pick("person"),
            "hook_strength": pick("hook_strength")}


def _apply_overrides(cfg: Config) -> None:
    """把 daily.* 里显式配置的项盖到主配置上（空值表示用默认）。"""
    pairs = (("voice", "tts.voice"), ("rate", "tts.rate"), ("language", "project.language"))
    for dkey, ckey in pairs:
        v = cfg.get("daily." + dkey, "")
        if v:
            cfg.set(ckey, v)
    if cfg.get("daily.offline", False):
        cfg.set("pipeline.offline", True)


def _write_status(cfg: Config, payload: Dict[str, Any]) -> None:
    path = _d(cfg, "logs", "daily/logs") / "status.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


# ---------------------------------------------------------------- 主流程

def run(cfg: Config, max_tasks: int = 1, dry_run: bool = False,
        notify_: bool = True, progress=None) -> List[Dict[str, Any]]:
    if not cfg.get("daily.enabled", True):
        return [{"skipped": True, "reason": "daily.enabled = false"}]

    _ensure_dirs(cfg)
    files = pending_files(cfg)
    if not files:
        payload = {"at": _now(), "ok": True, "skipped": True,
                   "reason": "收件箱是空的", "tasks": []}
        _write_status(cfg, payload)
        if notify_ and not dry_run:
            notify("小说推文 · 今天没有新原文",
                   "daily/inbox 是空的，把原文放进去就会自动生成。")
        return [{"skipped": True}]

    files = files[:max(1, int(max_tasks))]
    hook = _hook_for_today(cfg)
    if dry_run:
        out = []
        for p in files:
            title, _ = split_title(p.read_text(encoding="utf-8", errors="ignore"), p.stem)
            out.append({"dry_run": True, "file": p.name, "title": title, "hook": hook})
        return out

    _apply_overrides(cfg)
    started = time.time()
    payload: Dict[str, Any] = {"at": _now(), "ok": True, "skipped": False,
                               "hook": hook, "tasks": []}

    from .pipeline import Pipeline  # 延迟导入，--dry-run 不用加载 ffmpeg 相关

    results: List[Dict[str, Any]] = []
    with _Lock(_d(cfg, "logs", "daily/logs") / ".lock"):
        if cfg.get("daily.keep_awake", True):
            net_ok = wait_network(int(cfg.get("daily.network_timeout", 240) or 0))
            if not net_ok:
                raise RuntimeError("等待网络就绪超时（%ss），可能是刚唤醒还没联网"
                                   % cfg.get("daily.network_timeout"))
        with _Awake(bool(cfg.get("daily.keep_awake", True))):
            pipeline = Pipeline(cfg, progress=progress)
            if cfg.get("daily.ingest", False):
                _ingest_if_configured(cfg, payload)
            for src in files:
                item = _one(cfg, pipeline, src, hook, payload, progress)
                results.append(item)

    elapsed = round(time.time() - started, 1)
    ok = bool(results) and all(r.get("ok") for r in results)
    payload.update({"ok": ok, "elapsed_sec": elapsed, "at": _now()})
    payload["tasks"] = [{k: v for k, v in r.items() if not str(k).startswith("_")}
                        for r in results]
    _write_status(cfg, payload)
    if notify_:
        _notify_summary(results, ok)
    return results


def _ingest_if_configured(cfg: Config, payload: Dict[str, Any]) -> None:
    src = cfg.get("daily.ingest_dir", "") or cfg.get("assets.dir", "assets/clips")
    from .assets import AssetLibrary
    lib = AssetLibrary(cfg)
    try:
        folder = Path(os.path.expanduser(str(src)))
        if not folder.is_absolute():
            folder = Path(cfg.root) / folder
        res = lib.ingest(folder)
        payload.setdefault("ingest", []).append(
            {"dir": str(folder), "added": res.get("added", 0),
             "skipped": res.get("skipped", 0), "duplicates": res.get("duplicates", 0)})
    finally:
        lib.close()


def _one(cfg: Config, pipeline, src: Path, hook: int,
         payload: Dict[str, Any], progress) -> Dict[str, Any]:
    retries = max(0, int(cfg.get("daily.retry", 1) or 0))
    wait = max(0, int(cfg.get("daily.retry_wait", 90) or 0))
    item: Dict[str, Any] = {"file": src.name, "ok": False}
    last_err = ""
    for attempt in range(retries + 1):
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
            title, body = split_title(text, src.stem)
            if progress:
                progress(0, "每日生成：%s（第 %d/%d 篇）" % (title, len(payload.get("tasks", [])) + 1,
                                                         cfg.get("daily.max_per_run", 1)))
            script = pipeline.prepare(body, target_minutes=_minutes(cfg), title=title,
                                      force=False, **_style_kwargs(cfg))
            job_id = script["job_id"]
            meta = pipeline.render(job_id, hook_index=hook)
            out_dir = meta.get("out_dir") or ""
            archived = _archive(src, _d(cfg, "done", "daily/done"))
            result = {"job_id": job_id, "title": title, "hook": hook,
                      "duration_hms": meta.get("duration_hms", ""),
                      "out_dir": out_dir, "final": str(Path(out_dir) / "final.mp4"),
                      "archived": str(archived), "qc_status": meta.get("qc_status", "")}
            (archived.with_name(archived.name + ".result.json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            item.update({"ok": True, **result})
            return item
        except Exception as e:  # noqa: BLE001  一次失败不影响队列里的下一篇
            last_err = "%s: %s" % (type(e).__name__, e)
            item["error"] = last_err
            if attempt < retries:
                time.sleep(wait)
    try:
        failed = _archive(src, _d(cfg, "failed", "daily/failed"))
        (failed.with_name(failed.name + ".error.txt")).write_text(
            "%s\n\n%s\n" % (_now(), traceback.format_exc()), encoding="utf-8")
        item["archived"] = str(failed)
    except OSError:
        pass
    return item


def _notify_summary(results: List[Dict[str, Any]], ok: bool) -> None:
    good = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    if good and not bad:
        r = good[0]
        notify("✅ 今日成片已完成", "%s（%s）\n%s" % (
            r.get("title", ""), r.get("duration_hms", ""), r.get("out_dir", "")))
    elif bad:
        r = bad[0]
        notify("⚠️ 自动生成失败", "%s：%s" % (r.get("file", ""), r.get("error", "未知错误")))
    else:
        notify("小说推文", "本次没有需要处理的任务。")
