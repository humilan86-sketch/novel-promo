"""项目持久化：每个 job 在 work/<job_id>/project.json 存一份记录。

Web 控制台刷新/重启后靠它恢复项目列表、3 个开头、分段稿、发布文案和成品信息。
"""
import json
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

from .util import ensure_dir


def _path(cfg, job_id: str) -> Path:
    return cfg.path("project.work_dir") / job_id / "project.json"


def save(cfg, job_id: str, data: Dict) -> None:
    p = _path(cfg, job_id)
    ensure_dir(p.parent)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load(cfg, job_id: str) -> Optional[Dict]:
    p = _path(cfg, job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def update(cfg, job_id: str, **kw) -> Optional[Dict]:
    data = load(cfg, job_id) or {"job_id": job_id}
    data.update(kw)
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(cfg, job_id, data)
    return data


def scan(cfg, limit: int = 100) -> List[Dict]:
    """扫描 work 目录下所有项目，按创建时间倒序。"""
    root = cfg.path("project.work_dir")
    items: List[Dict] = []
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir():
                continue
            f = d / "project.json"
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            # 以目录名为准：项目被复制/改名时，记录里的旧 id 不会串到别的项目
            data["job_id"] = d.name
            data.setdefault("created", "")
            items.append(data)
    items.sort(key=lambda x: x.get("created", ""), reverse=True)
    return items[:limit]


def summary(data: Dict) -> Dict:
    """给项目列表用的精简字段。"""
    return {
        "job_id": data.get("job_id", ""),
        "title": data.get("title") or "未命名",
        "status": data.get("status", ""),
        "created": data.get("created", ""),
        "language": data.get("language", ""),
        "style": data.get("style", ""),
        "duration_hms": data.get("duration_hms", ""),
        "duration_sec": data.get("duration_sec", 0),
        "hooks": len(data.get("hooks") or []),
        "segments": len(data.get("segments") or []),
        "out_dir": data.get("out_dir", ""),
    }


def remove(cfg, job_id: str, keep_output: bool = False) -> Dict:
    """删除一个项目：中间产物 + 项目记录（可选连成品一起删）。

    只删工作区内的默认目录，不碰记录里指向外部的自定义导出目录。
    """
    if not job_id or not re.fullmatch(r"[A-Za-z0-9_\-]+", job_id):
        raise ValueError("非法项目 id")
    work_root = cfg.path("project.work_dir").resolve()
    out_root = cfg.path("project.out_dir").resolve()
    work_dir = (work_root / job_id).resolve()
    out_dir = (out_root / job_id).resolve()
    for d, root in ((work_dir, work_root), (out_dir, out_root)):
        if d == root or not _under(d, root):
            raise ValueError("项目路径越界")
    data = load(cfg, job_id) or {}
    result = {"job_id": job_id, "work_removed": False, "output_removed": False,
              "kept_output": "", "note": ""}

    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
        result["work_removed"] = not work_dir.exists()

    if keep_output:
        result["kept_output"] = str(out_dir)
        result["note"] = "成片保留在 output/ 下"
    elif out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
        result["output_removed"] = not out_dir.exists()

    # 记录里如果有外部导出目录，只提示不删除（避免误删用户目录）
    custom = str(data.get("out_dir") or "")
    if custom and Path(custom).resolve() != out_dir.resolve() and Path(custom).exists():
        result["note"] = ("外部导出目录未删除：%s" % custom)
    return result




def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
