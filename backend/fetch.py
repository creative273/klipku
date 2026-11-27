"""Tahap fetch: unduh dari URL dengan yt-dlp (paket Python) atau pakai file lokal."""
import re
from pathlib import Path

from config import BIN
from proc import Cancelled

RES_HEIGHT = {"720p": 720, "1080p": 1080, "2k": 1440, "4k": 2160}


def safe_name(text: str, limit=60) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\']', "", text or "video").strip(" .")
    return (text[:limit] or "video").strip()


def download(url: str, work: Path, resolution: str, job, emit) -> dict:
    import yt_dlp

    max_h = RES_HEIGHT.get(resolution, 1080)

    def hook(d):
        if job.cancelled.is_set():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                got = d.get("downloaded_bytes", 0)
                emit("progress", stage="fetch", pct=round(got * 100 / total, 1),
                     detail=f"Mengunduh video · {got / 1e6:.0f} dari {total / 1e6:.0f} MB")

    opts = {
        "format": f"bv*[height<={max_h}]+ba/b[height<={max_h}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(work / "source.%(ext)s"),
        "ffmpeg_location": str(BIN),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 3,
        "overwrites": True,
    }
    cookies = work.parent.parent / "cookies.txt"
    if cookies.exists():
        opts["cookiefile"] = str(cookies)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        if job.cancelled.is_set():  # yt-dlp membungkus exception dari hook
            raise Cancelled() from None
        msg = str(e).replace("\x1b[0;31mERROR:\x1b[0m ", "").strip()
        raise RuntimeError(f"Download gagal: {msg[-400:]}") from None
    if not path.exists():
        cands = sorted(work.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
        if not cands:
            raise RuntimeError("Download selesai tapi file video tidak ditemukan")
        path = cands[0]
    heatmap = []
    for h in (info.get("heatmap") or []):
        try:
            s_, e_, v_ = float(h["start_time"]), float(h["end_time"]), float(h["value"])
            if e_ > s_:
                heatmap.append({"s": round(s_, 2), "e": round(e_, 2), "v": round(max(0.0, min(1.0, v_)), 3)})
        except (KeyError, TypeError, ValueError):
            continue
    return {"path": path, "title": info.get("title") or "video", "heatmap": heatmap}
