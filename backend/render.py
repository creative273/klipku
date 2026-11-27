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


def gameplay_avoid_facecam(bx, bw, fc, src_w, max_shift=0.12):
    """Geser crop gameplay (bawah) supaya kotak facecam tidak ikut terpotong di pinggirnya.

    Hanya bergeser kalau cukup kecil (<= 12% lebar sumber); kalau facecam ada di tengah, biarkan.
    """
    if not fc:
        return bx
    if fc.get("rect"):
        x0, x1 = fc["rect"][0] * src_w, fc["rect"][2] * src_w
    else:  # tepi kotak tidak ketemu: perkirakan lebar webcam sekitar 3x lebar wajah
        x0, x1 = (fc["cx"] - fc["w"] * 1.5) * src_w, (fc["cx"] + fc["w"] * 1.5) * src_w
    left, right = bx, bx + bw
    if x1 <= left or x0 >= right:
        return bx  # tidak beririsan
    limit = src_w * max_shift
    if (x0 + x1) / 2 > (left + right) / 2:   # facecam di sisi kanan crop: geser ke kiri
        shift = right - x0
        nb = bx - shift
    else:                                     # facecam di sisi kiri crop: geser ke kanan
        shift = x1 - left
        nb = bx + shift
    if shift > limit or nb < 0 or nb + bw > src_w:
        return bx
    return even(nb)


def _manual_crop_box(rect, ratio, src_w, src_h):
    """Ubah kotak manual ternormalisasi [x,y,w,h] (0..1) jadi piksel (w,h,x,y) berasio tepat."""
    rx = max(0.0, min(1.0, float(rect[0]))) * src_w
    ry = max(0.0, min(1.0, float(rect[1]))) * src_h
    rw = max(0.05, min(1.0, float(rect[2]))) * src_w
    rh = max(0.05, min(1.0, float(rect[3]))) * src_h
    rw = min(rw, src_w - rx)
    rh = min(rh, src_h - ry)
    cx, cy = rx + rw / 2, ry + rh / 2
    if rw / max(rh, 1.0) > ratio:
        h = rh
        w = h * ratio
    else:
        w = rw
        h = w / ratio
    return tracking._fit(w, h, cx - w / 2, cy - h / 2, src_w, src_h)


def video_graph(fmt, src_w, src_h, W, H, gaming_split=0.40, analysis=None, gaming_crop=None):
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
        gc = gaming_crop if isinstance(gaming_crop, dict) else {}
        manual = gc.get("mode") == "manual"
        if manual and isinstance(gc.get("gameplay"), list) and len(gc["gameplay"]) == 4:
            bw, bh, bx, by = _manual_crop_box(gc["gameplay"], W / bot_h, src_w, src_h)
        else:
            # bawah: crop tengah full frame, penuh tanpa bar
            bw, bh, bx, by = center_crop(src_w, src_h, W / bot_h)
            fc = analysis.get("facecam") if analysis else None
            bx = gameplay_avoid_facecam(bx, bw, fc, src_w)
        if manual and isinstance(gc.get("facecam"), list) and len(gc["facecam"]) == 4:
            tw, th, tx, ty = _manual_crop_box(gc["facecam"], W / top_h, src_w, src_h)
        else:
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


