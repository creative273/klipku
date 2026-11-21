"""Antrian kerja + pipeline fetch -> transcribe -> detect -> render.

Satu worker thread: satu pekerjaan jalan pada satu waktu supaya laptop tetap ringan.
Progres dikirim sebagai event (dibaca UI lewat SSE).
"""
import json
import queue
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import db
import keys
from config import DEFAULT_OUTPUT
from detect import detect
from fetch import download, safe_name
from proc import Cancelled, Job, probe
from render import render_clip
from tracking import analyze
from transcribe import transcribe_online

_q: "queue.Queue[tuple]" = queue.Queue()
_jobs: dict[str, Job] = {}
_events: dict[str, list] = {}
_cond = threading.Condition()


# ---------- event ----------

def emit(session_id, kind, **data):
    with _cond:
        lst = _events.setdefault(session_id, [])
        lst.append({"seq": len(lst), "kind": kind, "ts": time.time(), **data})
        if len(lst) > 2000:
            del lst[:500]
            for i, e in enumerate(lst):
                e["seq"] = i
        _cond.notify_all()


def events_since(session_id, seq, timeout=15.0):
    with _cond:
        lst = _events.get(session_id, [])
        if len(lst) <= seq:
            _cond.wait(timeout)
            lst = _events.get(session_id, [])
        return lst[seq:]


def log_line(session_id, text):
    emit(session_id, "log", text=text)
    s = db.one("SELECT output_dir FROM sessions WHERE id=?", (session_id,))
    if s:
        try:
            p = work_dir(session_id, s["output_dir"])
            p.mkdir(parents=True, exist_ok=True)
            with open(p / "pipeline.log", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%H:%M:%S} {text}\n")
        except OSError:
            pass


# ---------- path ----------

def output_root() -> Path:
    d = db.get_settings().get("output_dir")
    return Path(d) if d else DEFAULT_OUTPUT


def work_dir(session_id, output_dir) -> Path:
    return Path(output_dir) / ".work" / session_id


def result_dir(session) -> Path:
    created = session["created_at"][:10]
    return Path(session["output_dir"]) / f"{created} {safe_name(session['title'] or 'video', 50)} ({session['id'][:4]})"


# ---------- API dipanggil server ----------

def create_session(params: dict) -> dict:
    st = db.get_settings()
    sid = db.new_id()
    out = Path(params.get("output_dir") or output_root())
    out.mkdir(parents=True, exist_ok=True)
    src_type = "url" if params.get("url") else "file"
    db.exec_(
        "INSERT INTO sessions(id, source_type, source_url, source_path, title, provider, model, transcribe_mode, "
        "clip_count, clip_duration, stage, output_dir, language, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid, src_type, params.get("url"), params.get("file_path"),
            Path(params["file_path"]).stem if src_type == "file" else None,
            params.get("provider") or st["provider"], params.get("model") or st["model"],
            "online", int(params.get("clip_count") or st["clip_count"]),
            params.get("clip_duration") or st["clip_duration"], "queued", str(out),
            params.get("language"), db.now(), db.now(),
        ),
    )
    opts = {
        "format": params.get("format") or "face916",
        "resolution": params.get("resolution") or st["resolution"],
        "subtitle_on": 1 if params.get("subtitle_on", True) else 0,
        "gaming_split": float(params.get("gaming_split") or 0.40),
    }
    _jobs[sid] = Job()
    _q.put(("pipeline", sid, opts))
    emit(sid, "stage", stage="queued")
    return db.one("SELECT * FROM sessions WHERE id=?", (sid,))


def render_again(session_id, clip_ids=None):
    _jobs[session_id] = Job()
    _q.put(("render", session_id, {"clip_ids": clip_ids}))


def cancel(session_id):
    job = _jobs.get(session_id)
    if job:
        job.cancel()


def start_worker():
    threading.Thread(target=_worker, name="klipku-worker", daemon=True).start()


# ---------- worker ----------

