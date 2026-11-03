"""剧本改写引擎（多语言）：黄金三秒 3 版 + 章回钩子 + 时长收敛 + 发布文案。

- 提示词、文本水池、字数/词数换算都走 i18n 模块，中/英共用一套流程
- LLM 不可用时自动进入离线模式（degraded），流程仍能跑通
"""
import json
import random
import re
from typing import Dict, List, Optional

import httpx

from . import i18n
from .config import Config
from .tts import rate_pct
from .util import log

# 兼容旧引用
BRIDGE_POOL = i18n.BRIDGES["zh"]


# ---------------------------------------------------------------- 文本工具

_SENT_RE = re.compile(r"[^。！？!?；;\n…]+[。！？!?；;…]*")
_SENT_RE_EN = re.compile(r"[^.!?\n]+[.!?]*")
_FULLWIDTH = {"，": "，", "。": "。", "！": "！", "？": "？", "：": "：", "；": "；",
              ",": "，", "!": "！", "?": "？", ";": "；", ":": "："}
_ABBR = ("Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr.", "vs.", "e.g.", "i.e.")


def split_sentences(text: str, lang: str = "zh") -> List[str]:
    text = text.replace("\r", "\n").replace("\u3000", " ")
    out: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        regex = _SENT_RE_EN if i18n.is_space_lang(lang) else _SENT_RE
        for m in regex.finditer(line):
            s = m.group(0).strip()
            if not s:
                continue
            if i18n.is_space_lang(lang):
                if len(s) < 3:
                    continue
                if s in _ABBR or s.endswith(_ABBR):
                    out.append(s)
                    continue
                out.append(re.sub(r"\s+", " ", s))
            else:
                out.append(_polish_inline(s))
    return out


