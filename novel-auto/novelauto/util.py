"""通用工具：进程调用、ffprobe、哈希。"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional


class CmdError(RuntimeError):
    pass


def log(msg: str) -> None:
    """日志走 stderr，保证 --json 模式下 stdout 只有 JSON。"""
    print(msg, file=sys.stderr, flush=True)


def run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True,
        timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    p = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and p.returncode != 0:
        tail = p.stderr.decode("utf-8", "ignore").strip().splitlines()[-12:]
        raise CmdError(
            "命令失败 rc=%s\ncmd: %s\n%s" % (p.returncode, " ".join(map(str, cmd)), "\n".join(tail))
        )
    return p


def ffprobe(path: Path) -> dict:
    p = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    return json.loads(p.stdout.decode("utf-8", "ignore"))


def media_info(path: Path) -> dict:
    d = ffprobe(path)
    fmt = d.get("format", {})
    dur = float(fmt.get("duration") or 0)
    w = h = 0
    fps = 0.0
    for s in d.get("streams", []):
        if s.get("codec_type") == "video":
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
            r = s.get("avg_frame_rate") or "0/1"
            try:
                num, den = r.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except Exception:
                fps = 0.0
            break
    return {"duration": dur, "width": w, "height": h, "fps": fps}


def duration(path: Path) -> float:
    return media_info(path)["duration"]


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def human_dur(sec: float) -> str:
    m, s = divmod(int(round(sec)), 60)
    return "%d:%02d" % (m, s)
