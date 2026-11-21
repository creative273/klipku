"""Tahap render: satu pass ffmpeg = potong + crop/scale per format + caption ASS + encode.

M1: crop statis (tengah). Face tracking masuk di M2 dan akan mengganti posisi crop.
"""
import os
import re
from pathlib import Path

from captions import build_ass
from config import FFMPEG, FONTS

SIZES = {
    "916": {"720p": (720, 1280), "1080p": (1080, 1920), "2k": (1440, 2560), "4k": (2160, 3840)},
    "square": {"720p": (720, 720), "1080p": (1080, 1080), "2k": (1440, 1440), "4k": (2160, 2160)},
    "landscape": {"720p": (1280, 720), "1080p": (1920, 1080), "2k": (2560, 1440), "4k": (3840, 2160)},
}


def even(x):
    return int(round(x / 2)) * 2


def out_size(fmt, resolution):
    key = "square" if fmt == "square" else "landscape" if fmt == "landscape" else "916"
    return SIZES[key].get(resolution, SIZES[key]["1080p"])


def ff_path(p: Path) -> str:
    """Escape path untuk argumen filter ffmpeg (C:\\x -> C\\:/x, apostrof di-escape)."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def rel_filter_path(target: Path, base: Path) -> str:
    """Path relatif berslash maju untuk filter ffmpeg. Lain drive: pakai ff_path absolut."""
    try:
        rel = os.path.relpath(target, base).replace("\\", "/")
    except ValueError:
        return "'" + ff_path(target) + "'"
    if re.search(r"[\s'\\:,;\[\]=]", rel):
        return "'" + ff_path(target) + "'"
    return rel


def center_crop(src_w, src_h, ratio):
    """Kotak crop terbesar dengan rasio w/h tertentu, di tengah sumber."""
    if src_w / src_h > ratio:
        h = src_h
        w = even(h * ratio)
    else:
        w = src_w
        h = even(w / ratio)
    return w, h, even((src_w - w) / 2), even((src_h - h) / 2)


def video_graph(fmt, src_w, src_h, W, H, gaming_split=0.40):
    """Kembalikan filter_complex (tanpa caption) yang menghasilkan label [base]."""
    if fmt in ("face916", "square"):
        w, h, x, y = center_crop(src_w, src_h, W / H)
        return f"[0:v]crop={w}:{h}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1[base]", None
    if fmt == "landscape":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[base]"), None
    if fmt == "blurbg916":
        return (f"[0:v]split=2[bg][fg];"
                f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,eq=brightness=-0.08[bgb];"
                f"[fg]scale={W}:-2:flags=lanczos[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"), None
    if fmt == "gaming916":
        top_h = even(H * gaming_split)
        bot_h = H - top_h
        # bawah: crop tengah full frame, penuh tanpa bar
        bw, bh, bx, by = center_crop(src_w, src_h, W / bot_h)
        # atas: tanpa tracking (M1) pakai area tengah-atas. M2 mengganti dengan posisi wajah.
        tw, th, tx, _ = center_crop(src_w, src_h, W / top_h)
        tw, th = even(tw * 0.5), even(th * 0.5)
        tx = even((src_w - tw) / 2)
        return (f"[0:v]split=2[a][b];"
                f"[a]crop={tw}:{th}:{tx}:0,scale={W}:{top_h}:flags=lanczos[top];"
                f"[b]crop={bw}:{bh}:{bx}:{by},scale={W}:{bot_h}:flags=lanczos[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[base]"), gaming_split
    raise ValueError(f"format tidak dikenal: {fmt}")


def render_clip(src: Path, src_w, src_h, clip: dict, words, out_path: Path, work: Path, job, style=None):
    fmt = clip["format"]
    W, H = out_size(fmt, clip["resolution"])
    graph, cap_y = video_graph(fmt, src_w, src_h, W, H, clip.get("gaming_split", 0.40))
    start, end = clip["start_s"], clip["end_s"]
    dur = end - start

    if clip.get("subtitle_on", 1):
        import json

        ass = work / f"clip_{clip['rank']:02d}.ass"
        ass.write_text(
            build_ass(words, start, end, W, H, style=style,
                      overrides=json.loads(clip.get("caption_overrides") or "{}"), position_y=cap_y),
            encoding="utf-8",
        )
        # ffmpeg dijalankan dengan cwd=work, jadi ASS & fontsdir dirujuk relatif:
        # tidak ada titik dua / apostrof / spasi dari path user di dalam filtergraph.
        fontsdir = f":fontsdir={rel_filter_path(FONTS, work)}" if FONTS.exists() else ""
        graph += f";[base]ass={ass.name}{fontsdir}[v]"
    else:
        graph += ";[base]null[v]"

    tmp = out_path.with_name(out_path.stem + ".part.mp4")
    try:
        _encode(job, src, start, dur, graph, tmp, work)
    except BaseException:
        tmp.unlink(missing_ok=True)  # batal / gagal: jangan tinggalkan file setengah jadi
        raise
    os.replace(tmp, out_path)

    thumb = work / f"thumb_{clip['id']}.jpg"
    job.run([FFMPEG, "-hide_banner", "-y", "-ss", f"{min(1.5, dur / 2):.2f}", "-i", out_path,
             "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", thumb])
    return thumb


def _encode(job, src, start, dur, graph, tmp, work):
    job.run([
        FFMPEG, "-hide_banner", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", tmp,
    ], cwd=work)
