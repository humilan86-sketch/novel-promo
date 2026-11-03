"""视频合成：章节分段渲染（Ken Burns + 调色 + 颗粒 + 字幕 + 混音）再无损拼接。"""
import random
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .util import CmdError, ensure_dir, media_info, run


def _clip_filter(i: int, c: Dict, cfg: Config) -> str:
    w = int(cfg.get("render.width"))
    h = int(cfg.get("render.height"))
    fps = int(cfg.get("render.fps"))
    dur = float(c["duration"])
    fps_i = float(cfg.get("render.fps"))
    kb = float(c.get("kb", cfg.get("render.kb_strength") or 0.1))  # 整个片段内总推镜幅度
    kb = max(0.02, min(0.35, kb))
    total_frames = max(1.0, dur * fps_i)
    big_w, big_h = int(w * 1.35) // 2 * 2, int(h * 1.35) // 2 * 2
    if int(c.get("zoom_dir", 1)) >= 0:
        zexpr = "min(1+%.4f*on/%.1f,%.4f)" % (kb, total_frames, 1.0 + kb)
    else:
        zexpr = "max(%.4f-%.4f*on/%.1f,1.0)" % (1.0 + kb, kb, total_frames)
    mirror = "hflip," if c.get("mirror") else ""
    return (
        "[%d:v]%sscale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,"
        "fps=%d,zoompan=z='%s':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
        "s=%dx%d:fps=%d,trim=duration=%.3f,setpts=PTS-STARTPTS,format=yuv420p[v%d]"
        % (i, mirror, big_w, big_h, big_w, big_h, fps, zexpr, w, h, fps, dur, i)
    )


