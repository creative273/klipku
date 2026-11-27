"""Klipku backend - API lokal untuk UI Electron.

Hanya listen di 127.0.0.1. Setiap request wajib bawa token (header X-Klipku-Token,
atau query ?t= untuk <video>/<img> yang tidak bisa kirim header).
"""
import argparse
import asyncio
import json
import platform
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
import ytdlp_update

ytdlp_update.activate()  # harus sebelum yt_dlp di-import di mana pun

import db  # noqa: E402
import keys  # noqa: E402
import pipeline  # noqa: E402
import styles  # noqa: E402
from config import DENO, FFMPEG, GITHUB_REPO, PROVIDERS, TOKEN, YUNET  # noqa: E402
from server_util import first_line, run  # noqa: E402

VERSION = "1.0.0"
app = FastAPI(title="Klipku backend", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "file://"],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["X-Klipku-Token", "Content-Type"],
)


@app.middleware("http")
async def check_token(request: Request, call_next):
    if request.method != "OPTIONS" and TOKEN:
        got = request.headers.get("X-Klipku-Token") or request.query_params.get("t")
        if got != TOKEN:
            return JSONResponse({"detail": "token salah"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def startup():
    db.init()
    styles.seed()
    pipeline.start_worker()


# ---------- sistem ----------

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
        "app": VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "engine_dir": str(config.ENGINE),
        "output_dir": str(pipeline.output_root()),
        "ffmpeg": first_line(ff) if ok_ff else f"error: {first_line(ff)}",
        "deno": first_line(dn) if ok_dn else f"error: {first_line(dn)}",
        "yt_dlp": ytdlp,
        "opencv": opencv,
        "yunet_model": YUNET.exists(),
    }


@app.post("/selftest")
def selftest_route():
    import selftest
    return selftest.selftest()


# ---------- settings ----------

class WatermarkIn(BaseModel):
    mode: str = "off"
    text: str = ""
    position: str = "tr"
    opacity: float = 0.85
    size: float = 0.18


class SettingsIn(BaseModel):
    transcribe_mode: str | None = None
    whisper_model: str | None = None
    watermark: WatermarkIn | None = None
    caption_style_id: str | None = None
    provider: str | None = None
    model: str | None = None
    output_dir: str | None = None
    clip_count: int | None = None
    clip_duration: str | None = None
    resolution: str | None = None
    welcome_seen: bool | None = None


@app.get("/settings")
def get_settings():
    import whisper_local
    st = db.get_settings()
    st["output_dir_effective"] = str(pipeline.output_root())
    st["watermark_image"] = pipeline.WATERMARK_IMG.exists()
    st["whisper"] = whisper_local.status()
    return {"settings": st, "keys": keys.status(PROVIDERS), "providers": PROVIDERS}


@app.put("/settings")
def put_settings(body: SettingsIn):
    data = body.model_dump(exclude_unset=True)
    if "provider" in data and data["provider"] not in PROVIDERS:
        raise HTTPException(400, "provider tidak dikenal")
    if "transcribe_mode" in data and data["transcribe_mode"] not in ("online", "offline"):
        raise HTTPException(400, "mode transkrip tidak dikenal")
    if "whisper_model" in data:
        import whisper_local
        if data["whisper_model"] not in whisper_local.MODELS:
            raise HTTPException(400, "model tidak dikenal")
    if body.watermark is not None:
        data["watermark"] = w = body.watermark.model_dump()  # isi default untuk field yang tidak dikirim
        if w["mode"] not in ("off", "text", "image") or w["position"] not in ("tl", "tr", "bl", "br"):
            raise HTTPException(400, "pengaturan watermark tidak valid")
        w["opacity"] = max(0.05, min(1.0, float(w["opacity"])))
        w["size"] = max(0.05, min(0.5, float(w["size"])))
        w["text"] = (w["text"] or "")[:60]
    if data.get("output_dir"):
        p = Path(data["output_dir"])
        try:
            p.mkdir(parents=True, exist_ok=True)
            (p / ".klipku-write-test").write_text("ok")
            (p / ".klipku-write-test").unlink()
        except OSError as e:
            raise HTTPException(400, f"Folder output tidak bisa ditulis: {e}") from None
    for k, v in data.items():
        db.set_setting(k, v)
    return get_settings()


class KeyIn(BaseModel):
    key: str


@app.put("/settings/keys/{provider}")
def put_key(provider: str, body: KeyIn):
    if provider not in PROVIDERS:
        raise HTTPException(404, "provider tidak dikenal")
    keys.set_(provider, body.key)
    return {"ok": True, "keys": keys.status(PROVIDERS)}


class PathIn(BaseModel):
    path: str


@app.put("/settings/watermark-image")
def put_watermark_image(body: PathIn):
    """Salin gambar logo ke folder data sebagai PNG (ffmpeg mengonversi JPG/WEBP/PNG)."""
    src = Path(body.path)
    if not src.is_file():
        raise HTTPException(400, "File gambar tidak ditemukan")
    ok, out = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-frames:v", "1",
                   "-vf", "scale='min(1200,iw)':-2", pipeline.WATERMARK_IMG])
    if not ok:
        raise HTTPException(400, f"Gambar tidak bisa dibaca: {out[-200:]}")
    return {"ok": True}


