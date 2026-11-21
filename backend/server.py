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
import db
import keys
import pipeline
from config import DENO, FFMPEG, PROVIDERS, TOKEN, YUNET
from server_util import first_line, run

VERSION = "0.1.0"
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

class SettingsIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    output_dir: str | None = None
    clip_count: int | None = None
    clip_duration: str | None = None
    resolution: str | None = None


@app.get("/settings")
def get_settings():
    st = db.get_settings()
    st["output_dir_effective"] = str(pipeline.output_root())
    return {"settings": st, "keys": keys.status(PROVIDERS), "providers": PROVIDERS}


@app.put("/settings")
def put_settings(body: SettingsIn):
    data = body.model_dump(exclude_unset=True)
    if "provider" in data and data["provider"] not in PROVIDERS:
        raise HTTPException(400, "provider tidak dikenal")
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


class ClipPatch(BaseModel):
    hook_title: str | None = None
    format: str | None = None
    subtitle_on: bool | None = None
    resolution: str | None = None
    gaming_split: float | None = None
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
    db.update("clips", cid, data)
    return db.one("SELECT * FROM clips WHERE id=?", (cid,))


@app.post("/clips/{cid}/render")
def render_clip(cid: str):
    c = db.one("SELECT session_id FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "klip tidak ada")
    pipeline.render_again(c["session_id"], [cid])
    return {"ok": True}


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
