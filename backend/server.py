"""Klipku backend - Milestone 0 (spike packaging).

Hanya dua endpoint:
  GET  /health    versi semua engine yang dibundel
  POST /selftest  uji nyata: ffmpeg render 3 detik + caption ASS, YuNet load, yt-dlp import, keyring

Semua request wajib membawa header X-Klipku-Token (dibuat Electron saat start).
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOKEN = os.environ.get("KLIPKU_TOKEN", "")
ENGINE = Path(os.environ.get("KLIPKU_ENGINE", Path(__file__).resolve().parent.parent))
OUTPUT = Path(os.environ.get("KLIPKU_OUTPUT", Path.home() / "Videos" / "Klipku"))
EXE = ".exe" if os.name == "nt" else ""
BIN = ENGINE / "bin"
FFMPEG = BIN / f"ffmpeg{EXE}"
FFPROBE = BIN / f"ffprobe{EXE}"
DENO = BIN / f"deno{EXE}"
YUNET = ENGINE / "models" / "face_detection_yunet_2023mar.onnx"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

app = FastAPI(title="Klipku backend", version="0.0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "file://"],  # UI dimuat dari file:// oleh Electron
    allow_methods=["GET", "POST"],
    allow_headers=["X-Klipku-Token", "Content-Type"],
)


@app.middleware("http")
async def check_token(request: Request, call_next):
    if request.method != "OPTIONS" and TOKEN and request.headers.get("X-Klipku-Token") != TOKEN:
        return JSONResponse({"detail": "token salah"}, status_code=401)
    return await call_next(request)


def run(args, timeout=60):
    """Jalankan executable langsung (tanpa shell). Kembalikan (ok, output)."""
    try:
        p = subprocess.run(
            [str(a) for a in args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return False, f"tidak ditemukan: {args[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout}s: {args[0]}"


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else ""


@app.get("/health")
def health():
    ok_ff, ff = run([FFMPEG, "-hide_banner", "-version"])
    ok_dn, dn = run([DENO, "--version"])
    try:
        import yt_dlp.version as ytv

        ytdlp = ytv.__version__
    except Exception as e:  # noqa: BLE001
        ytdlp = f"error: {e}"
    try:
        import cv2

        opencv = cv2.__version__
    except Exception as e:  # noqa: BLE001
        opencv = f"error: {e}"
    return {
        "app": "0.0.1",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "engine_dir": str(ENGINE),
        "output_dir": str(OUTPUT),
        "ffmpeg": first_line(ff) if ok_ff else f"error: {first_line(ff)}",
        "deno": first_line(dn) if ok_dn else f"error: {first_line(dn)}",
        "yt_dlp": ytdlp,
        "opencv": opencv,
        "yunet_model": YUNET.exists(),
    }


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


@app.post("/selftest")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
