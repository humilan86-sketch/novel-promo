"""配音引擎：可切换 Provider，逐句合成 + 缓存 + 并发 + 重试。

支持：
- edge  : 微软 edge-tts（默认，免费，无需 Key）
- say   : macOS 自带发音人（完全离线，无网络时兜底）
- volc  : 火山引擎豆包语音（需 VOLC_APPID / VOLC_TOKEN / VOLC_CLUSTER）
- minimax: MiniMax speech（需 MINIMAX_API_KEY / MINIMAX_GROUP_ID）
"""
import asyncio
import base64
import json
import os
import random
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

import httpx

from .config import Config
from .util import ensure_dir, sha1

try:
    import edge_tts
except Exception:  # pragma: no cover
    edge_tts = None


class TTSProvider:
    name = "base"
    ext = "mp3"
    # 不同 provider 真正影响音色的配置项，必须进缓存 key，否则换音色会命中旧音频
    SIG_KEYS = {"edge": ["tts.voice", "tts.rate", "tts.volume"],
                "say": ["tts.say_voice", "tts.say_rate"],
                "volc": ["tts.volc_voice", "tts.rate"],
                "minimax": ["tts.minimax_voice", "tts.minimax_model", "tts.rate"]}

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def synth(self, text: str, out: Path) -> None:
        raise NotImplementedError

    def cache_key(self, text: str) -> str:
        keys = self.SIG_KEYS.get(self.name, ["tts.voice", "tts.rate"])
        parts = [self.name] + [str(self.cfg.get(k)) for k in keys] + [text]
        return sha1("|".join(parts))


class EdgeTTS(TTSProvider):
    name = "edge"

    def synth(self, text: str, out: Path) -> None:
        if edge_tts is None:
            raise RuntimeError("未安装 edge-tts，请先 pip install edge-tts")
        rate = str(self.cfg.get("tts.rate"))
        volume = str(self.cfg.get("tts.volume"))
        voice = str(self.cfg.get("tts.voice"))

        async def _go():
            comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
            await comm.save(str(out))

        asyncio.run(_go())


class SayTTS(TTSProvider):
    """macOS 离线兜底。"""
    name = "say"
    ext = "aiff"

    def synth(self, text: str, out: Path) -> None:
        voice = str(self.cfg.get("tts.say_voice"))
        rate = int(self.cfg.get("tts.say_rate"))
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(out), text],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class VolcTTS(TTSProvider):
    name = "volc"

    def synth(self, text: str, out: Path) -> None:
        appid = os.getenv("VOLC_APPID", "").strip()
        token = os.getenv("VOLC_TOKEN", "").strip()
        cluster = os.getenv("VOLC_CLUSTER", "volcano_tts").strip()
        if not (appid and token):
            raise RuntimeError("缺少 VOLC_APPID / VOLC_TOKEN")
        body = {
            "app": {"appid": appid, "token": token, "cluster": cluster},
            "user": {"uid": "novelauto"},
            "audio": {
                "voice_type": str(self.cfg.get("tts.volc_voice")),
                "encoding": "mp3",
                "speed_ratio": 1.0 + float(str(self.cfg.get("tts.rate")).replace("%", "")) / 100.0,
            },
            "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"},
        }
        r = httpx.post(
            "https://openspeech.bytedance.com/api/v1/tts",
            headers={"Authorization": "Bearer;" + token, "Content-Type": "application/json"},
            json=body, timeout=60,
        )
        data = r.json()
        if data.get("code") not in (0, 3000):
            raise RuntimeError("火山 TTS 失败: %s" % json.dumps(data, ensure_ascii=False)[:300])
        out.write_bytes(base64.b64decode(data["data"]))


class MiniMaxTTS(TTSProvider):
    name = "minimax"

    def synth(self, text: str, out: Path) -> None:
        key = os.getenv("MINIMAX_API_KEY", "").strip()
        gid = os.getenv("MINIMAX_GROUP_ID", "").strip()
        if not (key and gid):
            raise RuntimeError("缺少 MINIMAX_API_KEY / MINIMAX_GROUP_ID")
        speed = 1.0 + float(str(self.cfg.get("tts.rate")).replace("%", "")) / 100.0
        body = {
            "model": str(self.cfg.get("tts.minimax_model")),
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": str(self.cfg.get("tts.minimax_voice")),
                              "speed": round(speed, 3), "vol": 1, "pitch": 0},
            "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000},
        }
        r = httpx.post(
            "https://api.minimax.chat/v1/t2a_v2?GroupId=" + gid,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json=body, timeout=120,
        )
        data = r.json()
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise RuntimeError("MiniMax TTS 失败: %s" % json.dumps(data, ensure_ascii=False)[:300])
        out.write_bytes(bytes.fromhex(audio_hex))


PROVIDERS = {"edge": EdgeTTS, "say": SayTTS, "volc": VolcTTS, "minimax": MiniMaxTTS}


def rate_pct(cfg) -> int:
    """把 config 里的 "-6%" 解析成 -6（整数百分比）。"""
    raw = str(cfg.get("tts.rate") or "0")
    try:
        return int(round(float(raw.replace("%", "").replace("+", ""))))
    except Exception:
        return 0


def clamp_rate(pct: int) -> int:
    return max(-30, min(30, int(pct)))


class TTSFailed(RuntimeError):
    """部分句子合成失败：failed = {idx: 错误信息}，其余句子已成功。"""

    def __init__(self, failed: dict):
        self.failed = failed
        super().__init__("%d 句配音失败: %s" % (len(failed), list(failed.values())[:3]))


class TTS:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        name = str(cfg.get("tts.provider", "edge"))
        if name not in PROVIDERS:
            raise ValueError("未知 TTS provider: %s（可选 %s）" % (name, ", ".join(PROVIDERS)))
        self.provider = PROVIDERS[name](cfg)
        self.cache_dir = ensure_dir(cfg.path("tts.cache_dir"))
        self.retries = int(cfg.get("tts.retries"))
        self.workers = int(cfg.get("tts.max_workers"))

    # 单句合成（带缓存）
    def synth_one(self, text: str, tag: str = "") -> Path:
        out = self.cache_dir / (self.provider.cache_key(text)[:24] + "." + self.provider.ext)
        if out.exists() and out.stat().st_size > 1024:
            return out
        tmp = out.with_name(out.stem + "." + uuid.uuid4().hex + ".part" + out.suffix)
        last = None
        for attempt in range(self.retries):
            try:
                self.provider.synth(text, tmp)
                if tmp.exists() and tmp.stat().st_size > 1024:
                    os.replace(tmp, out)
                    return out
                raise RuntimeError("音频文件为空")
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.6 * (attempt + 1) + random.random() * 0.3)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
        raise RuntimeError("TTS 合成失败（%s）: %s | 文本: %s" % (self.provider.name, last, text[:40]))

    def synth_all(self, lines: List[str], progress=None) -> List[Path]:
        results: List[Optional[Path]] = [None] * len(lines)
        failed: Dict[int, str] = {}
        done = [0]
        lock = __import__("threading").Lock()

        def work(i: int) -> None:
            try:
                results[i] = self.synth_one(lines[i])
            except Exception as e:  # noqa: BLE001
                failed[i] = str(e)[:120]
            with lock:
                done[0] += 1
                if progress:
                    progress(done[0], len(lines))

        if self.workers <= 1:
            for i in range(len(lines)):
                work(i)
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                list(ex.map(work, range(len(lines))))
        if failed:
            raise TTSFailed(failed)
        return [p for p in results if p is not None]
