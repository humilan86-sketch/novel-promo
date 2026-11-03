"""素材库：入库（指纹/时长/分辨率）、去重、按冷却期选片、生成测试素材。"""
import random
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from .config import Config
from .util import ensure_dir, media_info, run

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    duration REAL NOT NULL,
    width INTEGER, height INTEGER,
    phash TEXT,
    category TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    source TEXT DEFAULT '',
    added_at REAL,
    last_used_at REAL DEFAULT 0,
    uses INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_assets_path ON assets(path);
"""


# ------------------------------------------------------------------ 指纹

def dhash(frame_path: Path, size: int = 8) -> str:
    img = Image.open(frame_path).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(img.getdata())
    bits = []
    for row in range(size):
        for col in range(size):
            bits.append("1" if px[row * (size + 1) + col] > px[row * (size + 1) + col + 1] else "0")
    return "%016x" % int("".join(bits), 2)


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 99


def frame_hashes(path: Path, dur: float, n: int = 3) -> List[str]:
    hashes: List[str] = []
    with tempfile.TemporaryDirectory() as td:
        for i, ratio in enumerate([0.1, 0.5, 0.9][:n]):
            ts = max(0.0, dur * ratio)
            out = Path(td) / ("f%d.png" % i)
            try:
                run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % ts, "-i", str(path),
                     "-frames:v", "1", "-vf", "scale=160:-2", str(out)], check=False, timeout=60)
                if out.exists():
                    hashes.append(dhash(out))
            except Exception:
                continue
    return hashes


# ------------------------------------------------------------------ 库

class AssetLibrary:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db_path = cfg.path("assets.db")
        ensure_dir(self.db_path.parent)
        self.conn = sqlite3.connect(str(self.db_path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # --- 入库 ---
    def ingest(self, folder: Optional[Path] = None, category: str = "", emotion: str = "") -> Dict:
        folder = folder or self.cfg.path("assets.dir")
        ensure_dir(folder)
        exts = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".ts"}
        # 跳过成品/中间产物目录，避免把 output/ 里的成片当成素材入库
        skip_dirs = {"output", "work", ".cache", ".venv", "node_modules", "docs"}
        files = []
        for p in folder.rglob("*"):
            if p.suffix.lower() not in exts or not p.is_file():
                continue
            if any(part in skip_dirs for part in p.parts):
                continue
            files.append(p)
        files.sort()
        added, skipped, dup = 0, 0, 0
        for f in files:
            if self.conn.execute("SELECT 1 FROM assets WHERE path=?", (str(f),)).fetchone():
                skipped += 1
                continue
            try:
                info = media_info(f)
            except Exception:
                skipped += 1
                continue
            if info["duration"] < 1.5 or not info["width"]:
                skipped += 1
                continue
            hs = frame_hashes(f, info["duration"])
            if hs and self._is_duplicate(hs):
                dup += 1
                continue
            cat = category or f.parent.name if f.parent != folder else category
            self.conn.execute(
                "INSERT OR IGNORE INTO assets(path,duration,width,height,phash,category,emotion,"
                "source,added_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(f), info["duration"], info["width"], info["height"],
                 hs[0] if hs else "", cat, emotion, folder.name, time.time()),
            )
            added += 1
        self.conn.commit()
        return {"scanned": len(files), "added": added, "skipped": skipped, "duplicates": dup}

    def _is_duplicate(self, hashes: List[str], thresh: int = 4) -> bool:
        rows = self.conn.execute("SELECT phash FROM assets WHERE phash != ''").fetchall()
        for r in rows:
            if any(hamming(h, r["phash"]) <= thresh for h in hashes[:1]):
                return True
        return False

    # --- 查询 ---
    def stats(self) -> Dict:
        r = self.conn.execute("SELECT COUNT(*) n, COALESCE(SUM(duration),0) d FROM assets").fetchone()
        by_cat = self.conn.execute(
            "SELECT category, COUNT(*) n, COALESCE(SUM(duration),0) d FROM assets GROUP BY category"
        ).fetchall()
        excluded = set(self.cfg.get("assets.exclude_categories") or [])
        usable = self.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration),0) d FROM assets WHERE category NOT IN (%s)"
            % ",".join("?" * max(1, len(excluded))),
            tuple(excluded) if excluded else ("",),
        ).fetchone() if excluded else r
        return {"count": r["n"], "total_seconds": round(r["d"], 1),
                "usable_count": usable["n"], "usable_seconds": round(usable["d"], 1),
                "categories": [dict(x) for x in by_cat]}

    def purge_synthetic(self) -> Dict:
        """删除测试用的合成素材（数据库记录 + 文件）。"""
        rows = self.conn.execute(
            "SELECT id, path FROM assets WHERE category='_synthetic'").fetchall()
        removed = 0
        for row in rows:
            p = Path(row["path"])
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
            self.conn.execute("DELETE FROM assets WHERE id=?", (row["id"],))
            removed += 1
        self.conn.commit()
        # 顺手清掉空目录
        try:
            d = self.cfg.path("assets.dir") / "_synthetic"
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass
        return {"removed": removed}

    def list(self, limit: int = 200) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM assets ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- 选片 ---
    def pick(self, needs: List[float], prefer_category: str = "") -> List[Dict]:
        """给每个需要的时长挑素材区间。

        规则：每条片段 ≤ assets.max_segment；素材不够长/不够多时换起点复用；
        最终校验总时长必须覆盖需求，覆盖不了直接报错（否则成片末段会黑屏）。
        """
        all_rows = [dict(r) for r in self.conn.execute("SELECT * FROM assets").fetchall()]
        if not all_rows:
            raise RuntimeError("素材库是空的。先把素材放进 %s 并执行 ingest，"
                               "或用 `python -m novelauto assets synth` 生成测试素材"
                               % self.cfg.get("assets.dir"))
        # 默认排除测试用的合成素材：只要你的真素材够用，就绝不拿渐变占位片凑数
        excluded = set(self.cfg.get("assets.exclude_categories") or [])
        rows = [r for r in all_rows if (r.get("category") or "") not in excluded]
        if not rows:
            rows = all_rows
        cooldown = float(self.cfg.get("assets.cooldown_days")) * 86400
        max_seg = float(self.cfg.get("assets.max_segment") or 15.0)
        max_seg = max(4.0, max_seg)
        now = time.time()

        def score(a: Dict) -> float:
            s = random.random() * 0.3
            s += 1.0 if (prefer_category and a["category"] == prefer_category) else 0.0
            s -= 0.6 if (a["last_used_at"] and now - a["last_used_at"] < cooldown) else 0.0
            s -= min(0.5, a["uses"] * 0.02)
            return s

        clips: List[Dict] = []
        used_in_job = set()
        for need in needs:
            need = float(need)
            remaining = need
            guard = 0
            while remaining > 0.05 and guard < 600:
                guard += 1
                pool = [a for a in rows if a["id"] not in used_in_job] or rows
                pool = sorted(pool, key=score, reverse=True)[: max(1, len(pool) // 2)]
                a = random.choice(pool[:3]) if len(pool) >= 3 else pool[0]
                avail = float(a["duration"]) - 0.4
                if avail <= 0.8:
                    remaining -= 0.8  # 这条素材太短，放弃
                    continue
                take = min(remaining, max_seg, avail)
                start_max = max(0.0, float(a["duration"]) - take - 0.2)
                start = random.uniform(0, start_max) if start_max > 0 else 0.0
                clips.append({
                    "path": a["path"], "start": round(start, 3), "duration": round(take, 3),
                    "asset_id": a["id"], "category": a.get("category", ""),
                    "zoom_dir": random.choice([1, 1, 1, -1]),
                    "kb": round(random.uniform(0.07, 0.16), 4),
                    "mirror": random.random() < 0.5,
                    "eq": [round(random.uniform(-0.03, 0.03), 3),
                           round(1 + random.uniform(-0.04, 0.04), 3),
                           round(1 + random.uniform(-0.05, 0.05), 3)],
                })
                used_in_job.add(a["id"])
                remaining -= take

        need_total = sum(float(n) for n in needs)
        got_total = sum(float(c["duration"]) for c in clips)
        if got_total < need_total - 0.6:
            raise RuntimeError(
                "素材总时长不足：需要 %.1fs，只能凑出 %.1fs。请补充素材（当前 %d 条 / %.1f 秒）"
                % (need_total, got_total, len(rows), sum(float(r["duration"]) for r in rows)))
        return clips

    def mark_used(self, clips: List[Dict]) -> None:
        ids = {c["asset_id"] for c in clips if c.get("asset_id")}
        now = time.time()
        for i in ids:
            self.conn.execute("UPDATE assets SET last_used_at=?, uses=uses+1 WHERE id=?", (now, i))
        self.conn.commit()


# ------------------------------------------------------------------ 测试素材

def synth_placeholders(cfg: Config, count: int = 3, seconds: float = 90) -> List[Path]:
    """造几条合成素材，方便在没有素材时跑通全流程 / 做冒烟测试。"""
    out_dir = ensure_dir(cfg.path("assets.dir") / "_synthetic")
    patterns = [
        ("gradients=s=1080x1920:n=4:c0=0x2b1b3d:c1=0x0f0f14:c2=0x3d2440:c3=0x101018:speed=0.006,gblur=sigma=36", "紫夜"),
        ("gradients=s=1080x1920:n=4:c0=0x123a4b:c1=0x05121a:c2=0x1c4a5e:c3=0x02080d:speed=0.005,gblur=sigma=36", "深海"),
        ("gradients=s=1080x1920:n=4:c0=0x3a2a12:c1=0x120c05:c2=0x4a3518:c3=0x0a0703:speed=0.007,gblur=sigma=36", "暖棕"),
        ("gradients=s=1080x1920:n=4:c0=0x1a2b1e:c1=0x060a07:c2=0x2a3d2c:c3=0x040605:speed=0.006,gblur=sigma=36", "苔绿"),
    ]
    made: List[Path] = []
    for i in range(count):
        src, name = patterns[i % len(patterns)]
        out = out_dir / ("synth_%02d_%s.mp4" % (i + 1, name))
        run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src,
            "-t", "%.1f" % seconds, "-r", "30",
            "-vf", "eq=brightness=-0.04:saturation=1.05",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p",
            str(out),
        ], timeout=300)
        made.append(out)
    return made