@app.get("/settings/watermark-image")
def get_watermark_image():
    if not pipeline.WATERMARK_IMG.exists():
        raise HTTPException(404, "belum ada logo")
    return FileResponse(pipeline.WATERMARK_IMG, media_type="image/png")


@app.post("/models/whisper/{name}/download")
def whisper_download(name: str):
    import whisper_local
    try:
        whisper_local.start_download(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return whisper_local.status()


@app.delete("/models/whisper/{name}")
def whisper_delete(name: str):
    import whisper_local
    whisper_local.delete(name)
    return whisper_local.status()


@app.get("/models/whisper")
def whisper_status():
    import whisper_local
    return whisper_local.status()


@app.get("/engine/ytdlp")
def ytdlp_status():
    try:
        latest, _ = ytdlp_update.check_latest()
    except Exception:  # noqa: BLE001
        latest = None
    return {"active": ytdlp_update.active_version(), "latest": latest}


@app.post("/engine/update-ytdlp")
def ytdlp_do_update():
    try:
        return ytdlp_update.update()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Update yt-dlp gagal: {e}") from None


def _ver_tuple(v: str | None) -> tuple[int, ...]:
    import re
    nums = [int(x) for x in re.findall(r"\d+", (v or "").lstrip("vV"))]
    return tuple(nums[:4]) if nums else (0,)


@app.get("/engine/app-update")
def check_app_update():
    """Cek versi terbaru Klipku dari GitHub Releases."""
    import httpx
    releases_url = f"https://github.com/{GITHUB_REPO}/releases"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Klipku/{VERSION}"}
    try:
        r = httpx.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                      headers=headers, timeout=15, follow_redirects=True)
        if r.status_code == 404:
            # belum ada GitHub Release: coba cek tag terbaru
            rt = httpx.get(f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=1",
                           headers=headers, timeout=15, follow_redirects=True)
            tags = rt.json() if rt.status_code == 200 else []
            if not tags:
                return {"current": VERSION, "latest": None, "has_update": False, "url": releases_url,
                        "message": "Belum ada rilis publik di GitHub."}
            tag = (tags[0].get("name") or "").lstrip("vV")
            has_update = _ver_tuple(tag) > _ver_tuple(VERSION)
            return {"current": VERSION, "latest": tag, "has_update": has_update,
                    "url": f"https://github.com/{GITHUB_REPO}/releases/tag/{tags[0].get('name')}"}
        if r.status_code == 403:
            raise HTTPException(503, "Batas cek GitHub tercapai, coba lagi beberapa menit lagi.")
        r.raise_for_status()
        rel = r.json()
        latest = (rel.get("tag_name") or rel.get("name") or "").lstrip("vV")
        has_update = bool(latest) and _ver_tuple(latest) > _ver_tuple(VERSION)
        return {
            "current": VERSION,
            "latest": latest or VERSION,
            "has_update": has_update,
            "url": rel.get("html_url") or releases_url,
            "name": rel.get("name") or (f"v{latest}" if latest else ""),
            "published_at": rel.get("published_at"),
        }
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Tidak bisa terhubung ke GitHub: {e}") from None


@app.post("/settings/test-key/{provider}")
def test_key(provider: str):
    import httpx
    if provider not in PROVIDERS:
        raise HTTPException(404, "provider tidak dikenal")
    k = keys.get(provider)
    if not k:
        return {"ok": False, "message": "Key belum diisi"}
    try:
        r = httpx.get(PROVIDERS[provider]["base"] + "/models", headers={"Authorization": f"Bearer {k}"}, timeout=20)
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"Tidak bisa terhubung: {e}"}
    if r.status_code == 200:
        return {"ok": True, "message": "Key valid"}
    return {"ok": False, "message": f"Ditolak (HTTP {r.status_code})"}


# ---------- sesi & klip ----------

