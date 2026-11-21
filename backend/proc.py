"""Menjalankan executable bundel (ffmpeg/ffprobe) tanpa shell, bisa dibatalkan."""
import json
import subprocess
import threading

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

    def run(self, args, timeout=None, cwd=None) -> str:
        """Jalankan, kembalikan stderr+stdout. Raise RuntimeError kalau gagal."""
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
        try:
            out, _ = self._proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            raise RuntimeError(f"timeout {timeout}s: {args[0]}")
        text = out.decode("utf-8", errors="replace")
        self.check()
        if self._proc.returncode != 0:
            raise RuntimeError(text.strip().splitlines()[-1] if text.strip() else f"exit {self._proc.returncode}")
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
