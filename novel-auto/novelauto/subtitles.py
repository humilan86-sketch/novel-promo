"""字幕渲染：用 PIL 把每句字幕画成 1080x1920 透明 PNG，交给 ffmpeg overlay 叠加。

为什么不用 ASS：本机 ffmpeg 构建未启用 libass/freetype（ass/drawtext/subtitles 滤镜都不可用）。
PNG overlay 方案零外部依赖、中文字体可控，而且以后要加「关键词变色/放大」也只改这里。
"""
import hashlib
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .util import ensure_dir


def _ass_color(value, default):
    """把 ASS 的 &HAABBGGRR 颜色转成 PIL RGBA；解析失败用默认值。"""
    v = str(value or "")
    try:
        if v.startswith("&H") and len(v) >= 10:
            b, g, r = int(v[4:6], 16), int(v[6:8], 16), int(v[8:10], 16)
            return (r, g, b, 255)
    except Exception:
        pass
    return default


def _font(path: str, size: int, index: int = 0):
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        for p in ("/System/Library/Fonts/STHeiti Medium.ttc",
                  "/System/Library/Fonts/Songti.ttc"):
            try:
                return ImageFont.truetype(p, size, index=0)
            except Exception:
                continue
        return ImageFont.load_default()


def wrap_lines(text: str, max_chars: int, max_lines: int = 3) -> List[str]:
    """折行：英文按单词、中文按字数，优先在标点处断开。"""
    text = text.strip()
    if " " in text and len(text) > max_chars:
        return _wrap_words(text, max_chars, max_lines)
    if len(text) <= max_chars:
        return [text]
    lines: List[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= max_chars and ch in "，、；：…—,.!?":
            lines.append(buf)
            buf = ""
        elif len(buf) >= max_chars * 2:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    out: List[str] = []
    for l in lines:
        while len(l) > max_chars:
            cut = _cut(l, max_chars)
            out.append(l[:cut])
            l = l[cut:]
        if l:
            out.append(l)
    return out[:max_lines]


def wrap_to_width(text: str, font, max_width: int, max_lines: int = 3) -> List[str]:
    """按渲染后的像素宽度换行（中英文都适用），避免英文长句横向溢出。"""
    words = text.split()
    if len(words) > 1:  # 英文/带空格的文本：按词
        lines: List[str] = []
        buf = ""
        for w in words:
            trial = (buf + " " + w).strip()
            if not buf or _text_w(trial, font) <= max_width:
                buf = trial
            else:
                lines.append(buf)
                buf = w
        if buf:
            lines.append(buf)
        # 单词本身超宽：从中间硬断
        out: List[str] = []
        for l in lines:
            while _text_w(l, font) > max_width and len(l) > 1:
                cut = len(l) // 2
                out.append(l[:cut])
                l = l[cut:]
            out.append(l)
        return out[:max_lines]
    # 中文：按字数均匀切
    n = max(1, int(len(text) / max(1, max_width / max(1, _text_w("字", font)))))
    lines = [text[i:i + n] for i in range(0, len(text), n)]
    return lines[:max_lines]


def _text_w(text: str, font) -> int:
    from PIL import ImageDraw
    img = Image.new("RGBA", (8, 8))
    try:
        return ImageDraw.Draw(img).textbbox((0, 0), text, font=font)[2]
    except Exception:
        return len(text) * 32


def _wrap_words(text: str, max_chars: int, max_lines: int = 3) -> List[str]:
    words = text.split()
    lines: List[str] = []
    buf = ""
    for w in words:
        if not buf:
            buf = w
        elif len(buf) + 1 + len(w) <= max_chars:
            buf += " " + w
        else:
            lines.append(buf)
            buf = w
    if buf:
        lines.append(buf)
    return lines[:max_lines]


def _cut(text: str, limit: int) -> int:
    for i in range(limit, max(2, limit // 2), -1):
        if text[i - 1] in "，、；：…—":
            return i
    return limit


def _draw_png(text: str, cfg: Config, out: Path) -> Path:
    w = int(cfg.get("render.width"))
    h = int(cfg.get("render.height"))
    size = int(cfg.get("subtitle.font_size"))
    margin_v = int(cfg.get("subtitle.margin_v"))
    outline = int(cfg.get("subtitle.outline"))
    shadow = int(cfg.get("subtitle.shadow"))
    max_chars = int(cfg.get("subtitle.max_chars_per_line"))
    font_path = str(cfg.get("project.font_path"))
    fill = _ass_color(cfg.get("subtitle.primary"), (255, 255, 255, 255))
    stroke = _ass_color(cfg.get("subtitle.outline_color"), (0, 0, 0, 255))
    shadow_c = (stroke[0], stroke[1], stroke[2], 190)

    max_width = int(w * 0.88)
    lines: List[str] = []
    for attempt in range(3):
        font = _font(font_path, size)
        lines = wrap_to_width(text, font, max_width, max_lines=4)
        if len(lines) <= 3 or attempt == 2:
            break
        size = int(size * 0.9)   # 行太多就缩字号，绝不丢字
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    line_h = int(size * 1.42)
    block_h = line_h * len(lines)
    y0 = h - margin_v - block_h
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=outline)
        tw = bbox[2] - bbox[0]
        x = (w - tw) // 2
        y = y0 + i * line_h
        if shadow:
            draw.text((x + shadow, y + shadow), line, font=font, fill=shadow_c,
                      stroke_width=outline, stroke_fill=shadow_c)
        draw.text((x, y), line, font=font, fill=fill,
                  stroke_width=outline, stroke_fill=stroke)
    ensure_dir(out.parent)
    img.save(out, optimize=True)
    return out


def make_subtitle_pngs(segments: List[Dict], cfg: Config, out_dir: Path) -> List[Dict]:
    """返回 [{"png": Path, "start": 相对开始, "end": 相对结束, "duration": ...}]"""
    cache_dir = ensure_dir(cfg.path("tts.cache_dir").parent / "subs")
    result: List[Dict] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        start = seg.get("rel_start")
        end = seg.get("rel_end")
        if not text or start is None or end is None:
            continue
        if end - start < 0.2:
            end = start + 0.2
        key = hashlib.sha1("|".join(str(x) for x in [
            "v3",   # 渲染逻辑版本号：改换行/字体策略时 +1，避免复用旧图
            text, cfg.get("subtitle.font_size"), cfg.get("subtitle.margin_v"),
            cfg.get("subtitle.outline"), cfg.get("subtitle.shadow"),
            cfg.get("subtitle.max_chars_per_line"),
            cfg.get("render.width"), cfg.get("render.height"),
            cfg.get("project.font_path"), cfg.get("subtitle.primary"),
        ]).encode()).hexdigest()[:20]
        png = cache_dir / (key + ".png")
        if not png.exists():
            _draw_png(text, cfg, png)
        result.append({
            "png": png,
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "duration": round(float(end) - float(start), 3),
        })
    return result