class SessionIn(BaseModel):
    url: str | None = None
    file_path: str | None = None
    provider: str | None = None
    model: str | None = None
    clip_count: int | None = None
    clip_duration: str | None = None
    format: str | None = None
    resolution: str | None = None
    subtitle_on: bool = True
    gaming_split: float | None = None
    language: str | None = None
    caption_style_id: str | None = None
    title_overlay: bool = False


FORMATS = {"face916", "blurbg916", "square", "landscape", "gaming916"}


@app.post("/sessions")
def create_session(body: SessionIn):
    if bool(body.url) == bool(body.file_path):
        raise HTTPException(400, "Isi salah satu: link video atau file lokal")
    if body.url and not body.url.strip().lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Link harus diawali http:// atau https://")
    if body.file_path and not Path(body.file_path).is_file():
        raise HTTPException(400, f"File tidak ditemukan: {body.file_path}")
    if body.format and body.format not in FORMATS:
        raise HTTPException(400, "format tidak dikenal")
    if body.clip_count is not None and not 1 <= body.clip_count <= 10:
        raise HTTPException(400, "Jumlah klip 1 sampai 10")
    return pipeline.create_session(body.model_dump())


@app.get("/sessions")
def list_sessions(limit: int = 50, offset: int = 0):
    rows = db.all_(
        "SELECT s.*, (SELECT COUNT(*) FROM clips c WHERE c.session_id=s.id AND c.status='done') AS done_count, "
        "(SELECT c.id FROM clips c WHERE c.session_id=s.id AND c.thumb_path IS NOT NULL ORDER BY c.rank LIMIT 1) AS cover_clip "
        "FROM sessions s ORDER BY s.created_at DESC LIMIT ? OFFSET ?", (limit, offset))
    return {"sessions": rows}


@app.get("/sessions/{sid}")
def get_session(sid: str):
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "sesi tidak ada")
    s["clips"] = db.all_("SELECT * FROM clips WHERE session_id=? ORDER BY score_total DESC, rank", (sid,))
    for c in s["clips"]:
        t = db.one("SELECT path FROM crop_tracks WHERE clip_id=?", (c["id"],))
        c["tracking"] = None
        if t:
            a = json.loads(t["path"])
            c["tracking"] = {
                "shots": len(a["shots"]),
                "dual_shots": sum(1 for sh in a["shots"] if len(sh["tracks"]) >= 2),
                "face_shots": sum(1 for sh in a["shots"] if sh["tracks"]),
                "facecam": bool(a.get("facecam")),
            }
    s["result_dir"] = str(pipeline.result_dir(s))
    return s


@app.get("/sessions/{sid}/events")
async def session_events(sid: str, since: int = 0):
    async def gen():
        seq = since
        while True:
            evs = await asyncio.to_thread(pipeline.events_since, sid, seq, 15.0)
            if not evs:
                yield ": ping\n\n"
                continue
            for e in evs:
                seq = e["seq"] + 1
                yield f"data: {json.dumps(e)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/sessions/{sid}/cancel")
def cancel_session(sid: str):
    pipeline.cancel(sid)
    return {"ok": True}


