"""Akses SQLite. Satu koneksi per thread, WAL mode, foreign keys ON."""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from config import DATA, DB_PATH, SCHEMA

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return str(uuid.uuid4())


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DATA.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        _local.conn = c
    return c


def init():
    c = conn()
    exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='sessions'").fetchone()
    if not exists:
        c.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrate(c)
    # sesi yang terputus karena app ditutup paksa: tandai error, jangan menggantung
    c.execute(
        "UPDATE sessions SET stage='error', error='App ditutup saat proses berjalan', updated_at=? "
        "WHERE stage IN ('queued','fetch','transcribe','detect','render')",
        (now(),),
    )
    c.execute("UPDATE clips SET status='failed', error='App ditutup saat render' WHERE status IN ('tracking','rendering')")
    c.commit()


def _migrate(c):
    """Tambah kolom baru ke DB versi lama (ALTER TABLE ADD COLUMN aman dijalankan ulang)."""
    scols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
    if "opts" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN opts TEXT NOT NULL DEFAULT '{}'")
    cols = {r[1] for r in c.execute("PRAGMA table_info(clips)")}
    if "title_overlay" not in cols:
        c.execute("ALTER TABLE clips ADD COLUMN title_overlay INTEGER NOT NULL DEFAULT 0 CHECK (title_overlay IN (0,1))")
    c.commit()


def row(r):
    return dict(r) if r is not None else None


def one(sql, args=()):
    return row(conn().execute(sql, args).fetchone())


def all_(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def exec_(sql, args=()):
    c = conn()
    c.execute(sql, args)
    c.commit()


def update(table: str, id_: str, fields: dict, key: str = "id"):
    if not fields:
        return
    fields = {**fields}
    if table in ("sessions", "clips", "caption_styles"):
        fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    exec_(f"UPDATE {table} SET {cols} WHERE {key}=?", (*fields.values(), id_))


# ---------- settings (key-value JSON) ----------

DEFAULT_SETTINGS = {
    "provider": "groq",
    "model": "openai/gpt-oss-120b",
    "transcribe_mode": "online",
    "output_dir": None,  # None = Videos\Klipku
    "clip_count": 5,
    "clip_duration": "30-60",
    "resolution": "1080p",
    "caption_style_id": "builtin-tegas-kuning",
    "whisper_model": "small",
    "watermark": {"mode": "off", "text": "", "position": "tr", "opacity": 0.85, "size": 0.18},
}


def get_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    for r in all_("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out


def set_setting(key: str, value):
    exec_(
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value), now()),
    )