def render_chapter(cfg: Config, clips: List[Dict], chapter_wav: Path,
                   subtitles: List[Dict], out: Path, duration: float) -> Path:
    """渲染一个章节：多素材片段拼接 -> 调色/颗粒 -> 逐句字幕 overlay -> 混音。"""
    ensure_dir(out.parent)
    fps = int(cfg.get("render.fps"))
    args: List[str] = ["ffmpeg", "-v", "error", "-y", "-threads", "0"]
    for c in clips:
        args += ["-ss", "%.3f" % float(c["start"]), "-t", "%.3f" % (float(c["duration"]) + 0.5),
                 "-i", str(Path(c["path"]).resolve())]
    n_clips = len(clips)
    # 字幕 PNG（-loop 1 生成指定时长的静帧输入）
    for sub in subtitles:
        args += ["-loop", "1", "-framerate", str(fps), "-t", "%.3f" % sub["duration"],
                 "-i", str(Path(sub["png"]).resolve())]
    sub_base = n_clips
    audio_idx = n_clips + len(subtitles)
    bgm = str(cfg.get("render.bgm_path") or "").strip()
    bgm_idx = None
    if bgm and Path(bgm).exists():
        bgm_idx = audio_idx + 1
        args += ["-stream_loop", "-1", "-i", str(Path(bgm).resolve())]
    args += ["-i", str(Path(chapter_wav).resolve())]

    fc: List[str] = []
    for i, c in enumerate(clips):
        fc.append(_clip_filter(i, c, cfg))
    vconcat = "".join("[v%d]" % i for i in range(n_clips))
    fc.append("%sconcat=n=%d:v=1:a=0[vc]" % (vconcat, n_clips))
    # 素材比音频短时用末帧补齐（tpad），避免末段黑屏/静帧跳动
    fc.append("[vc]tpad=stop_mode=clone:stop_duration=3,trim=duration=%.3f,"
              "setpts=PTS-STARTPTS[vpad]" % duration)
    # 注意：eq 的 contrast / saturation 是乘数（默认 1.0），brightness 是加数
    d = float(cfg.get("render.color_delta") or 0.04)
    b = round(random.uniform(-d * 0.7, d * 0.7), 3)
    cc = round(1.0 + random.uniform(-d, d), 3)
    sat = round(1.0 + random.uniform(-d * 1.2, d * 1.2), 3)
    grain = int(cfg.get("render.grain"))
    texture = ",noise=alls=%d:allf=u" % grain if grain > 0 else ""
    fc.append("[vpad]eq=brightness=%.3f:contrast=%.3f:saturation=%.3f%s[vbase]"
              % (b, cc, sat, texture))

    cur = "vbase"
    for j, sub in enumerate(subtitles):
        idx = sub_base + j
        a, bb = float(sub["start"]), float(sub["end"])
        d = max(0.3, float(sub["duration"]))
        fi = min(float(cfg.get("subtitle.fade_in_ms")) / 1000.0, d / 3.0)
        fo = min(float(cfg.get("subtitle.fade_out_ms")) / 1000.0, d / 3.0)
        fc.append(
            "[%d:v]format=rgba,fade=t=in:st=0:d=%.2f:alpha=1,"
            "fade=t=out:st=%.3f:d=%.2f:alpha=1,setpts=PTS-STARTPTS+%.3f/TB[s%d]"
            % (idx, fi, max(0.0, d - fo), fo, a, j))
    for j, sub in enumerate(subtitles):
        a, bb = float(sub["start"]), float(sub["end"])
        nxt = "vsub%d" % j
        fc.append("[%s][s%d]overlay=0:0:eof_action=pass:enable='between(t,%.3f,%.3f)'[%s]"
                  % (cur, j, a, bb, nxt))
        cur = nxt
    fc.append("[%s]format=yuv420p[vout]" % cur)

    lufs = -16.0
    if bgm_idx is not None:
        vol = float(cfg.get("render.bgm_volume_db"))
        fc.append("[%d:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                  "volume=%.1fdB,atrim=0:%.3f[bg]" % (bgm_idx, vol, duration))
        fc.append("[%d:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                  "loudnorm=I=%.1f:TP=-1.5:LRA=11[voc]" % (audio_idx, lufs))
        fc.append("[voc][bg]amix=inputs=2:duration=first:dropout_transition=0,"
                  "loudnorm=I=%.1f:TP=-1.5:LRA=11[aout]" % lufs)
    else:
        fc.append("[%d:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                  "loudnorm=I=%.1f:TP=-1.5:LRA=11[aout]" % (audio_idx, lufs))

    args += [
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", "[aout]",
        "-t", "%.3f" % duration,
        "-c:v", "libx264", "-preset", str(cfg.get("render.preset")),
        "-crf", str(cfg.get("render.crf")), "-pix_fmt", "yuv420p", "-r", str(fps),
        "-maxrate", str(int(cfg.get("render.maxrate"))),
        "-bufsize", str(int(cfg.get("render.bufsize"))),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out),
    ]
    run(args, cwd=out.parent, timeout=3600)
    return out


def concat_chapters(chapters: List[Path], out: Path, expect_duration: float,
                    cfg: Config) -> Path:
    ensure_dir(out.parent)
    import tempfile
    list_file = Path(tempfile.mkdtemp()) / "concat.txt"
    list_file.write_text(
        "".join("file '%s'\n" % str(p.resolve()).replace("'", "'\\''") for p in chapters),
        encoding="utf-8",
    )
    base = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    try:
        run(base + ["-c", "copy", "-movflags", "+faststart", str(out)], timeout=1200)
        got = media_info(out)["duration"]
        bad = abs(got - expect_duration) > 2.0 or got <= 0
    except CmdError:
        bad = True
    if bad:
        # 时间戳异常或 copy 失败时重编码拼接（复用渲染配置）
        run(base + ["-c:v", "libx264", "-preset", str(cfg.get("render.preset")),
                    "-crf", str(cfg.get("render.crf")), "-pix_fmt", "yuv420p",
                    "-maxrate", str(int(cfg.get("render.maxrate"))),
                    "-bufsize", str(int(cfg.get("render.bufsize"))),
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
            timeout=3600)
    return out



# ------------------------------------------------------------------ 封面

def make_cover(cfg: Config, source: Path, at: float, lines: List[str], out: Path,
               tip: str = "") -> Path:
    """从素材源取一帧干净画面（没有烧录字幕）做封面，叠加两行大字。"""
    ensure_dir(out.parent)
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp())
    frame = tmp_dir / "frame.jpg"
    w = int(cfg.get("render.width"))
    h = int(cfg.get("render.height"))
    run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % max(0.0, at), "-i", str(source),
         "-frames:v", "1",
         "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d" % (w, h, w, h),
         str(frame)], timeout=180)

    img = Image.open(frame).convert("RGB")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(0, 1100):
        alpha = int(165 * (1 - y / 1100))
        od.rectangle([0, y, w, y + 1], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    font_path = str(cfg.get("project.font_path"))
    latin = bool(__import__("re").match(r"[A-Za-z]", (lines[0] if lines else "") or "a"))
    size_main, size_sub = (92, 60) if latin else (100, 66)
    y = 430
    for idx, line in enumerate(lines[:2]):
        line = line.strip()
        if not line:
            continue
        size = size_main if idx == 0 else size_sub
        font = _cover_font(font_path, size)
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=6)
        while (bbox[2] - bbox[0]) > w - 120 and size > 40:
            size = int(size * 0.9)
            font = _cover_font(font_path, size)
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=6)
        tw = bbox[2] - bbox[0]
        x = max(40, (w - tw) / 2)
        draw.text((x, y), line, font=font, fill=(255, 255, 255),
                  stroke_width=6, stroke_fill=(0, 0, 0))
        y += size + 34

    bar_y = h - 300
    bar = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(bar).rectangle([60, bar_y, w - 60, bar_y + 132], fill=(0, 0, 0, 170))
    img = Image.alpha_composite(img.convert("RGBA"), bar).convert("RGB")
    draw = ImageDraw.Draw(img)
    sf = _cover_font(font_path, 46)
    tip = tip or "Full story in the comments"
    bbox = draw.textbbox((0, 0), tip, font=sf)
    draw.text(((w - (bbox[2] - bbox[0])) / 2, bar_y + 40), tip, font=sf, fill=(255, 214, 102))
    img.save(out, quality=92)
    return out


def _cover_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size, index=0)
    except Exception:
        try:
            return ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", size, index=0)
        except Exception:
            return ImageFont.load_default()