def _worker():
    while True:
        kind, sid, opts = _q.get()
        job = _jobs.get(sid) or Job()
        try:
            if kind == "pipeline":
                _run_pipeline(sid, opts, job)
            else:
                _run_render(sid, opts.get("clip_ids"), job)
        except Cancelled:
            db.update("sessions", sid, {"stage": "cancelled", "error": None})
            db.exec_("UPDATE clips SET status='cancelled' WHERE session_id=? AND status IN ('pending','rendering')", (sid,))
            emit(sid, "stage", stage="cancelled")
            log_line(sid, "Dibatalkan.")
        except Exception as e:  # noqa: BLE001
            msg = str(e) or e.__class__.__name__
            db.update("sessions", sid, {"stage": "error", "error": msg})
            emit(sid, "stage", stage="error", error=msg)
            log_line(sid, "ERROR " + msg + "\n" + traceback.format_exc()[-1500:])
        finally:
            _q.task_done()


def _stage(sid, stage):
    db.update("sessions", sid, {"stage": stage})
    emit(sid, "stage", stage=stage)
    log_line(sid, f"== {stage}")


def _run_pipeline(sid, opts, job: Job):
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
    work = work_dir(sid, s["output_dir"])
    work.mkdir(parents=True, exist_ok=True)
    ev = lambda kind, **d: emit(sid, kind, **d)  # noqa: E731

    # 1. fetch
    _stage(sid, "fetch")
    if s["source_type"] == "url":
        got = download(s["source_url"], work, opts["resolution"], job, ev)
        src, title = got["path"], got["title"]
    else:
        src, title = Path(s["source_path"]), s["title"]
        if not src.exists():
            raise RuntimeError(f"File tidak ditemukan: {src}")
    info = probe(src)
    db.update("sessions", sid, {
        "source_path": str(src), "title": title, "duration_s": info["duration"],
        "width": info["width"], "height": info["height"], "fps": info["fps"],
    })
    log_line(sid, f"Video: {title} | {info['width']}x{info['height']} | {info['duration']:.0f} detik")

    # 2. transcribe
    _stage(sid, "transcribe")
    tr = transcribe_online(src, info["duration"], work, keys.get("groq"), s["language"], job, ev)
    db.exec_("INSERT OR REPLACE INTO transcripts(session_id, engine, words, segments, created_at) VALUES(?,?,?,?,?)",
             (sid, tr["engine"], json.dumps(tr["words"]), json.dumps(tr["segments"]), db.now()))
    if tr.get("language") and not s["language"]:
        db.update("sessions", sid, {"language": tr["language"]})
    log_line(sid, f"Transkrip: {len(tr['words'])} kata")

    # 3. detect
    _stage(sid, "detect")
    found = detect(tr, info["duration"], s["clip_count"], s["clip_duration"],
                   s["provider"], s["model"], keys.get(s["provider"]), job, ev)
    db.exec_("DELETE FROM clips WHERE session_id=?", (sid,))
    for rank, c in enumerate(found, 1):
        db.exec_(
            "INSERT INTO clips(id, session_id, rank, start_s, end_s, score_total, score_hook, score_flow, score_value, "
            "reason, hook_title, format, subtitle_on, gaming_split, resolution, status, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.new_id(), sid, rank, c["start_s"], c["end_s"], c["score_total"], c["score_hook"], c["score_flow"],
             c["score_value"], c["reason"], c["hook_title"], opts["format"], opts["subtitle_on"],
             opts["gaming_split"], opts["resolution"], "pending", db.now(), db.now()),
        )
    log_line(sid, f"Momen ditemukan: {len(found)}")
    emit(sid, "clips")

    # 4. render
    _run_render(sid, None, job)


