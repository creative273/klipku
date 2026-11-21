"""Tahap render: satu pass ffmpeg = potong + crop/scale per format + caption ASS + encode.

M1: crop statis (tengah). Face tracking masuk di M2 dan akan mengganti posisi crop.
"""
import os
import re
from pathlib import Path

import tracking
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


def _crop(box, W, H):
    w, h, x, y = box
    return f"crop={w}:{h}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1"


def _layout_filter(seg, W, H, tag):
    """Filter untuk satu segmen: single = 1 crop penuh, dual = 2 crop atas-bawah."""
    if seg["kind"] == "dual":
        top = H // 2 // 2 * 2
        return (f"split=2[{tag}a][{tag}b];[{tag}a]{_crop(seg['boxes'][0], W, top)}[{tag}ta];"
                f"[{tag}b]{_crop(seg['boxes'][1], W, H - top)}[{tag}tb];[{tag}ta][{tag}tb]vstack=inputs=2")
    return _crop(seg["boxes"][0], W, H)


def segments_graph(segs, W, H):
    """Gabungkan segmen berlayout beda dalam satu filter_complex -> [base]."""
    if len(segs) == 1:
        return f"[0:v]{_layout_filter(segs[0], W, H, 'q0')}[base]"
    n = len(segs)
    parts = [f"[0:v]split={n}" + "".join(f"[i{k}]" for k in range(n))]
    for k, sg in enumerate(segs):
        end = "" if k == n - 1 else f":end={sg['e']:.4f}"
        parts.append(f"[i{k}]trim=start={sg['s']:.4f}{end},setpts=PTS-STARTPTS,{_layout_filter(sg, W, H, f'q{k}')}[s{k}]")
    parts.append("".join(f"[s{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0,setsar=1[base]")
    return ";".join(parts)


def video_graph(fmt, src_w, src_h, W, H, gaming_split=0.40, analysis=None):
    """Kembalikan (filter_complex -> [base], posisi caption [(s, e, y)] atau None)."""
    if fmt in ("face916", "square"):
        if analysis:
            segs = tracking.layout_segments(analysis, W / H, src_w, src_h, allow_dual=(fmt == "face916"))
        else:
            w, h, x, y = center_crop(src_w, src_h, W / H)
            segs = [{"s": 0.0, "e": 0.0, "kind": "single", "boxes": [(w, h, x, y)]}]
        pos = [(sg["s"], sg["e"], 0.5 if sg["kind"] == "dual" else None) for sg in segs]
        return segments_graph(segs, W, H), pos
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
        # atas: facecam (wajah yang diam sepanjang klip). Tidak ketemu: area tengah-atas.
        fc = analysis.get("facecam") if analysis else None
        if fc:
            tw, th, tx, ty = tracking.facecam_box(fc, W / top_h, src_w, src_h)
        else:
            tw, th, _, _ = center_crop(src_w, src_h, W / top_h)
            tw, th = even(tw * 0.5), even(th * 0.5)
            tx, ty = even((src_w - tw) / 2), 0
        y = top_h / H
        return (f"[0:v]split=2[a][b];"
                f"[a]crop={tw}:{th}:{tx}:{ty},scale={W}:{top_h}:flags=lanczos[top];"
                f"[b]crop={bw}:{bh}:{bx}:{by},scale={W}:{bot_h}:flags=lanczos[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[base]"), [(0.0, 0.0, y)]
    raise ValueError(f"format tidak dikenal: {fmt}")


def render_clip(src: Path, src_w, src_h, clip: dict, words, out_path: Path, work: Path, job, style=None,
                analysis=None):
    fmt = clip["format"]
    W, H = out_size(fmt, clip["resolution"])
    graph, cap_pos = video_graph(fmt, src_w, src_h, W, H, clip.get("gaming_split", 0.40), analysis)
    start, end = clip["start_s"], clip["end_s"]
    dur = end - start

    if clip.get("subtitle_on", 1):
        import json

        ass = work / f"clip_{clip['rank']:02d}.ass"
        ass.write_text(
            build_ass(words, start, end, W, H, style=style,
                      overrides=json.loads(clip.get("caption_overrides") or "{}"), positions=cap_pos),
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
