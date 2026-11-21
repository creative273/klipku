"""Transkrip offline dengan faster-whisper (CPU, int8).

Model tidak ikut di installer (ukurannya ratusan MB sampai 1.6 GB). User mengunduhnya
sekali dari Pengaturan; file model hanya data (bukan executable), disimpan di
%APPDATA%\\Klipku\\models\\whisper-<nama>.
"""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import numpy as np

from config import DATA, FFMPEG, NO_WINDOW

MODELS = {
    "small": {"repo": "Systran/faster-whisper-small", "label": "Small (cepat, ~480 MB)"},
    "turbo": {"repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "label": "Large v3 Turbo (akurat, ~1,6 GB)"},
}
WANT = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json")
ROOT = DATA / "models"

_dl = {}            # name -> {"pct", "error", "done", "bytes", "total"}
_dl_lock = threading.Lock()
_model = {"name": None, "obj": None}
_model_lock = threading.Lock()


def model_dir(name) -> Path:
    return ROOT / f"whisper-{name}"


def installed(name) -> bool:
    d = model_dir(name)
    return (d / "model.bin").exists() and (d / "config.json").exists() and (d / ".complete").exists()


def status():
    out = {}
    for name, m in MODELS.items():
        st = dict(_dl.get(name, {}))
        out[name] = {
            "label": m["label"],
            "installed": installed(name),
            "downloading": bool(st) and not st.get("done") and not st.get("error"),
            "pct": st.get("pct", 0),
            "error": st.get("error"),
        }
    return out


def start_download(name):
    if name not in MODELS:
        raise ValueError("model tidak dikenal")
    with _dl_lock:
        cur = _dl.get(name)
        if cur and not cur.get("done") and not cur.get("error"):
            return
        _dl[name] = {"pct": 0, "bytes": 0, "total": 0}
    threading.Thread(target=_download, args=(name,), daemon=True).start()


def _download(name):
    repo = MODELS[name]["repo"]
    dest = model_dir(name)
    tmp = dest.with_name(dest.name + ".part")
    st = _dl[name]
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        meta = httpx.get(f"https://huggingface.co/api/models/{repo}", params={"blobs": "true"}, timeout=30,
                         follow_redirects=True)
        meta.raise_for_status()
        files = [s for s in meta.json().get("siblings", []) if s["rfilename"] in WANT]
        if not any(f["rfilename"] == "model.bin" for f in files):
            raise RuntimeError("model.bin tidak ada di repo model")
        st["total"] = sum(f.get("size") or 0 for f in files)
        for f in files:
            url = f"https://huggingface.co/{repo}/resolve/main/{f['rfilename']}"
            with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, connect=20)) as r:
                r.raise_for_status()
                with open(tmp / f["rfilename"], "wb") as out:
                    for chunk in r.iter_bytes(1 << 20):
                        out.write(chunk)
                        st["bytes"] += len(chunk)
                        if st["total"]:
                            st["pct"] = round(st["bytes"] * 100 / st["total"], 1)
            if f.get("size") and (tmp / f["rfilename"]).stat().st_size != f["size"]:
                raise RuntimeError(f"Ukuran {f['rfilename']} tidak cocok, unduhan terputus. Coba lagi.")
        (tmp / ".complete").write_text(time.strftime("%Y-%m-%d"))
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        st["pct"] = 100
        st["done"] = True
    except Exception as e:  # noqa: BLE001
        st["error"] = f"Unduh model gagal: {e}"[:300]
        shutil.rmtree(tmp, ignore_errors=True)


def delete(name):
    with _model_lock:
        if _model["name"] == name:
            _model["name"], _model["obj"] = None, None
    shutil.rmtree(model_dir(name), ignore_errors=True)
    _dl.pop(name, None)


def _get_model(name):
    from faster_whisper import WhisperModel

    with _model_lock:
        if _model["name"] != name:
            _model["obj"] = None
            threads = max(2, (os.cpu_count() or 4) - 1)
            _model["obj"] = WhisperModel(str(model_dir(name)), device="cpu", compute_type="int8", cpu_threads=threads)
            _model["name"] = name
        return _model["obj"]


def load_audio(src, job) -> np.ndarray:
    """Decode audio track pertama ke float32 mono 16 kHz lewat ffmpeg bundel."""
    p = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:a:0", "-vn",
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, creationflags=NO_WINDOW,
    )
    job.check()
    if p.returncode != 0:
        raise RuntimeError("Gagal membaca audio: " + p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe_offline(src, duration, name, language, job, emit) -> dict:
    if not installed(name):
        raise RuntimeError(f"Model offline '{MODELS[name]['label']}' belum diunduh. Buka Pengaturan > Transkrip.")
    emit("log", text="Memuat model transkrip offline...")
    model = _get_model(name)
    audio = load_audio(src, job)
    segs, info = model.transcribe(
        audio,
        language=language if language in ("id", "en") else None,
        word_timestamps=True,
        vad_filter=True,
        beam_size=3,
        condition_on_previous_text=False,
    )
    words, segments = [], []
    last = 0.0
    for sg in segs:  # generator: transkrip berjalan saat diiterasi
        job.check()
        txt = (sg.text or "").strip()
        if txt:
            segments.append({"s": round(sg.start, 3), "e": round(sg.end, 3), "text": txt})
        for w in sg.words or []:
            t = (w.word or "").strip()
            if t:
                words.append({"t": t, "s": round(w.start, 3), "e": round(w.end, 3)})
        if sg.end - last > 5 and duration:
            last = sg.end
            emit("progress", stage="transcribe", pct=round(min(99, sg.end * 100 / duration), 1))
    if not words:
        raise RuntimeError("Transkrip kosong: tidak ada ucapan yang terdeteksi di video ini.")
    return {"engine": f"faster-whisper:{name}", "words": words, "segments": segments, "language": info.language}
