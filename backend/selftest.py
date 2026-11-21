"""Tes mesin (Milestone 0): dipanggil dari Pengaturan > Tes mesin."""
import json
import os
import time
from pathlib import Path

from config import DENO, FFMPEG, FFPROBE, YUNET, DEFAULT_OUTPUT as OUTPUT
from server_util import first_line, run

ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,90,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,0,5,40,40,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:03.00,Cap,,0,0,0,,{\\pos(540,768)}KLIPKU {\\c&H0000FF00&}OK
"""


def ff_filter_path(p: Path) -> str:
    """Escape path untuk dipakai di dalam filter ffmpeg (Windows: C\\:/...)."""
    s = p.resolve().as_posix()
    return s.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def selftest():
    results = []
    OUTPUT.mkdir(parents=True, exist_ok=True)
    work = OUTPUT / ".work"
    work.mkdir(exist_ok=True)

    # 1. ffmpeg punya filter ass + vstack (dipakai format Gaming)
    ok, out = run([FFMPEG, "-hide_banner", "-filters"])
    has_ass = ok and " ass " in out
    has_vstack = ok and " vstack " in out
    results.append({"test": "ffmpeg filter ass + vstack", "ok": has_ass and has_vstack,
                    "detail": f"ass={has_ass} vstack={has_vstack}"})

    # 2. render 3 detik 1080x1920: atas 40% + bawah 60% (simulasi format Gaming) + caption ASS
    ass = work / "selftest.ass"
    ass.write_text(ASS_TEMPLATE, encoding="utf-8")
    out_mp4 = OUTPUT / "klipku-selftest.mp4"
    tmp_mp4 = work / "klipku-selftest.part.mp4"
    t0 = time.time()
    graph = (
        "[0:v]split=2[a][b];"
        "[a]crop=600:427:100:60,scale=1080:768[top];"
        "[b]crop=1012:1080:454:0,scale=1080:1152[bot];"
        f"[top][bot]vstack=inputs=2,ass='{ff_filter_path(ass)}'[v]"
    )
    ok, out = run([
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", tmp_mp4,
    ], timeout=120)
    if ok:
        os.replace(tmp_mp4, out_mp4)
    results.append({"test": "render Gaming 9:16 + caption", "ok": ok and out_mp4.exists(),
                    "detail": f"{time.time() - t0:.1f}s -> {out_mp4}" if ok else out[-600:]})

    # 3. ffprobe baca hasil
    if out_mp4.exists():
        ok, out = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                       "stream=width,height", "-of", "json", out_mp4])
        dims = json.loads(out)["streams"][0] if ok else {}
        results.append({"test": "ffprobe 1080x1920", "ok": dims == {"width": 1080, "height": 1920},
                        "detail": str(dims or out[-300:])})

    # 4. YuNet face detector bisa dimuat
    try:
        import cv2

        det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320))
        results.append({"test": "YuNet face detector", "ok": det is not None, "detail": cv2.__version__})
    except Exception as e:  # noqa: BLE001
        results.append({"test": "YuNet face detector", "ok": False, "detail": str(e)})

    # 5. yt-dlp (paket Python, bukan exe) + deno
    try:
        import yt_dlp

        ok_dn, dn = run([DENO, "--version"])
        results.append({"test": "yt-dlp + deno", "ok": ok_dn,
                        "detail": f"yt-dlp {yt_dlp.version.__version__}; {first_line(dn)}"})
    except Exception as e:  # noqa: BLE001
        results.append({"test": "yt-dlp + deno", "ok": False, "detail": str(e)})

    # 6. keyring (Windows Credential Manager) tulis-baca-hapus
    try:
        import keyring

        keyring.set_password("Klipku-selftest", "probe", "ok")
        val = keyring.get_password("Klipku-selftest", "probe")
        keyring.delete_password("Klipku-selftest", "probe")
        results.append({"test": "keyring (Credential Manager)", "ok": val == "ok",
                        "detail": type(keyring.get_keyring()).__name__})
    except Exception as e:  # noqa: BLE001
        results.append({"test": "keyring (Credential Manager)", "ok": False, "detail": str(e)})

    return {"ok": all(r["ok"] for r in results), "output": str(out_mp4), "results": results}