@app.post("/sessions/{sid}/retry")
def retry_session(sid: str):
    s = db.one("SELECT stage FROM sessions WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "sesi tidak ada")
    if s["stage"] not in ("error", "cancelled"):
        raise HTTPException(400, "Sesi ini tidak sedang gagal atau dibatalkan")
    pipeline.retry(sid)
    return {"ok": True}


@app.get("/active")
def active_session():
    """Sesi yang sedang berjalan (untuk indikator di sidebar)."""
    s = db.one("SELECT id, title, source_url, stage FROM sessions WHERE stage IN "
               "('queued','fetch','transcribe','detect','render') ORDER BY updated_at DESC LIMIT 1")
    if not s:
        return {"session": None}
    last = next((e for e in reversed(pipeline._events.get(s["id"], [])) if e["kind"] == "progress"), None)
    s["pct"] = last["pct"] if last and last["stage"] == s["stage"] else 0
    s["detail"] = last.get("detail") if last and last["stage"] == s["stage"] else None
    return {"session": s}


@app.post("/sessions/{sid}/render-all")
def render_all(sid: str):
    if not db.one("SELECT id FROM sessions WHERE id=?", (sid,)):
        raise HTTPException(404, "sesi tidak ada")
    pipeline.render_again(sid)
    return {"ok": True}


@app.delete("/sessions/{sid}")
def delete_session(sid: str):
    pipeline.cancel(sid)
    pipeline.cleanup_work(sid)
    db.exec_("DELETE FROM sessions WHERE id=?", (sid,))
    return {"ok": True}


def _clean_rect(v):
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        x, y, w, h = (float(n) for n in v)
    except (TypeError, ValueError):
        return None
    w = max(0.05, min(1.0, w))
    h = max(0.05, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def _clean_gaming_crop(raw) -> str:
    if not isinstance(raw, dict):
        return "{}"
    mode = raw.get("mode")
    if mode not in ("auto", "manual"):
        mode = "auto"
    out = {"mode": mode}
    for k in ("facecam", "gameplay"):
        rect = _clean_rect(raw.get(k))
        if rect:
            out[k] = rect
    return json.dumps(out)


class ClipPatch(BaseModel):
    caption_style_id: str | None = None
    caption_overrides: dict[str, str] | None = None
    title_overlay: bool | None = None
    hook_title: str | None = None
    face_tracking: bool | None = None
    format: str | None = None
    subtitle_on: bool | None = None
    resolution: str | None = None
    gaming_split: float | None = None
    gaming_crop: dict | None = None
    start_s: float | None = None
    end_s: float | None = None


@app.patch("/clips/{cid}")
def patch_clip(cid: str, body: ClipPatch):
    c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "klip tidak ada")
    data = body.model_dump(exclude_unset=True)
    if "format" in data and data["format"] not in FORMATS:
        raise HTTPException(400, "format tidak dikenal")
    if "subtitle_on" in data:
        data["subtitle_on"] = 1 if data["subtitle_on"] else 0
    if "face_tracking" in data:
        data["face_tracking"] = 1 if data["face_tracking"] else 0
    if "title_overlay" in data:
        data["title_overlay"] = 1 if data["title_overlay"] else 0
    if "gaming_crop" in data:
        data["gaming_crop"] = _clean_gaming_crop(data["gaming_crop"])
    if "caption_overrides" in data:
        clean = {str(int(k)): str(v)[:60] for k, v in (data["caption_overrides"] or {}).items() if str(k).isdigit()}
        data["caption_overrides"] = json.dumps(clean, ensure_ascii=False)
    if data.get("caption_style_id") and not db.one("SELECT id FROM caption_styles WHERE id=?",
                                                    (data["caption_style_id"],)):
        raise HTTPException(400, "preset caption tidak ada")
    if "hook_title" in data:
        data["hook_title"] = (data["hook_title"] or "").strip()[:100] or c["hook_title"]
    if "start_s" in data or "end_s" in data:
        s_ = data.get("start_s", c["start_s"])
        e_ = data.get("end_s", c["end_s"])
        if s_ < 0 or e_ - s_ < 3:
            raise HTTPException(400, "Durasi klip minimal 3 detik")
    db.update("clips", cid, data)
    if "start_s" in data or "end_s" in data:
        db.exec_("DELETE FROM crop_tracks WHERE clip_id=?", (cid,))  # rentang berubah: analisis ulang
    return db.one("SELECT * FROM clips WHERE id=?", (cid,))


class SessionGamingCropIn(BaseModel):
    gaming_crop: dict
    gaming_split: float | None = None
    set_format: bool = True


@app.put("/sessions/{sid}/gaming-crop")
def apply_session_gaming_crop(sid: str, body: SessionGamingCropIn):
    if not db.one("SELECT id FROM sessions WHERE id=?", (sid,)):
        raise HTTPException(404, "sesi tidak ada")
    gc_json = _clean_gaming_crop(body.gaming_crop)
    fields = {"gaming_crop": gc_json}
    if body.gaming_split is not None:
        fields["gaming_split"] = max(0.25, min(0.60, float(body.gaming_split)))
    if body.set_format:
        fields["format"] = "gaming916"
    clips = db.all_("SELECT id FROM clips WHERE session_id=?", (sid,))
    for c in clips:
        db.update("clips", c["id"], fields)
    return {"ok": True, "updated": len(clips)}


@app.post("/clips/{cid}/render")
def render_clip(cid: str):
    c = db.one("SELECT session_id FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "klip tidak ada")
    pipeline.render_again(c["session_id"], [cid])
    return {"ok": True}


@app.get("/clips/{cid}/words")
def clip_words(cid: str):
    """Kata-kata di rentang klip (indeks global transkrip), waktu relatif ke awal klip."""
    c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "klip tidak ada")
    s = db.one("SELECT width, height FROM sessions WHERE id=?", (c["session_id"],))
    tr = db.one("SELECT words FROM transcripts WHERE session_id=?", (c["session_id"],))
    ct = db.one("SELECT path FROM crop_tracks WHERE clip_id=?", (cid,))
    fc = json.loads(ct["path"]).get("facecam") if ct else None
    words = json.loads(tr["words"]) if tr else []
    ov = json.loads(c["caption_overrides"] or "{}")
    out = []
    for i, w in enumerate(words):
        if w["e"] <= c["start_s"] or w["s"] >= c["end_s"]:
            continue
        out.append({"i": i, "orig": w["t"], "t": ov.get(str(i), w["t"]),
                    "s": round(max(w["s"], c["start_s"]) - c["start_s"], 3),
                    "e": round(min(w["e"], c["end_s"]) - c["start_s"], 3)})
    return {
        "clip": c, "words": out, "style": styles.get(c["caption_style_id"]),
        "source": {"width": s["width"] if s else 1920, "height": s["height"] if s else 1080},
        "facecam": fc,
    }


class TitleIn(BaseModel):
    hint: str | None = None


@app.post("/clips/{cid}/regenerate-title")
def regenerate_title(cid: str, body: TitleIn):
    from detect import regenerate_titles
    from proc import Job
    c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "klip tidak ada")
    s = db.one("SELECT provider, model FROM sessions WHERE id=?", (c["session_id"],))
    st = db.get_settings()
    provider = st["provider"] or s["provider"]
    model = st["model"] or s["model"]
    text = " ".join(w["t"] for w in clip_words(cid)["words"])
    try:
        titles = regenerate_titles(text, c["hook_title"], provider, model, keys.get(provider), Job(), body.hint)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None
    return {"titles": titles}


