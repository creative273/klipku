"""Tahap transkrip. M1: online lewat Groq Whisper (timestamp per kata).

Audio diekstrak ke MP3 16 kHz mono 32 kbps (sekitar 14 MB per jam) lalu dipecah
per 20 menit supaya aman di bawah batas 25 MB per file dari Groq.
"""
import time
from pathlib import Path

import httpx

from config import FFMPEG, GROQ_STT_MODEL, PROVIDERS

CHUNK_S = 20 * 60


def extract_audio(src: Path, out: Path, start: float, dur: float, job):
    job.run([
        FFMPEG, "-hide_banner", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", out,
    ])


def groq_chunk(path: Path, key: str, language, job) -> dict:
    data = {
        "model": GROQ_STT_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
        "temperature": "0",
    }
    if language in ("id", "en"):
        data["language"] = language
    url = PROVIDERS["groq"]["base"] + "/audio/transcriptions"
    for attempt in range(4):
        job.check()
        with open(path, "rb") as f:
            r = httpx.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": (path.name, f, "audio/mpeg")},
                timeout=httpx.Timeout(300, connect=20),
            )
        if r.status_code == 429 and attempt < 3:
            wait = float(r.headers.get("retry-after", "10") or 10)
            time.sleep(min(wait, 60))
            continue
        if r.status_code == 401:
            raise RuntimeError("API key Groq salah atau kedaluwarsa. Cek di Pengaturan.")
        if r.status_code >= 400:
            raise RuntimeError(f"Transkrip Groq gagal (HTTP {r.status_code}): {r.text[:300]}")
        return r.json()
    raise RuntimeError("Transkrip Groq kena limit terus (HTTP 429). Coba lagi beberapa menit lagi.")


def transcribe_online(src: Path, duration: float, work: Path, key: str, language, job, emit) -> dict:
    if not key:
        raise RuntimeError("API key Groq belum diisi. Buka Pengaturan, isi key Groq (gratis), lalu ulangi.")
    words, segments, lang = [], [], None
    n = max(1, int((duration + CHUNK_S - 1) // CHUNK_S))
    for i in range(n):
        start = i * CHUNK_S
        dur = min(CHUNK_S, duration - start)
        if dur <= 0.5:
            break
        mp3 = work / f"audio_{i:02d}.mp3"
        extract_audio(src, mp3, start, dur, job)
        emit("progress", stage="transcribe", pct=round((i + 0.5) * 100 / n, 1),
             detail=f"Transkrip online bagian {i + 1} dari {n}" if n > 1 else "Transkrip online (Groq)")
        res = groq_chunk(mp3, key, language, job)
        lang = lang or res.get("language")
        for w in res.get("words") or []:
            t = (w.get("word") or "").strip()
            if t:
                words.append({"t": t, "s": round(w["start"] + start, 3), "e": round(w["end"] + start, 3)})
        for s in res.get("segments") or []:
            txt = (s.get("text") or "").strip()
            if txt:
                segments.append({"s": round(s["start"] + start, 3), "e": round(s["end"] + start, 3), "text": txt})
        emit("progress", stage="transcribe", pct=round((i + 1) * 100 / n, 1))
    if not words:
        raise RuntimeError("Transkrip kosong: tidak ada ucapan yang terdeteksi di video ini.")
    return {"engine": f"groq:{GROQ_STT_MODEL}", "words": words, "segments": segments, "language": lang}