def _run_render(sid, clip_ids, job: Job):
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
    tr = db.one("SELECT words FROM transcripts WHERE session_id=?", (sid,))
    if not tr:
        raise RuntimeError("Transkrip sesi ini tidak ada")
    words = json.loads(tr["words"])
    clips = db.all_("SELECT * FROM clips WHERE session_id=? ORDER BY rank", (sid,))
    if clip_ids:
        clips = [c for c in clips if c["id"] in clip_ids]
    _stage(sid, "render")
    src = Path(s["source_path"])
    if not src.exists():
        raise RuntimeError(f"Video sumber sudah tidak ada: {src}")
    work = work_dir(sid, s["output_dir"])
    work.mkdir(parents=True, exist_ok=True)
    res = result_dir(s)
    res.mkdir(parents=True, exist_ok=True)
    failed = 0
    for i, c in enumerate(clips):
        job.check()
        db.update("clips", c["id"], {"status": "rendering", "error": None})
        emit(sid, "clip", id=c["id"], status="rendering")
        emit(sid, "progress", stage="render", pct=round(i * 100 / len(clips), 1))
        out = res / f"{c['rank']:02d} - {safe_name(c['hook_title'], 60)}.mp4"
        old = c.get("output_path")
        try:
            t0 = time.time()
            analysis = None
            if c["face_tracking"] and c["format"] in TRACKED:
                analysis = _analysis(sid, s, c, src, job)
                db.update("clips", c["id"], {"status": "rendering"})
                emit(sid, "clip", id=c["id"], status="rendering")
            thumb = render_clip(src, s["width"], s["height"], c, words, out, work, job, analysis=analysis)
            if old and old != str(out) and Path(old).exists():
                Path(old).unlink(missing_ok=True)
            db.update("clips", c["id"], {"status": "done", "output_path": str(out), "thumb_path": str(thumb)})
            emit(sid, "clip", id=c["id"], status="done")
            log_line(sid, f"Klip {c['rank']} selesai ({time.time() - t0:.0f} dtk): {out.name}")
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001  satu klip gagal tidak membatalkan yang lain
            failed += 1
            db.update("clips", c["id"], {"status": "failed", "error": str(e)[-500:]})
            emit(sid, "clip", id=c["id"], status="failed", error=str(e)[-300:])
            log_line(sid, f"Klip {c['rank']} gagal: {e}")
    emit(sid, "progress", stage="render", pct=100)
    db.update("sessions", sid, {"stage": "done", "error": f"{failed} klip gagal" if failed else None})
    emit(sid, "stage", stage="done", failed=failed)
    log_line(sid, "Selesai.")


TRACKED = {"face916", "square", "gaming916"}


def _analysis(sid, s, c, src, job):
    """Ambil analisis wajah dari cache, atau hitung (status klip: tracking)."""
    row = db.one("SELECT path FROM crop_tracks WHERE clip_id=?", (c["id"],))
    if row:
        return json.loads(row["path"])
    db.update("clips", c["id"], {"status": "tracking"})
    emit(sid, "clip", id=c["id"], status="tracking")
    t0 = time.time()
    try:
        a = analyze(src, c["start_s"], c["end_s"] - c["start_s"], s["width"], s["height"], job, s["fps"] or 30.0)
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001  tracking gagal: render tetap jalan dengan crop tengah
        log_line(sid, f"Klip {c['rank']}: deteksi wajah gagal ({e}), pakai crop tengah.")
        return None
    db.exec_("INSERT OR REPLACE INTO crop_tracks(clip_id, mode, path, shots, created_at) VALUES(?,?,?,?,?)",
             (c["id"], "auto", json.dumps(a), json.dumps([sh["s"] for sh in a["shots"]]), db.now()))
    n2 = sum(1 for sh in a["shots"] if len(sh["tracks"]) >= 2)
    log_line(sid, f"Klip {c['rank']}: {len(a['shots'])} shot, {n2} shot 2 orang, "
                  f"facecam {'ketemu' if a.get('facecam') else 'tidak ketemu'} ({time.time() - t0:.0f} dtk)")
    return a


def cleanup_work(session_id):
    s = db.one("SELECT output_dir FROM sessions WHERE id=?", (session_id,))
    if s:
        shutil.rmtree(work_dir(session_id, s["output_dir"]), ignore_errors=True)
