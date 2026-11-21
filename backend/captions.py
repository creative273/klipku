"""Caption word-by-word sebagai file ASS (satu sumber untuk render).

Kata dikelompokkan per 1-3 kata. Kata yang sedang diucapkan diberi warna highlight.
"""
import re

DEFAULT_STYLE = {
    "font_family": "Arial",
    "font_size": 82,           # pada tinggi 1920; diskalakan ke resolusi output
    "color": "#FFFFFF",
    "highlight_color": "#FFD400",
    "outline_color": "#000000",
    "outline_px": 6,
    "bold": 1,
    "uppercase": 1,
    "words_per_line": 3,
    "position_y": 0.70,
}


def ass_color(hex_):
    h = hex_.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def clean(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words, max_words):
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        brk = (
            len(cur) >= max_words
            or re.search(r"[.,?!…;:]$", w["t"])
            or (nxt is not None and nxt["s"] - w["e"] > 0.5)
            or nxt is None
        )
        if brk:
            groups.append(cur)
            cur = []
    return groups


def build_ass(words, clip_start, clip_end, width, height, style=None, overrides=None, position_y=None):
    """words: transkrip penuh [{t,s,e}]. Kembalikan teks file ASS untuk satu klip."""
    st = {**DEFAULT_STYLE, **(style or {})}
    overrides = overrides or {}
    scale = height / 1920
    if width > height:  # landscape: caption relatif ke tinggi lebih kecil
        scale = height / 1080 * 0.75
    size = max(18, round(st["font_size"] * scale))
    outline = max(1, round(st["outline_px"] * scale))
    y = round(height * (position_y if position_y is not None else st["position_y"]))
    base, hi = ass_color(st["color"]), ass_color(st["highlight_color"])

    sel = []
    for idx, w in enumerate(words):
        if w["e"] <= clip_start or w["s"] >= clip_end:
            continue
        t = overrides.get(str(idx), w["t"])
        if not t.strip():
            continue  # kata dihapus lewat editor
        sel.append({"t": t.strip(), "s": max(w["s"], clip_start) - clip_start, "e": min(w["e"], clip_end) - clip_start})

    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Cap,{st['font_family']},{size},{base},{base},{ass_color(st['outline_color'])},&H80000000,"
        f"{-1 if st['bold'] else 0},0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    groups = group_words(sel, max(1, int(st["words_per_line"] or 3)))
    for gi, g in enumerate(groups):
        g_end = g[-1]["e"]
        nxt_start = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else clip_end - clip_start
        hold = min(nxt_start, g_end + 0.4)
        for wi, w in enumerate(g):
            s = w["s"]
            e = g[wi + 1]["s"] if wi + 1 < len(g) else hold
            if e <= s:
                continue
            parts = []
            for k, x in enumerate(g):
                txt = clean(x["t"].upper() if st["uppercase"] else x["t"])
                parts.append(f"{{\\c{hi}}}{txt}{{\\c{base}}}" if k == wi else txt)
            text = " ".join(parts)
            lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{\\pos({width // 2},{y})}}{text}")
    return head + "\n".join(lines) + "\n"
