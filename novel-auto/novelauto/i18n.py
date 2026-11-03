"""多语言支持：语言参数、提示词、文本水池、字数/词数换算。

新增一门语言只需要往 LANGS 里加一项（en / zh 已内置；es / pt 可照抄 en 改文案）。
"""
import re
from typing import Dict, List

LANGS: Dict[str, Dict] = {
    "zh": {
        "label": "中文",
        "space_lang": False,
        "units_per_sec": 5.2,      # 汉字/秒
        "unit_name": "字",
        "min_units_per_min": 260,  # 每分钟成片大约需要多少字
        "subtitle_max_chars": 16,
        "font": "Hiragino Sans GB",
        "font_path": "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "default_voice": "zh-CN-YunxiNeural",
        "voices": [
            ("zh-CN-YunxiNeural", "云希 · 男声（故事感，推荐）"),
            ("zh-CN-YunjianNeural", "云健 · 男声（低沉，悬疑）"),
            ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声（温柔）"),
            ("zh-CN-XiaoyiNeural", "晓伊 · 女声（年轻）"),
        ],
        "cover_tip": "全文在评论区置顶",
        "hook_types": ["悬念", "冲突", "倒叙"],
    },
    "en": {
        "label": "English",
        "space_lang": True,
        "units_per_sec": 3.0,      # 英文单词/秒（实测约 180 wpm 叙述语速）
        "unit_name": "words",
        "min_units_per_min": 170,  # 每分钟约 170 词
        "subtitle_max_chars": 38,
        "font": "Avenir Next",
        "font_path": "/System/Library/Fonts/Avenir Next.ttc",
        "default_voice": "en-US-AndrewNeural",
        "voices": [
            ("en-US-AndrewNeural", "Andrew · Male (warm, storytelling — recommended)"),
            ("en-US-ChristopherNeural", "Christopher · Male (deep, authority — suspense)"),
            ("en-US-BrianNeural", "Brian · Male (casual, sincere)"),
            ("en-US-JennyNeural", "Jenny · Female (friendly, warm)"),
            ("en-US-AriaNeural", "Aria · Female (confident, novel)"),
            ("en-GB-SoniaNeural", "Sonia · Female UK (elegant)"),
        ],
        "cover_tip": "Full story in the comments",
        "hook_types": ["Suspense", "Conflict", "Flashback"],
    },
}

SPACE_LANGS = {"en", "es", "pt", "fr", "de"}


def lang_of(cfg) -> str:
    l = str(cfg.get("project.language") or "en").lower()
    return l if l in LANGS else "en"


def meta(cfg) -> Dict:
    return LANGS[lang_of(cfg)]


def is_space_lang(l: str) -> bool:
    return l in SPACE_LANGS


# ---------------------------------------------------------------- 单位换算

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def count_units(text: str, lang: str) -> int:
    if is_space_lang(lang):
        return len(_WORD_RE.findall(text))
    return len(_HAN_RE.findall(text))


def units_for_seconds(seconds: float, lang: str) -> int:
    return int(seconds * LANGS[lang]["units_per_sec"])


def truncate(text: str, max_units: int, lang: str) -> str:
    """按单位（字/词）截断，尽量停在句子边界。"""
    if count_units(text, lang) <= max_units:
        return text
    if is_space_lang(lang):
        pos = 0
        seen = 0
        for m in _WORD_RE.finditer(text):
            seen += 1
            pos = m.end()
            if seen >= max_units:
                break
        window = text[:pos]
        for sep in (".", "!", "?", "\n"):
            cut = window.rfind(sep)
            if cut > len(text) * 0.4:
                return text[:cut + 1]
        return window
    seen = 0
    for i, ch in enumerate(text):
        if "\u4e00" <= ch <= "\u9fff":
            seen += 1
        if seen >= max_units:
            cut = max(text.rfind("\n", 0, i), text.rfind("。", 0, i),
                      text.rfind("！", 0, i), text.rfind("？", 0, i))
            return text[: cut + 1 if cut > i * 0.6 else i]
    return text


