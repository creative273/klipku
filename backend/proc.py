"""Menjalankan executable bundel (ffmpeg/ffprobe) tanpa shell, bisa dibatalkan."""
import json
import re
import subprocess
import threading
import time

from config import FFPROBE, NO_WINDOW


class Cancelled(Exception):
    pass


class Job:
    """Penanda pembatalan per sesi + proses anak yang sedang jalan."""

    def __init__(self):
        self.cancelled = threading.Event()
        self._proc = None
        self._lock = threading.Lock()

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    def run(self, args, timeout=None, cwd=None, on_progress=None, total_s=None) -> str:
        """Jalankan, kembalikan output. Raise RuntimeError kalau gagal.

        on_progress(frac 0..1): untuk ffmpeg, tambahkan "-progress pipe:1 -nostats" di args;
        baris out_time_us dibaca selama proses berjalan.
        """
        self.check()
        with self._lock:
            self._proc = subprocess.Popen(
                [str(a) for a in args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=cwd,
                creationflags=NO_WINDOW,
            )
        p = self._proc
        progress_mode = "-progress" in [str(a) for a in args]
        tail = []
        deadline = time.time() + timeout if timeout else None
        killer = None
        if deadline:
            killer = threading.Timer(timeout, lambda: p.poll() is None and p.kill())
            killer.start()
        try:
            for raw in p.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if progress_mode and on_progress and total_s and line.startswith(("out_time_us=", "out_time_ms=")):
                    try:
                        us = int(line.split("=", 1)[1])
                        on_progress(max(0.0, min(1.0, us / 1e6 / total_s)))
                    except ValueError:
                        pass
                    continue
                if progress_mode and re.match(r"^[a-z_0-9]+=", line):
                    continue  # baris -progress lain (frame=, speed=, progress=)
                tail.append(line)
                if len(tail) > 60:
                    del tail[:20]
            p.wait()
        finally:
            if killer:
                killer.cancel()
        if deadline and time.time() > deadline and p.returncode != 0:
            raise RuntimeError(f"timeout {timeout}s: {args[0]}")
        self.check()
        text = "\n".join(tail)
        if p.returncode != 0:
            raise RuntimeError(text.strip().splitlines()[-1] if text.strip() else f"exit {p.returncode}")
        return text


def probe(path) -> dict:
    p = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,width,height,r_frame_rate,channels:stream_tags=language",
         "-of", "json", str(path)],
        capture_output=True, creationflags=NO_WINDOW,
    )
    if p.returncode != 0:
        raise RuntimeError("ffprobe gagal membaca video: " + p.stderr.decode("utf-8", "replace")[-300:])
    info = json.loads(p.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if v is None:
        raise RuntimeError("File tidak punya stream video")
    if not audios:
        raise RuntimeError("File tidak punya audio, tidak bisa ditranskrip")
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return {
        "duration": float(info["format"]["duration"]),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": float(num) / float(den or 1),
        "audio_indexes": [a["index"] for a in audios],
    }