def _polish_inline(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = s.replace("……", "…").replace("——", "—")
    for k, v in _FULLWIDTH.items():
        s = s.replace(k, v)
    return s.strip()


def normalize_for_tts(text: str, lang: str = "zh") -> str:
    return i18n.normalize_tts(text, lang)


def group_lines(sentences: List[str], max_chars: int, lang: str = "zh") -> List[str]:
    """把句子合成适合单屏字幕的行；英文按词切，避免切断单词。"""
    if i18n.is_space_lang(lang):
        return _group_words(sentences, max_chars)
    lines: List[str] = []
    buf = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        while len(s) > max_chars:
            if buf:
                lines.append(buf)
                buf = ""
            cut = _best_cut(s, max_chars)
            lines.append(s[:cut])
            s = s[cut:]
        if not s:
            continue
        if not buf:
            buf = s
        elif len(buf) + len(s) <= max_chars:
            buf += s
        else:
            lines.append(buf)
            buf = s
    if buf:
        lines.append(buf)
    return [l.strip() for l in lines if l.strip()]


def _group_words(sentences: List[str], max_chars: int) -> List[str]:
    lines: List[str] = []
    buf = ""
    for s in sentences:
        s = re.sub(r"\s+", " ", s.strip())
        if not s:
            continue
        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_chars:
            buf += " " + s
        else:
            lines.append(buf)
            buf = s
        while len(buf) > max_chars * 1.5:
            cut = buf.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            lines.append(buf[:cut].strip())
            buf = buf[cut:].strip()
    if buf:
        lines.append(buf)
    return lines


def _best_cut(text: str, limit: int) -> int:
    window = text[:limit]
    for i in range(len(window) - 1, max(2, limit // 3), -1):
        if window[i] in "，、；：…—":
            return i + 1
    return limit


# ---------------------------------------------------------------- LLM 客户端

class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.key = cfg.api_key
        self.base = cfg.llm_base_url
        self.model = cfg.llm_model

    @property
    def available(self) -> bool:
        return bool(self.key)

    def chat_json(self, system: str, user: str, temperature: Optional[float] = None,
                  retries: int = 2) -> Dict:
        if not self.available:
            raise RuntimeError("没有配置 LLM_API_KEY，无法调用模型")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.cfg.get("script.temperature") if temperature is None else temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": 8000,
        }
        last = None
        for _ in range(retries + 1):
            try:
                r = httpx.post(
                    self.base + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.key,
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=float(self.cfg.get("script.timeout")),
                )
                if r.status_code >= 400:
                    raise RuntimeError("LLM HTTP %s: %s" % (r.status_code, r.text[:300]))
                content = r.json()["choices"][0]["message"]["content"]
                return _loads_json(content)
            except Exception as e:  # noqa: BLE001
                last = e
                continue
        raise RuntimeError("LLM 调用失败: %s" % last)


def _loads_json(content: str) -> Dict:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("LLM 返回的不是 JSON 对象")
        return data
    except Exception:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(content[start:end + 1])
            if not isinstance(data, dict):
                raise ValueError("LLM 返回的不是 JSON 对象")
            return data
        raise


def _parse_hooks(pack: Dict) -> List[Dict]:
    hooks: List[Dict] = []
    raw = pack.get("hooks")
    if isinstance(raw, list):
        for item in raw[:3]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                hooks.append({"type": str(item.get("type") or ""), "text": text})
    if not hooks:
        legacy = str(pack.get("hook_3s") or "").strip()
        if legacy:
            hooks.append({"type": "", "text": legacy})
    return hooks


def _split_chunks(text: str, size: int) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + len(p) < size:
            buf += "\n" + p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    out: List[str] = []
    for c in chunks:
        if len(c) <= size * 1.5:
            out.append(c)
        else:
            for i in range(0, len(c), size):
                out.append(c[i:i + size])
    return out


# ---------------------------------------------------------------- 主类

class ScriptWriter:
    def __init__(self, cfg: Config, lang: Optional[str] = None):
        self.cfg = cfg
        self.lang = lang or i18n.lang_of(cfg)
        self.llm = LLM(cfg)

    # ------------------------------------------------------------ 离线兜底
    def rewrite_offline(self, source: str) -> Dict:
        lang = self.lang
        m = i18n.LANGS[lang]
        sentences = split_sentences(source, lang)
        lines = group_lines(sentences, m["subtitle_max_chars"] * 2, lang)
        if not lines:
            raise ValueError("原文没有可用句子")
        hook = self._pick_hook(sentences) or lines[0]
        lines[0] = hook
        items = [{"text": t, "role": "body"} for t in lines]
        items[0]["role"] = "hook"
        hooks = self._offline_hooks(sentences, lines[0])
        items[0]["text"] = hooks[0]["text"]
        items = self._insert_bridges(items, i18n.bridges(lang)[:2])
        ending = random.choice(i18n.endings(lang))
        items.append({"text": ending, "role": "ending"})
        return {
            "segments": [{"text": normalize_for_tts(x["text"], lang), "role": x["role"]}
                         for x in items if x["text"].strip()],
            "ending": ending,
            "bridges": i18n.bridges(lang)[:2],
            "hooks": hooks,
            "publish": self._offline_publish(hooks[0]["text"] if hooks else lines[0]),
            "degraded": True,
        }

    def _pick_hook(self, sentences: List[str]) -> str:
        markers = i18n.hook_markers(self.lang)
        space = i18n.is_space_lang(self.lang)
        limit = 140 if space else 30
        cands = [s for s in sentences[: max(3, len(sentences) // 3)] if len(s) <= limit]
        scored = []
        for s in cands:
            low = s.lower() if space else s
            scored.append((sum(1 for m in markers if m in low), s))
        scored.sort(key=lambda x: -x[0])
        if scored and scored[0][0] > 0:
            return scored[0][1]
        return sentences[0] if sentences else ""

    def _offline_hooks(self, sentences: List[str], first: str) -> List[Dict]:
        types = i18n.LANGS[self.lang]["hook_types"]
        space = i18n.is_space_lang(self.lang)
        cands = [x for x in sentences[: max(6, len(sentences) // 3)]
                 if (20 <= len(x) <= 140 if space else 8 <= len(x) <= 26)]
        pool = [first] + [c for c in cands if c != first]
        out: List[Dict] = []
        for i in range(3):
            base = pool[i] if i < len(pool) else first
            out.append({"type": types[i] if i < len(types) else "", "text": base})
        return out

    def _insert_bridges(self, items: List[Dict], bridges: List[str]) -> List[Dict]:
        if len(items) < 6 or not bridges:
            return items
        total = sum(len(x["text"]) for x in items)
        targets = [total * 0.34, total * 0.68]
        out: List[Dict] = []
        acc = 0
        bi = 0
        for item in items:
            out.append(item)
            acc += len(item["text"])
            while bi < len(targets) and acc >= targets[bi]:
                if bi < len(bridges):
                    out.append({"text": bridges[bi], "role": "bridge"})
                bi += 1
        return out

    def _offline_publish(self, hook: str) -> Dict:
        lang = self.lang
        space = i18n.is_space_lang(lang)
        tpl = i18n.OFFLINE_PUBLISH.get(lang, i18n.OFFLINE_PUBLISH["en"])
        if space:
            words = hook.replace("\n", " ").split()
            clause = " ".join(words[:8]).rstrip(",.")
        else:
            clause = (re.split(r"[，。！？…、；：]", hook)[0] or hook).strip()[:12]
        genre = i18n.genre_of(hook, lang)
        tags = i18n.hashtags(lang).get(genre or "default") or i18n.hashtags(lang)["default"]
        return {
            "title_candidates": [t % clause for t in tpl["title"]],
            "title": (tpl["title"][0] % clause),
            "hashtags": tags[:4],
            "pinned_comment": tpl["pinned"],
            "cover_text": clause + "\\n" + tpl["cover2"],
            "genre": genre or "drama",
        }

    # ------------------------------------------------------------ LLM 版本
    def rewrite(self, source: str, target_sec: float) -> Dict:
        if not self.llm.available or self.cfg.get("pipeline.offline"):
            return self.rewrite_offline(source)

        m = i18n.LANGS[self.lang]
        units_per_sec = float(m["units_per_sec"])
        # 语速越慢，同样时长需要的字/词越少（-10% → 预算 ×0.9）
        rate_factor = 1.0 + rate_pct(self.cfg) / 100.0
        # 英文模型习惯性写短，预算上浮 8% 抵消
        boost = 1.08 if i18n.is_space_lang(self.lang) else 1.0
        target_units = int(target_sec * units_per_sec * rate_factor * boost)
        chunk_size = 700 if not i18n.is_space_lang(self.lang) else 2400
        chunks = _split_chunks(source, chunk_size)

        directive = i18n.directive(self.cfg, self.lang)
        system_rewrite = i18n.SYSTEM[self.lang]["rewrite"]
        if directive:
            system_rewrite = system_rewrite + "\n\nSTYLE REQUIREMENTS:\n" + directive

        lines: List[str] = []
        prev_tail = ""
        for i, chunk in enumerate(chunks, 1):
            share = max(30, int(target_units * len(chunk) / max(1, len(source))))
            try:
                data = self.llm.chat_json(
                    system_rewrite,
                    i18n.rewrite_prompt(self.cfg, chunk, share, prev_tail, i, len(chunks)),
                )
                got = [normalize_for_tts(str(x), self.lang) for x in data.get("lines", []) if str(x).strip()]
            except Exception as e:  # noqa: BLE001
                log("[warn] LLM 改写第 %d 块失败，降级离线处理: %s" % (i, e))
                got = group_lines(split_sentences(chunk, self.lang),
                                  m["subtitle_max_chars"] * 2, self.lang)
            if not got:
                continue
            lines.extend(got)
            prev_tail = " ".join(got[-2:]) if i18n.is_space_lang(self.lang) else "".join(got[-2:])

        if not lines:
            return self.rewrite_offline(source)

        pack: Dict = {}
        try:
            genre_hint = i18n.style_genre_hint(self.cfg, self.lang) or i18n.genre_of(source, self.lang) or ""
            hook_system = i18n.SYSTEM[self.lang]["hook"]
            if directive:
                hook_system = hook_system + "\n\nSTYLE REQUIREMENTS:\n" + directive
            pack = self.llm.chat_json(
                hook_system,
                i18n.hook_pack_prompt(self.cfg, lines[:6], lines, genre_hint),
            )
        except Exception as e:  # noqa: BLE001
            log("[warn] 钩子包生成失败，使用离线模板: %s" % e)

        hooks = _parse_hooks(pack) or [{"type": "", "text": lines[0]}]
        lines[0] = hooks[0]["text"]
        bridge_pack = pack.get("bridges") if isinstance(pack.get("bridges"), list) else []
        bridges = ([str(b).strip() for b in bridge_pack if str(b).strip()][:3]
                   or i18n.bridges(self.lang)[:2])
        ending = str(pack.get("ending") or "").strip() or random.choice(i18n.endings(self.lang))
        if not lines or lines[-1] != ending:
            lines.append(ending)

        items = [{"text": t, "role": "hook" if i == 0 else "body"} for i, t in enumerate(lines)]
        items = self._insert_bridges(items, bridges)
        publish = pack.get("publish")
        if not isinstance(publish, dict):
            publish = self._offline_publish(lines[0])

        return {
            "segments": [{"text": normalize_for_tts(x["text"], self.lang), "role": x["role"]}
                         for x in items if x["text"].strip()],
            "ending": ending,
            "bridges": bridges,
            "hooks": hooks,
            "publish": publish,
            "degraded": False,
        }

    # ------------------------------------------------------------ 时长收敛
    def adjust(self, lines: List[str], ratio: float,
               bridges: Optional[List[str]] = None) -> List[str]:
        ratio = max(0.5, min(1.8, ratio))
        if abs(ratio - 1) < 0.03:
            return lines
        got = None
        if self.llm.available and not self.cfg.get("pipeline.offline"):
            try:
                data = self.llm.chat_json(
                    i18n.SYSTEM[self.lang]["adjust"],
                    i18n.adjust_prompt(self.cfg, lines, ratio), temperature=0.7)
                got = [normalize_for_tts(str(x), self.lang) for x in data.get("lines", []) if str(x).strip()]
            except Exception as e:  # noqa: BLE001
                log("[warn] 时长调整失败，使用本地策略: %s" % e)
        if not got:
            got = self._local_adjust(lines, ratio)
        return self._ensure_bridges(got, bridges)

    def _ensure_bridges(self, lines: List[str],
                        bridges: Optional[List[str]] = None) -> List[str]:
        use = [b for b in (bridges or []) if b] or i18n.bridges(self.lang)[:2]
        known = set(i18n.bridges(self.lang)) | set(use)
        base = [l for l in lines if l.strip() and l not in known]
        if len(base) < 8:
            return base
        items = [{"text": t, "role": "hook" if i == 0 else "body"} for i, t in enumerate(base)]
        items = self._insert_bridges(items, use[:3])
        return [x["text"] for x in items]

    @staticmethod
    def _local_adjust(lines: List[str], ratio: float) -> List[str]:
        n = len(lines)
        keep = max(4, int(round(n * ratio)))
        if keep < n:
            idxs = list(range(1, n - 1))
            drop = set(idxs[::max(1, len(idxs) // max(1, n - keep))][: n - keep])
            return [l for i, l in enumerate(lines) if i not in drop]
        return lines