# ---------------------------------------------------------------- 文本规整

def normalize_tts(text: str, lang: str) -> str:
    t = text.strip()
    t = re.sub(r"[*#>`_\[\]()（）]", "", t)
    if is_space_lang(lang):
        t = t.replace("&", " and ").replace("%", " percent").replace("+", " plus ")
        t = re.sub(r"\s+", " ", t)
        return t.strip()
    t = t.replace("&", "和").replace("@", "艾特")
    t = re.sub(r"(\d)%", r"\1百分之", t)
    t = re.sub(r"%", "百分之", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ---------------------------------------------------------------- 语言应用

def apply(cfg) -> None:
    """把语言的默认字体/音色/字幕宽度写进 config（用户显式设置的优先）。"""
    l = lang_of(cfg)
    m = LANGS[l]
    if not str(cfg.get("project.font") or "").strip():
        cfg.set("project.font", m["font"])
    if not str(cfg.get("project.font_path") or "").strip():
        cfg.set("project.font_path", m["font_path"])
    if not str(cfg.get("tts.voice") or "").strip():
        cfg.set("tts.voice", m["default_voice"])
    if not int(cfg.get("subtitle.max_chars_per_line") or 0):
        cfg.set("subtitle.max_chars_per_line", m["subtitle_max_chars"])
    if not int(cfg.get("subtitle.font_size") or 0):
        cfg.set("subtitle.font_size", 64)


# ---------------------------------------------------------------- 文本水池

BRIDGES = {
    "zh": [
        "可接下来发生的事，才真叫离谱。",
        "你以为这就完了？这才刚开始。",
        "真正的麻烦，是从第二天早上开始的。",
        "直到那天晚上，我才明白这一切都不是巧合。",
        "而这一切的答案，藏在一个我从来不敢打开的抽屉里。",
        "如果时间能倒回这一刻，我一定会做不一样的选择。",
        "但当时我并不知道，这个决定会毁掉我全部的生活。",
    ],
    "en": [
        "But what happened next was worse.",
        "You think that's it? That was just the beginning.",
        "The real trouble started the next morning.",
        "And then I found the one thing I was never supposed to see.",
        "If I could go back to that moment, I'd choose differently.",
        "But I didn't know yet what that decision would cost me.",
        "That's when everything I believed about this family fell apart.",
    ],
}

ENDINGS = {
    "zh": [
        "而那个站在门口的人，我这辈子都不会忘记他的脸。",
        "我抬起头，看见的却是一张本不该出现在这里的脸。",
        "手机在这时候响了，屏幕上显示的名字，让我整个人僵在原地。",
    ],
    "en": [
        "I looked up, and the face in the doorway was not supposed to be there.",
        "Then my phone lit up, and the name on the screen froze me in place.",
        "And the person standing there was someone I had buried three years ago.",
    ],
}

HASHTAGS = {
    "zh": {
        "default": ["#小说推文", "#解压视频", "#asmr", "#故事", "#有声小说"],
        "言情": ["#言情小说", "#虐文推荐", "#小说推文"],
        "悬疑": ["#悬疑小说", "#细思极恐", "#小说推文"],
        "爽文": ["#爽文推荐", "#逆袭", "#小说推文"],
    },
    "en": {
        "default": ["#novel", "#storytime", "#satisfying", "#asmr", "#booktok"],
        "romance": ["#romancebook", "#booktok", "#storytime", "#novel"],
        "thriller": ["#thrillerbooks", "#plotttwist", "#booktok", "#novel"],
        "revenge": ["#revenge", "#booktok", "#novel", "#storytime"],
    },
}

HOOK_MARKERS = {
    "zh": ("却", "竟", "？！", "离婚", "死", "重生", "穿越", "系统", "退婚", "断亲",
           "三年", "秘密", "真相", "白月光", "私生子", "破产", "顶替", "回归"),
    "en": ("but", "secret", "husband", "wife", "died", "dead", "divorce", "inherit",
           "married", "affair", "pregnant", "revenge", "truth", "until", "nobody",
           "never", "three years", "will", "lawyer", "buried", "locked", "sister"),
}

GENRES = {
    "zh": [("言情", ("婚", "喜欢", "白月光", "老公", "男朋友", "总裁", "心动")),
           ("悬疑", ("尸", "鬼", "死", "血", "失踪", "警察", "解剖", "暗道")),
           ("爽文", ("重生", "系统", "逆袭", "打脸", "废物", "退婚"))],
    "en": [("romance", ("marriage", "love", "husband", "wife", "affair", "wedding",
                        "boyfriend", "kiss", "heart")),
           ("thriller", ("body", "dead", "blood", "missing", "police", "shadow",
                         "locked", "scream", "grave", "murder")),
           ("revenge", ("revenge", "inherit", "divorce", "betray", "ruin", "pregnant",
                        "cheated", "exposed"))],
}


def bridges(l: str) -> List[str]:
    return BRIDGES.get(l, BRIDGES["en"])


def endings(l: str) -> List[str]:
    return ENDINGS.get(l, ENDINGS["en"])


def hashtags(l: str) -> Dict[str, List[str]]:
    return HASHTAGS.get(l, HASHTAGS["en"])


def hook_markers(l: str) -> tuple:
    return tuple(x.lower() for x in HOOK_MARKERS.get(l, HOOK_MARKERS["en"]))


def genre_of(text: str, l: str) -> str:
    low = text.lower() if is_space_lang(l) else text
    best, score = "", 0
    for name, words in GENRES.get(l, GENRES["en"]):
        s = sum(low.count(w.lower()) for w in words)
        if s > score:
            best, score = name, s
    return best


# ---------------------------------------------------------------- 提示词

SYSTEM = {
    "zh": {
        "rewrite": """你是顶尖的中文小说推文编剧，专门给短视频写「解压视频 + 有声小说」的口播稿。
你的稿子会被 AI 配音，配在解压视频上，用户是靠「听」来消费的。

硬性要求：
1. 口语化：短句为主，一句话不超过 22 个字；去掉所有书面语、长定语、比喻堆砌。
2. 保留原作的人名、称呼、关键设定、时间线，不能前后矛盾。
3. 只写「能听见的」内容，不要写心理长段描写和动作括号。
4. 每段结尾不要收得太满，要留一点往下听的惯性。
5. 不要出现「未完待续」「下集更精彩」「点赞关注」这类词。

输出严格 JSON。""",
        "hook": """你是短视频爆款开头专家，精通中文小说推文的「黄金三秒」与钩子结构。
你只输出严格 JSON。""",
        "adjust": """你是中文短视频口播稿的精修编辑，负责按目标时长增删内容，保持剧情连续、钩子不丢。
你只输出严格 JSON。""",
    },
    "en": {
        "rewrite": """You are a top scriptwriter for viral TikTok "novel promotion" videos. Your script is SPOKEN by an AI narrator over satisfying/ASMR footage. Viewers are LISTENING, not reading — your words have to keep them from scrolling.

Hard rules:
1. Spoken English only: short sentences, one idea per sentence, at most 16 words per sentence. No literary prose, no stacked clauses, no metaphors.
2. Keep the original names, relationships, key facts and timeline consistent. Never contradict yourself.
3. Write only what can be HEARD. No camera directions, no stage directions, no parentheses, no sound cues.
4. Never fully resolve a beat — always leave forward pull.
5. Never use: "to be continued", "stay tuned", "like and subscribe", "link in bio".

Output strict JSON only.""",
        "hook": """You are a viral hook specialist. You know exactly how to write the first 3 seconds of a TikTok novel-promo video so people stop scrolling. Output strict JSON only.""",
        "adjust": """You are a script editor who compresses or expands a spoken narration script to hit a target length while keeping the plot and the hooks intact. Output strict JSON only.""",
    },
}


def _lang(cfg) -> str:
    return lang_of(cfg)


def rewrite_prompt(cfg, chunk: str, target_units: int, prev_tail: str,
                   idx: int, total: int) -> str:
    l = _lang(cfg)
    if l == "en":
        max_units = int(target_units * 1.08)
        return """Rewrite the novel excerpt below into a spoken-narration script (chunk %d/%d).
Target length: %d words. HARD RANGE: %d-%d words — stay inside it, do not undershoot.

Rules:
- Keep only plot-driving content. Delete scenery, inner monologue paragraphs, repeated thoughts.
- One idea per sentence, max 16 words per sentence, spoken English.
- No narrator-style summaries ("and so the story begins").
- The previous chunk ended with: %s
  Start naturally from there; do not repeat it.

Excerpt:
\"\"\"
%s
\"\"\"

Output JSON: {"lines": ["first line", "second line", ...]}""" % (
            idx, total, target_units, int(target_units * 0.93), max_units,
            (prev_tail or "(this is the opening)"), chunk)
    max_chars = int(target_units * 1.08)
    return """把下面这段小说原文改写成口播稿（第 %d/%d 块），目标 %d 字，**最多不超过 %d 字（含标点）**，超了必须自己删。

要求：
- 只保留推动剧情的内容，删掉环境描写、重复心理活动。
- 每句 ≤ 22 字，口语化。
- 不要写旁白式总结（如「就这样，故事开始了」）。
- 上一块的结尾是：%s
  开头要自然衔接，不要重复上一块的内容。

原文：
\"\"\"
%s
\"\"\"

输出 JSON：{"lines": ["第一句", "第二句", ...]}""" % (
        idx, total, target_units, max_chars, (prev_tail or "（这是开头，没有上文）"), chunk)


def hook_pack_prompt(cfg, first_lines: List[str], all_lines: List[str], genre_hint: str) -> str:
    l = _lang(cfg)
    sample = "\n".join(all_lines[:6])
    tail = "\n".join(all_lines[-4:])
    if l == "en":
        return """This is a 3-10 minute TikTok novel-promo narration. Genre hint: %s.

Opening lines:
%s

Ending lines:
%s

Do 4 things and output JSON:

1. "hooks": 3 different 3-second openings, each MAX 18 words, each must stop the scroll:
   - type="Suspense": tease the anomaly, give no answer (e.g. "Three years married, and I'd never once been in that room.")
   - type="Conflict": drop the sharpest conflict in one line (e.g. "My husband's ex lives in our attic.")
   - type="Flashback": consequence first, then rewind (e.g. "I tore up the marriage certificate because of that soup.")
   Each: {"type": "Suspense|Conflict|Flashback", "text": "..."}
2. "bridges": 3 mid-video re-hooks (≤16 words each). They get inserted around 1/3 and 2/3 of the video to stop viewers leaving. Must be story-specific, no generic filler.
3. "ending": rewrite the last line as a cliffhanger (≤18 words).
4. "publish":
   - "title_candidates": 3 TikTok titles, each ≤ 60 characters, styles: curious / conflict / suspense
   - "hashtags": 4 English hashtags with #
   - "pinned_comment": one pinned comment that sends people to the full story, no link spam
   - "cover_text": two lines of cover text, separated by \\n, each ≤ 14 characters
   - "genre": genre (romance / thriller / revenge / drama)

Output JSON: {"hooks": [{"type":"Suspense","text":""},{"type":"Conflict","text":""},{"type":"Flashback","text":""}],
  "bridges": ["","",""], "ending": "", "publish": {...}}""" % (genre_hint, sample, tail)
    return """这是一条 3-10 分钟小说推文口播稿，题材倾向：%s。

开头几句：
%s

结尾几句：
%s

请做 3 件事，输出 JSON：

1. "hooks"：生成 3 个不同打法的「黄金三秒」，每个不超过 26 字，必须让人 3 秒内不想划走：
   - type="悬念"：只抛问题和异常，不给答案
   - type="冲突"：把最激烈的矛盾一句话甩出来
   - type="倒叙"：先给结果/后果，再回溯
   每项字段：{"type": "悬念|冲突|倒叙", "text": "…"}
2. "bridges"：给 3 句「续航钩」，会被插在视频 1/3 和 2/3 处，每句 ≤ 22 字，要跟剧情有关。
3. "ending"：把结尾改成断点（半句话 / 抉择 / 悬念定格），≤ 26 字。
4. "publish"：
   - "title_candidates": 3 个标题，各 ≤ 30 字，风格分别是「好奇」「冲突」「悬念」
   - "hashtags": 4 个中文话题标签，带 #
   - "pinned_comment": 一句置顶评论文案
   - "cover_text": 封面两行大字，用 \\n 分隔，每行 ≤ 8 字
   - "genre": 题材

输出 JSON：{"hooks": [...], "bridges": ["","",""], "ending": "", "publish": {...}}""" % (
        genre_hint, sample, tail)


def adjust_prompt(cfg, lines: List[str], ratio: float) -> str:
    l = _lang(cfg)
    action = ("compress" if ratio < 1 else "expand") if l == "en" else ("压缩" if ratio < 1 else "扩写")
    if l == "en":
        return """Below is a spoken-narration script, one line per sentence. %s it to about %.2f of its current length.

Rules:
- Spoken English, max 16 words per sentence.
- When compressing: delete only lines that don't move the plot; never delete twists or key reveals.
- When expanding: add detail, dialogue or action; do not invent a new main plot.
- Keep the order.
- Length check: count the words in your output. It MUST be within 3%% of %.2f x the original length. If it is not, redo it before answering.

Current lines:
%s

Output JSON: {"lines": [...]}""" % (action.capitalize(), ratio, ratio, _json(lines))
    return """下面是一条小说推文口播稿（逐句）。请在保持剧情和钩子完整的前提下%s到原来的 %.2f 倍长度。

规则：
- 每句 ≤ 22 字，口语化。
- 压缩时只删不影响主线的句子和重复信息，不要删反转和关键信息。
- 扩写时补细节、补对话、补动作，不要造新的主线剧情。
- 句数可以变，但顺序不能乱。

原稿：
%s

输出 JSON：{"lines": [...]}""" % (action, ratio, _json(lines))


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------- 离线兜底文案

OFFLINE_PUBLISH = {
    "zh": {
        "title": ["%s，我却发现了这个秘密", "别划走，%s这件事没那么简单", "%s，真相让我后背发凉"],
        "pinned": "全文整理好了，书名放在评论区置顶，想看后续的可以去看完整版。",
        "cover2": "我看清了真相",
        "cover_default": ["全集已整理", "全文在评论区"],
    },
    "en": {
        "title": ["%s — and then I found the secret", "Don't scroll: %s is not what it looks like",
                  "%s, and the truth still gives me chills"],
        "pinned": "Full story is pinned in the comments. Book title's there 👇",
        "cover2": "Then I saw the truth",
        "cover_default": ["Full story", "in the comments"],
    },
}

# ---------------------------------------------------------------- 风格参数

STYLES = {
    "en": {
        "auto": ("Auto", "no extra style directive"),
        "thriller": ("Thriller / Suspense",
                     "Genre: domestic thriller. Build dread, withhold information, clip your sentences. "
                     "Never let the listener feel safe."),
        "romance": ("Emotional Romance",
                    "Genre: emotional romance. Focus on the ache of being loved wrong, the wound in the "
                    "relationship, the intimacy that hurts."),
        "revenge": ("Revenge / Payback",
                    "Genre: revenge. Establish the injustice early, make the listener furious, and keep them "
                    "hungry for payback."),
        "sweet": ("Sweet / Cozy",
                  "Genre: sweet romance. Warm, playful, low stakes, high feeling. Keep it charming."),
        "drama": ("Family Drama",
                  "Genre: family drama. Secrets inside a family, loyalty vs betrayal, quiet cruelty."),
    },
    "zh": {
        "auto": ("自动", "不加额外风格约束"),
        "thriller": ("悬疑", "风格：悬疑。制造压迫感，信息延迟给，句子短促，别让听众有安全感。"),
        "romance": ("情感", "风格：情感。写被爱错的感觉、关系里的伤口，亲密但要疼。"),
        "revenge": ("爽文复仇", "风格：复仇。早点立起不公，让听众愤怒，憋着等打脸。"),
        "sweet": ("甜宠", "风格：甜宠。轻松、暖、低伤害，情绪密度高。"),
        "drama": ("家庭伦理", "风格：家庭伦理。家族秘密、亲情与背叛、安静的残忍。"),
    },
}

PERSONS = {
    "en": {
        "auto": ("Auto", ""),
        "first": ("First person", "Narration: first person. Write as the person who lived it, using 'I'."),
        "third": ("Third person", "Narration: third person limited, following the main character closely."),
    },
    "zh": {
        "auto": ("自动", ""),
        "first": ("第一人称", "叙述人称：第一人称，用「我」。"),
        "third": ("第三人称", "叙述人称：第三人称限知，紧跟主角。"),
    },
}

HOOK_STRENGTHS = {
    "en": {
        "normal": ("Balanced", ""),
        "strong": ("Strong", "Hook strength: aggressive. The very first line must be as sharp as possible — "
                            "the first 3 seconds decide everything."),
        "max": ("Maximum", "Hook strength: maximum. Open mid-crisis with zero setup. Assume the viewer will "
                          "leave in two seconds and dare them to."),
    },
    "zh": {
        "normal": ("自然", ""),
        "strong": ("强悬念", "钩子强度：强。第一句必须尽可能抓人，前三秒决定一切。"),
        "max": ("极强", "钩子强度：极强。零铺垫、直接从危机中段开场，逼观众不许划走。"),
    },
}


def _opt(dic: Dict, lang: str, key: str) -> str:
    table = dic.get(lang, dic["en"])
    item = table.get(key or "auto") or table["auto"]
    return item[1]


def directive(cfg, lang: str) -> str:
    """把风格/人称/钩子强度拼成一段附加给大模型的指令。"""
    parts = []
    st = str(cfg.get("script.style") or "auto")
    ps = str(cfg.get("script.person") or "auto")
    hs = str(cfg.get("script.hook_strength") or "normal")
    for text in (_opt(STYLES, lang, st), _opt(PERSONS, lang, ps), _opt(HOOK_STRENGTHS, lang, hs)):
        if text:
            parts.append(text)
    return "\n".join(parts)


def style_label(cfg, lang: str) -> str:
    key = str(cfg.get("script.style") or "auto")
    return (STYLES.get(lang, STYLES["en"]).get(key) or STYLES["en"]["auto"])[0]


def style_genre_hint(cfg, lang: str) -> str:
    """用风格当题材提示；auto 时返回空串交给自动判断。"""
    key = str(cfg.get("script.style") or "auto")
    if key == "auto":
        return ""
    return style_label(cfg, lang)


def style_options(lang: str) -> List[Dict]:
    return [{"id": k, "label": v[0]} for k, v in STYLES.get(lang, STYLES["en"]).items()]


def person_options(lang: str) -> List[Dict]:
    return [{"id": k, "label": v[0]} for k, v in PERSONS.get(lang, PERSONS["en"]).items()]


def hook_options(lang: str) -> List[Dict]:
    return [{"id": k, "label": v[0]} for k, v in HOOK_STRENGTHS.get(lang, HOOK_STRENGTHS["en"]).items()]
