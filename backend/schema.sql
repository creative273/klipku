-- Klipku (nama kerja) - schema SQLite lokal, satu user, satu laptop.
-- File DB: %APPDATA%\Klipku\klipku.db
-- Konvensi: id = TEXT uuid v4 dibuat di Python; waktu = TEXT ISO-8601 UTC ('2026-11-21T12:00:00Z');
-- durasi/posisi video = REAL detik. Tidak ada auth/RLS: app lokal satu user,
-- server Python hanya listen di 127.0.0.1 dengan token acak per sesi app.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- S01/S03/S11: satu kali Run pipeline = satu sesi
CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,
  source_type   TEXT NOT NULL CHECK (source_type IN ('url','file')),
  source_url    TEXT,
  source_path   TEXT,                 -- path file asli (file lokal) atau hasil download
  title         TEXT,
  duration_s    REAL,
  width         INTEGER,
  height        INTEGER,
  fps           REAL,
  audio_track   INTEGER NOT NULL DEFAULT 0,
  language      TEXT,                 -- 'id' | 'en' | 'mixed' | NULL (auto)
  provider      TEXT NOT NULL,        -- 'groq' | 'gemini' | 'openrouter'
  model         TEXT NOT NULL,
  transcribe_mode TEXT NOT NULL CHECK (transcribe_mode IN ('offline','online')),
  clip_count    INTEGER NOT NULL DEFAULT 5 CHECK (clip_count BETWEEN 1 AND 10),
  clip_duration TEXT NOT NULL DEFAULT '30-60' CHECK (clip_duration IN ('30','60','30-60')),
  stage         TEXT NOT NULL DEFAULT 'queued'
    CHECK (stage IN ('queued','fetch','transcribe','detect','render','done','error','cancelled')),
  error         TEXT,
  output_dir    TEXT NOT NULL,
  opts          TEXT NOT NULL DEFAULT '{}', -- pilihan saat Buat klip (format, resolusi, caption), untuk Coba lagi
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  CHECK ((source_type = 'url' AND source_url IS NOT NULL) OR (source_type = 'file' AND source_path IS NOT NULL))
);
CREATE INDEX sessions_created_idx ON sessions (created_at DESC);
CREATE INDEX sessions_stage_idx ON sessions (stage);

-- Transkrip per kata disimpan sebagai JSON (satu baris per sesi):
-- words = [{"t":"halo","s":1.20,"e":1.48,"p":0.93}], segments = [{"s":..,"e":..,"text":..}]
CREATE TABLE transcripts (
  session_id  TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  engine      TEXT NOT NULL,          -- 'faster-whisper:small' | 'groq:whisper-large-v3-turbo'
  words       TEXT NOT NULL CHECK (json_valid(words)),
  segments    TEXT NOT NULL CHECK (json_valid(segments)),
  created_at  TEXT NOT NULL
);

-- S06: preset caption (bawaan + buatan user)
CREATE TABLE caption_styles (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  builtin         INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0,1)),
  font_family     TEXT NOT NULL,
  font_size       INTEGER NOT NULL CHECK (font_size BETWEEN 20 AND 200),
  color           TEXT NOT NULL,      -- '#FFFFFF'
  highlight_color TEXT NOT NULL,      -- warna kata aktif
  outline_color   TEXT NOT NULL DEFAULT '#000000',
  outline_px      INTEGER NOT NULL DEFAULT 4,
  bold            INTEGER NOT NULL DEFAULT 1 CHECK (bold IN (0,1)),
  uppercase       INTEGER NOT NULL DEFAULT 0 CHECK (uppercase IN (0,1)),
  box             INTEGER NOT NULL DEFAULT 0 CHECK (box IN (0,1)),
  words_per_line  INTEGER NOT NULL DEFAULT 0, -- 0 = auto
  position_y      REAL NOT NULL DEFAULT 0.70 CHECK (position_y BETWEEN 0 AND 1),
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- S04/S05/S09: klip hasil deteksi AI
CREATE TABLE clips (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  rank          INTEGER NOT NULL,
  start_s       REAL NOT NULL,
  end_s         REAL NOT NULL,
  score_total   INTEGER NOT NULL CHECK (score_total BETWEEN 0 AND 100),
  score_hook    INTEGER NOT NULL CHECK (score_hook BETWEEN 0 AND 100),
  score_flow    INTEGER NOT NULL CHECK (score_flow BETWEEN 0 AND 100),
  score_value   INTEGER NOT NULL CHECK (score_value BETWEEN 0 AND 100),
  reason        TEXT,                 -- alasan AI memilih momen ini
  hook_title    TEXT NOT NULL,
  format        TEXT NOT NULL DEFAULT 'face916'
    CHECK (format IN ('face916','blurbg916','square','landscape','gaming916')),
  face_tracking INTEGER NOT NULL DEFAULT 1 CHECK (face_tracking IN (0,1)),
  subtitle_on   INTEGER NOT NULL DEFAULT 1 CHECK (subtitle_on IN (0,1)),
  title_overlay INTEGER NOT NULL DEFAULT 0 CHECK (title_overlay IN (0,1)), -- judul hook tampil 3 dtk pertama
  caption_style_id TEXT REFERENCES caption_styles(id) ON DELETE SET NULL,
  caption_overrides TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(caption_overrides)), -- {"word_idx": "teks baru"}
  gaming_split  REAL NOT NULL DEFAULT 0.40 CHECK (gaming_split BETWEEN 0.25 AND 0.60), -- tinggi bagian muka
  resolution    TEXT NOT NULL DEFAULT '1080p' CHECK (resolution IN ('720p','1080p','2k','4k')),
  status        TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','tracking','rendering','done','failed','cancelled')),
  error         TEXT,
  output_path   TEXT,
  thumb_path    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  CHECK (end_s > start_s),
  UNIQUE (session_id, rank)
);
CREATE INDEX clips_session_idx ON clips (session_id, score_total DESC);
CREATE INDEX clips_status_idx ON clips (status);
CREATE INDEX clips_style_idx ON clips (caption_style_id);

-- Hasil face tracking per klip (cache, supaya render ulang tidak tracking ulang)
-- path = [{"t":0.0,"cx":0.52,"cy":0.31,"w":0.18}] koordinat ternormalisasi 0..1, sudah dihaluskan
CREATE TABLE crop_tracks (
  clip_id     TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
  mode        TEXT NOT NULL CHECK (mode IN ('auto','center')),
  path        TEXT NOT NULL CHECK (json_valid(path)),
  shots       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(shots)), -- detik pergantian shot
  created_at  TEXT NOT NULL
);

-- S12: pengaturan key-value. API key TIDAK disimpan di sini (Windows Credential Manager via keyring).
CREATE TABLE settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL CHECK (json_valid(value)),
  updated_at  TEXT NOT NULL
);