# ---------- preset caption & font ----------

class StyleIn(BaseModel):
    name: str
    font_family: str
    font_size: int
    color: str
    highlight_color: str
    outline_color: str = "#000000"
    outline_px: int = 6
    bold: int = 0
    uppercase: int = 1
    box: int = 0
    words_per_line: int = 3
    position_y: float = 0.70


def _check_style(b: StyleIn):
    import re
    if b.font_family not in styles.FONT_FILES:
        raise HTTPException(400, "font tidak tersedia")
    for col in (b.color, b.highlight_color, b.outline_color):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", col):
            raise HTTPException(400, f"warna tidak valid: {col}")
    if not (20 <= b.font_size <= 200 and 0 <= b.outline_px <= 40 and 1 <= b.words_per_line <= 8
            and 0.05 <= b.position_y <= 0.95 and b.name.strip()):
        raise HTTPException(400, "nilai gaya di luar batas")


@app.get("/caption-styles")
def list_styles():
    return {"styles": styles.all_(), "fonts": list(styles.FONT_FILES), "default": db.get_settings()["caption_style_id"]}


@app.post("/caption-styles")
def create_style(b: StyleIn):
    _check_style(b)
    if db.one("SELECT id FROM caption_styles WHERE name=?", (b.name.strip(),)):
        raise HTTPException(400, "Nama preset sudah dipakai")
    sid = db.new_id()
    d = b.model_dump()
    d["name"] = d["name"].strip()
    cols = ["id", *d.keys(), "builtin", "created_at", "updated_at"]
    db.exec_(f"INSERT INTO caption_styles({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
             (sid, *d.values(), 0, db.now(), db.now()))
    return styles.get(sid)


@app.delete("/caption-styles/{sid}")
def delete_style(sid: str):
    s = db.one("SELECT builtin FROM caption_styles WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "preset tidak ada")
    if s["builtin"]:
        raise HTTPException(400, "Preset bawaan tidak bisa dihapus")
    db.exec_("DELETE FROM caption_styles WHERE id=?", (sid,))
    return {"ok": True}


@app.get("/fonts/{name}")
def font_file(name: str):
    if name not in styles.FONT_FILES.values():
        raise HTTPException(404, "font tidak ada")
    return FileResponse(config.FONTS / name, media_type="font/ttf")


@app.get("/sessions/{sid}/source")
def session_source(sid: str):
    s = db.one("SELECT source_path FROM sessions WHERE id=?", (sid,))
    if not s or not s["source_path"] or not Path(s["source_path"]).exists():
        raise HTTPException(404, "video sumber tidak ada")
    return FileResponse(s["source_path"])


def _clip_file(cid, field):
    c = db.one(f"SELECT {field} FROM clips WHERE id=?", (cid,))
    if not c or not c[field] or not Path(c[field]).exists():
        raise HTTPException(404, "file tidak ada")
    return c[field]


@app.get("/clips/{cid}/video")
def clip_video(cid: str):
    return FileResponse(_clip_file(cid, "output_path"), media_type="video/mp4")


@app.get("/clips/{cid}/thumb")
def clip_thumb(cid: str):
    return FileResponse(_clip_file(cid, "thumb_path"), media_type="image/jpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
