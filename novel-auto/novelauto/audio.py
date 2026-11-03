"""音频时间轴：以「逐句音频真实时长」为唯一时间真源。

流程：逐句合成 -> 解码成 48k 单声道 PCM -> 去首尾静音 -> 按句间停顿拼成 master.wav
      -> 得到毫秒级精确时间轴 -> 按时间轴切章节、切字幕、切画面。
"""
import wave
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .config import Config
from .util import ensure_dir, run

SR = 48000


def decode_pcm(path: Path) -> np.ndarray:
    """任意音频 -> 48k / mono / int16 numpy。"""
    p = run([
        "ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le",
        "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SR), "-",
    ])
    return np.frombuffer(p.stdout, dtype=np.int16)


def trim_silence(x: np.ndarray, thresh: int = 300, keep_ms: int = 40) -> np.ndarray:
    """去掉句子音频首尾的安静段，让字幕起止更贴嘴。"""
    if x.size == 0:
        return x
    loud = np.abs(x) > thresh
    if not loud.any():
        return x
    idx = np.flatnonzero(loud)
    keep = int(SR * keep_ms / 1000)
    start = max(0, idx[0] - keep)
    end = min(x.size, idx[-1] + keep)
    return x[start:end]


def silence(sec: float) -> np.ndarray:
    return np.zeros(int(SR * sec), dtype=np.int16)


def write_wav(path: Path, arr: np.ndarray) -> None:
    ensure_dir(path.parent)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(arr.astype(np.int16).tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        return np.frombuffer(w.readframes(n), dtype=np.int16)


def build_master(audio_files: List[Path], segs: List[Dict]) -> Tuple[np.ndarray, List[Dict]]:
    """把逐句音频拼成 master，返回 (pcm, 带精确时间的 segments)。"""
    parts: List[np.ndarray] = []
    timeline: List[Dict] = []
    cursor = 0  # 采样点
    for f, seg in zip(audio_files, segs):
        pcm = trim_silence(decode_pcm(f))
        start = cursor
        parts.append(pcm)
        cursor += pcm.size
        end = cursor
        timeline.append({
            "text": seg.get("display_text", seg.get("text", "")),
            "tts_text": seg.get("text", ""),
            "role": seg.get("role", "body"),
            "start": round(start / SR, 3),
            "end": round(end / SR, 3),
        })
        pause = float(seg.get("pause_after", 0.15))
        if pause > 0:
            parts.append(silence(pause))
            cursor += int(SR * pause)
    # 结尾留 0.8s
    parts.append(silence(0.8))
    master = np.concatenate(parts) if parts else np.zeros(SR, dtype=np.int16)
    return master, timeline


def split_chapters(timeline: List[Dict], *, cmin: float, cmax: float) -> List[Dict]:
    """把句子分组为章节：优先落在 cmin~cmax 之间、且尽量接近理想长度。

    保证：除单句本身超长外，每章 duration <= cmax；末章过短并入上一章。
    """
    if not timeline:
        return []
    ideal = min(cmax, max(cmin, (cmin + cmax) / 2.0))
    chapters: List[Dict] = []
    i, n = 0, len(timeline)
    while i < n:
        start = timeline[i]["start"]
        best_j, best_diff = None, 1e9
        j = i
        while j < n:
            dur = timeline[j]["end"] - start
            if dur > cmax and j > i:
                break
            if dur >= cmin:
                diff = abs(dur - ideal)
                if diff < best_diff:
                    best_j, best_diff = j, diff
            j += 1
        if best_j is None:
            # 没有一句能凑够 cmin：尽量多拿几句，但不超过 cmax
            j = i
            while j + 1 < n and timeline[j + 1]["end"] - start <= cmax:
                j += 1
            best_j = j
        chapters.append(_pack_chapter(len(chapters) + 1, timeline[i:best_j + 1]))
        i = best_j + 1

    # 末章太短就并入上一章
    if len(chapters) >= 2 and chapters[-1]["duration"] < cmin * 0.6:
        last = chapters.pop()
        merged = chapters[-1]["segments"] + last["segments"]
        chapters[-1] = _pack_chapter(chapters[-1]["idx"], merged)

    # 重新计算相对时间；章节 end 延伸到下一章开头，避免把句间停顿切掉
    for k, ch in enumerate(chapters):
        base = ch["segments"][0]["start"]
        for s in ch["segments"]:
            s["rel_start"] = round(s["start"] - base, 3)
            s["rel_end"] = round(s["end"] - base, 3)
        ch["start"] = ch["segments"][0]["start"]
        if k + 1 < len(chapters):
            end = chapters[k + 1]["start"]
        else:
            end = timeline[-1]["end"] + 0.8  # 结尾 0.8s 尾巴
        ch["end"] = round(end, 3)
        ch["duration"] = round(ch["end"] - ch["start"], 3)
    return chapters


def _pack_chapter(idx: int, segs: List[Dict]) -> Dict:
    return {"idx": idx, "segments": segs, "start": segs[0]["start"], "end": segs[-1]["end"],
            "duration": round(segs[-1]["end"] - segs[0]["start"], 3)}


def slice_wav(master: np.ndarray, start: float, end: float, out: Path) -> Path:
    a = max(0, int(start * SR))
    b = min(master.size, int(end * SR))
    write_wav(out, master[a:b])
    return out


def loudnorm(src: Path, dst: Path, lufs: float = -16.0) -> Path:
    """单遍 loudnorm 到目标响度（章节级，避免整条被压得忽大忽小）。"""
    run([
        "ffmpeg", "-v", "error", "-y", "-i", str(src),
        "-af", "loudnorm=I=%.1f:TP=-1.5:LRA=11" % lufs,
        "-ar", str(SR), "-ac", "1", str(dst),
    ])
    return dst