def parse_clip_segments(clip: dict) -> list[dict]:
    """Parse kolom clips.segments (JSON/list) menjadi daftar [{"s": float, "e": float, "note": str}, ...]."""
    import json
    raw = clip.get("segments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            raw = []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        try:
            if isinstance(item, dict):
                s, e = float(item["s"]), float(item["e"])
                note = str(item.get("note") or "")[:80].strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                s, e = float(item[0]), float(item[1])
                note = ""
            else:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if s >= 0 and e - s >= 0.4:
            out.append({"s": round(s, 3), "e": round(e, 3), "note": note})
    return out


def clip_segments(clip: dict, source_dur: float | None = None) -> list[tuple[float, float]]:
    """Kembalikan daftar segmen sumber [(s, e), ...] untuk klip.

    - Kompilasi (segments tidak kosong): daftar potongan [(s1, e1), (s2, e2), ...].
      Jika hook_clip_on juga aktif (>=2 segmen), tambahkan teaser 5 dtk dari peak_s di awal bila belum di awal.
    - Normal: [(start_s, end_s)]
    - Hook 5 dtk di depan (hook_clip_on=1):
      [(h_start, h_end), (m_start, m_end)]
      = 5 detik momen viral/heatmap di awal, lalu sisa durasinya adalah momen tepat sebelum kejadian itu.
    """
    custom = parse_clip_segments(clip)
    if custom:
        max_e = max(x["e"] for x in custom)
        src_dur = float(source_dur) if source_dur and source_dur > 0 else max_e
        segs = []
        for x in custom:
            s = max(0.0, min(x["s"], src_dur))
            e = max(0.0, min(x["e"], src_dur))
            if e - s >= 0.4:
                segs.append((round(s, 3), round(e, 3)))
        if segs:
            if clip.get("hook_clip_on") and len(segs) >= 2:
                peak = clip.get("peak_s")
                if peak is not None:
                    pk = float(peak)
                    # jika peak berada di segmen selain segmen pertama, taruh teaser 5 dtk di depan
                    first_s, first_e = segs[0]
                    if not (first_s <= pk < first_e):
                        h_s = max(0.0, min(pk, max(0.0, src_dur - 2.5)))
                        h_e = min(src_dur, h_s + 5.0)
                        for ss, se in segs:
                            if ss <= pk < se:
                                h_e = min(se, h_s + 5.0)
                                break
                        if h_e - h_s >= 1.5:
                            segs = [(round(h_s, 3), round(h_e, 3)), *segs]
            return segs

    start, end = float(clip["start_s"]), float(clip["end_s"])
    total = max(3.0, end - start)
    if not clip.get("hook_clip_on") or total < 10.0:
        return [(start, end)]
    src_dur = float(source_dur) if source_dur and source_dur > end else end
    hook_dur = min(5.0, max(2.5, total * 0.25))
    main_dur = max(3.0, total - hook_dur)
    peak = clip.get("peak_s")
    if peak is None:
        peak = start + total * 0.65
    h_start = max(0.0, min(float(peak), max(0.0, src_dur - hook_dur)))
    h_end = min(src_dur, h_start + hook_dur)
    # bagian kedua = momen sebelum kejadian viral (mis. 25 dtk sebelum momen 5 dtk untuk klip 30 dtk)
    if h_start >= 3.0:
        m_end = h_start
        m_start = max(0.0, m_end - main_dur)
        if m_end - m_start < 3.0:
            m_start, m_end = start, min(src_dur, start + main_dur)
    else:
        m_start = start
        m_end = min(src_dur, start + main_dur)
    return [(round(h_start, 3), round(h_end, 3)), (round(m_start, 3), round(m_end, 3))]


def render_clip(src: Path, src_w, src_h, clip: dict, words, out_path: Path, work: Path, job, style=None,
                analysis=None, watermark=None, on_progress=None, source_dur=None):
    """watermark: {"mode": "off|text|image", "text", "image" (path), "position", "opacity", "size"}"""
    import json

    fmt = clip["format"]
    W, H = out_size(fmt, clip["resolution"])
    gc = json.loads(clip.get("gaming_crop") or "{}")
    segs = clip_segments(clip, source_dur)
    multi = len(segs) >= 2
    graph, cap_pos = video_graph(fmt, src_w, src_h, W, H, clip.get("gaming_split", 0.40), analysis, gaming_crop=gc)
    start, end = float(clip["start_s"]), float(clip["end_s"])
    dur = sum(max(0.0, e - s) for s, e in segs)

    if multi:
        input_args = []
        pre = []
        concat_in = []
        n = len(segs)
        for k, (ss, se) in enumerate(segs):
            d = max(0.2, se - ss)
            input_args.extend(["-ss", f"{ss:.3f}", "-t", f"{d:.3f}", "-i", src])
            pre.append(f"[{k}:v]setpts=PTS-STARTPTS[sv{k}]")
            af = ["asetpts=PTS-STARTPTS"]
            if d > 0.08:
                if k > 0:
                    af.append("afade=t=in:st=0:d=0.02")
                if k < n - 1:
                    af.append(f"afade=t=out:st={max(0.0, d - 0.02):.3f}:d=0.02")
            pre.append(f"[{k}:a:0]{','.join(af)}[sa{k}]")
            concat_in.append(f"[sv{k}][sa{k}]")
        prefix = ";".join(pre) + ";" + "".join(concat_in) + f"concat=n={n}:v=1:a=1[rawv][aout];"
        graph = prefix + graph.replace("[0:v]", "[rawv]", 1)
        audio_map = "[aout]"
        wm_idx = n
    else:
        ss, se = segs[0]
        input_args = ["-ss", f"{ss:.3f}", "-t", f"{max(0.2, se - ss):.3f}", "-i", src]
        audio_map = "0:a:0"
        wm_idx = 1

    wm = watermark or {}
    extra_inputs = []
    if wm.get("mode") == "image" and wm.get("image") and Path(wm["image"]).exists():
        ww = even(W * float(wm.get("size", 0.18)))
        op = max(0.05, min(1.0, float(wm.get("opacity", 0.85))))
        # tinggi logo belum diketahui sebelum scale: pakai overlay dengan ekspresi ukuran (w/h = ukuran logo)
        m = round(W * 0.03)
        pos = wm.get("position", "tr")
        ox = f"{m}" if pos in ("tl", "bl") else f"W-w-{m}"
        oy = f"{m}" if pos in ("tl", "tr") else f"H-h-{m}"
        graph += (f";[{wm_idx}:v]scale={ww}:-2:flags=lanczos,format=rgba,colorchannelmixer=aa={op:.2f}[wm];"
                  f"[base][wm]overlay={ox}:{oy}:format=auto[basew]")
        base_label = "basew"
        extra_inputs = ["-loop", "1", "-i", str(wm["image"])]
    else:
        base_label = "base"
    wm_text = wm.get("text", "").strip() if wm.get("mode") == "text" else ""

    subs = bool(clip.get("subtitle_on", 1))
    title = clip.get("hook_title") if clip.get("title_overlay") else None
    if subs or title or wm_text:
        # judul: Gaming di atas panel facecam, format lain 10% dari atas
        title_y = 0.06 if fmt == "gaming916" else 0.10
        ass = work / f"clip_{clip['id']}.ass"
        ass.write_text(
            build_ass(words, start, end, W, H, style=style,
                      overrides=json.loads(clip.get("caption_overrides") or "{}"), positions=cap_pos,
                      subtitles=subs, title=title, title_y=title_y,
                      watermark_text=wm_text, watermark_pos=wm.get("position", "tr"),
                      watermark_opacity=float(wm.get("opacity", 0.85)),
                      segments=segs),
            encoding="utf-8",
        )
        # ffmpeg dijalankan dengan cwd=work, jadi ASS & fontsdir dirujuk relatif:
        # tidak ada titik dua / apostrof / spasi dari path user di dalam filtergraph.
        fontsdir = f":fontsdir={rel_filter_path(FONTS, work)}" if FONTS.exists() else ""
        graph += f";[{base_label}]ass={ass.name}{fontsdir}[v]"
    else:
        graph += f";[{base_label}]null[v]"

    tmp = out_path.with_name(out_path.stem + ".part.mp4")
    try:
        _encode(job, input_args, dur, graph, audio_map, tmp, work, extra_inputs, on_progress)
    except BaseException:
        tmp.unlink(missing_ok=True)  # batal / gagal: jangan tinggalkan file setengah jadi
        raise
    os.replace(tmp, out_path)

    thumb = work / f"thumb_{clip['id']}.jpg"
    job.run([FFMPEG, "-hide_banner", "-y", "-ss", f"{min(1.5, dur / 2):.2f}", "-i", out_path,
             "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", thumb])
    return thumb


def _encode(job, input_args, dur, graph, audio_map, tmp, work, extra_inputs=(), on_progress=None):
    job.run([
        FFMPEG, "-hide_banner", "-nostats", "-progress", "pipe:1", "-y", *input_args, *extra_inputs,
        "-filter_complex", graph, "-map", "[v]", "-map", audio_map, "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", tmp,
    ], cwd=work, on_progress=on_progress, total_s=dur)
