"""配置加载：config.yaml + .env。"""
import os
from pathlib import Path
from typing import Any, Dict

import yaml

from . import i18n

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*a, **k):
        return False

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    "project": {
        "language": "en",
        "work_dir": "work",
        "out_dir": "output",
        "font": "",        # 留空 = 按语言自动
        "font_path": "",   # 留空 = 按语言自动
    },
    "target": {
        "minutes_min": 3.0,
        "minutes_max": 10.0,
        "tolerance": 0.08,
        "chars_per_sec": 5.2,   # 旧字段，仅兼容；实际按 i18n 的 units_per_sec
        "max_rounds": 3,
    },
    "script": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "temperature": 1.15,
        "timeout": 180,
        "style": "auto",           # auto/thriller/romance/revenge/sweet/drama
        "person": "auto",          # auto/first/third
        "hook_strength": "normal", # normal/strong/max
    },
    "tts": {
        "provider": "edge",          # edge | volc | minimax | say
        "voice": "",            # 留空 = 按语言自动
        "rate": "-6%",
        "volume": "+0%",
        "max_workers": 3,
        "retries": 3,
        "cache_dir": ".cache/tts",
        "volc_voice": "zh_female_shuangkuaisisi_moon_bigtts",
        "minimax_voice": "male-qn-qingse",
        "minimax_model": "speech-02-hd",
        "say_voice": "Tingting",
        "say_rate": 175,
    },
    "subtitle": {
        "font_size": 64,
        "margin_v": 660,
        "outline": 4,
        "shadow": 2,
        "primary": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "max_chars_per_line": 0,   # 0 = 按语言自动
        "fade_in_ms": 120,
        "fade_out_ms": 80,
    },
    "render": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "crf": 22,
        "preset": "faster",
        "maxrate": 6000000,
        "bufsize": 12000000,
        "kb_strength": 0.00035,
        "grain": 0,
        "color_delta": 0.04,
        "bgm_volume_db": -30,
        "bgm_path": "",
    },
    "assets": {
        "dir": "assets/clips",
        "db": "assets/assets.db",
        "cooldown_days": 7,
        "min_segment": 6.0,
        "max_segment": 15.0,
        "exclude_categories": ["_synthetic"],
    },
    "pipeline": {
        "chapter_min_sec": 40,
        "chapter_max_sec": 75,
        "offline": False,
    },
    "qc": {
        "blackdetect": True,
        "black_min_duration": 2.0,
        "black_pix_th": 0.05,
        "silencedetect": True,
        "silence_db": -45,
        "silence_min_duration": 2.0,
        "max_asset_reuse": 3,
        "lufs_target": -16,
        "lufs_tolerance": 2.5,
    },
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.root = ROOT

    def __getitem__(self, key: str) -> Dict[str, Any]:
        return self.raw[key]

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        cur = self.raw
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    def path(self, key: str) -> Path:
        """把相对路径配置解析到项目根目录下。"""
        p = Path(str(self.get(key)))
        return p if p.is_absolute() else self.root / p

    @property
    def api_key(self) -> str:
        return (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()

    @property
    def llm_base_url(self) -> str:
        return (os.getenv("LLM_BASE_URL") or self.get("script.base_url")).rstrip("/")

    @property
    def llm_model(self) -> str:
        return os.getenv("LLM_MODEL") or self.get("script.model")


def load(path: Path = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_file = Path(path) if path else (ROOT / "config.yaml")
    base = _merge(DEFAULTS, {})
    if cfg_file.exists():
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        base = _merge(base, raw)
    # 环境变量覆盖
    if os.getenv("TTS_PROVIDER"):
        base["tts"]["provider"] = os.getenv("TTS_PROVIDER")
    if os.getenv("NOVELAUTO_LANG"):
        base["project"]["language"] = os.getenv("NOVELAUTO_LANG")
    cfg = Config(base)
    i18n.apply(cfg)   # 按语言补字体/音色/字幕宽度
    return cfg


CONFIG = None


def get_config(path: Path = None) -> Config:
    global CONFIG
    if CONFIG is None or path is not None:
        CONFIG = load(path)
    return CONFIG
