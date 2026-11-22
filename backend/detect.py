"""Tahap deteksi momen: LLM membaca transkrip bernomor, memilih klip, memberi skor + judul hook.

Transkrip dipecah per jendela (~1800 kata) supaya muat di limit token per menit tier gratis.
Hasil semua jendela digabung, dibuang yang tumpang tindih, diambil N teratas.
"""
import json
import math
import re
import time

import httpx

from config import PROVIDERS
from proc import Cancelled

DURATIONS = {"30": (20, 40), "60": (45, 75), "30-60": (25, 65)}
WINDOW_WORDS = 1800

SYSTEM = """Kamu editor video pendek (TikTok, Reels, Shorts) yang sangat paham apa yang bikin orang berhenti scroll.
Tugasmu: dari transkrip bernomor, pilih momen yang paling layak jadi klip berdiri sendiri.

Aturan memilih:
- Klip harus utuh: punya awal yang langsung menarik dan akhir yang tuntas (bukan terpotong di tengah kalimat atau ide).
- Kalimat pertama klip harus kuat sebagai hook: pernyataan berani, pertanyaan, angka, konflik, atau cerita yang langsung mulai.
- Jangan pilih bagian sponsor, iklan, ajakan subscribe, salam pembuka, atau penutup.
- Klip tidak boleh saling tumpang tindih.

Skor 0-100:
- hook: seberapa kuat 3 detik pertama menahan penonton
- flow: seberapa utuh dan enak alurnya dari awal sampai akhir
- value: seberapa berguna, lucu, mengejutkan, atau emosional isinya

Judul hook: bahasa yang sama dengan transkrip, maksimal 60 karakter, gaya AIDA (menarik perhatian dan bikin penasaran), tanpa hashtag, tanpa emoji, tanpa clickbait palsu.

Balas HANYA JSON dengan bentuk:
{"clips":[{"start":<nomor baris awal>,"end":<nomor baris akhir>,"hook":<0-100>,"flow":<0-100>,"value":<0-100>,"title":"...","reason":"<1 kalimat alasan>"}]}"""


def build_units(words):
    """Gabungkan kata jadi kalimat. Putus di tanda baca akhir, jeda > 0.8 dtk, atau 30 kata."""
    units, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_punct = re.search(r"[.?!…]$", w["t"])
        gap = (nxt["s"] - w["e"]) if nxt else 99
        if end_punct or gap > 0.8 or len(cur) >= 30 or nxt is None:
            units.append({
                "w0": cur[0], "w1": cur[-1],
                "s": words[cur[0]]["s"], "e": words[cur[-1]]["e"],
                "text": " ".join(words[j]["t"] for j in cur),
            })
            cur = []
    return units


def fmt_time(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"


def windows(units):
    out, cur, n = [], [], 0
    for i, u in enumerate(units):
        cur.append(i)
        n += len(u["text"].split())
        if n >= WINDOW_WORDS:
            out.append(cur)
            cur, n = [], 0
    if cur:
        out.append(cur)
    return out


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("tidak ada JSON")
    return json.loads(text[i:j + 1])


class AuthError(RuntimeError):
    """Key ditolak: tidak ada gunanya mencoba bagian transkrip berikutnya."""


def _err_message(r):
    try:
        return r.json()["error"]["message"]
    except Exception:  # noqa: BLE001
        return r.text[:200]


def chat(provider, model, key, system, user, job, json_mode=True):
    p = PROVIDERS[provider]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.4,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if provider == "groq" and model.startswith("openai/gpt-oss"):
        # model reasoning: jatah token habis untuk berpikir -> jawaban kosong. Pendekkan.
        body["reasoning_effort"] = "low"
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers["X-Title"] = "Klipku"
    empty_retry = True
    for attempt in range(5):
        job.check()
        r = httpx.post(p["base"] + "/chat/completions", json=body, headers=headers,
                       timeout=httpx.Timeout(180, connect=20))
        if r.status_code == 429 and attempt < 4:
            wait = float(r.headers.get("retry-after", "15") or 15)
            time.sleep(min(max(wait, 2), 65))
            continue
        if r.status_code in (401, 403):
            raise AuthError(f"API key {p['label']} ditolak. Cek di Pengaturan.")
        if r.status_code == 400 and json_mode and any(
                k in r.text for k in ("json_validate_failed", "response_format", "failed_generation")):
            # mode JSON ditolak / jawaban kosong: ulangi tanpa mode JSON, JSON dibaca manual
            return chat(provider, model, key, system, user, job, json_mode=False)
        if r.status_code >= 500 and attempt < 2:
            time.sleep(3)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{p['label']} menolak permintaan (HTTP {r.status_code}): {_err_message(r)}")
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not content and empty_retry:
            empty_retry = False
            continue
        return content
    raise RuntimeError(f"{p['label']} kena limit terus (HTTP 429). Coba model lain atau tunggu sebentar.")


def fit_duration(units, a, b, lo, hi, total_dur):
    """Rapikan rentang unit [a..b] agar durasi masuk lo..hi detik, tetap di batas kalimat."""
    a, b = max(0, min(a, len(units) - 1)), max(0, min(b, len(units) - 1))
    if b < a:
        a, b = b, a
    while b > a and units[b]["e"] - units[a]["s"] > hi:
        b -= 1
    while units[b]["e"] - units[a]["s"] < lo and b + 1 < len(units) and units[b + 1]["e"] - units[a]["s"] <= hi:
        b += 1
    start = max(0.0, units[a]["s"] - 0.15)
    end = min(total_dur, units[b]["e"] + 0.35)
    return a, b, start, end


def detect(transcript, total_dur, clip_count, clip_duration, provider, model, key, job, emit):
    if not key:
        raise RuntimeError(f"API key {PROVIDERS[provider]['label']} belum diisi. Buka Pengaturan.")
    units = build_units(transcript["words"])
    lo, hi = DURATIONS.get(clip_duration, DURATIONS["30-60"])
    wins = windows(units)
    per_win = max(2, math.ceil(clip_count * 1.5 / len(wins)) + 1)
    cands = []
    failed = []
    for wi, idxs in enumerate(wins):
        lines = "\n".join(f"[{i}] ({fmt_time(units[i]['s'])}) {units[i]['text']}" for i in idxs)
        user = (
            f"Durasi tiap klip harus {lo}-{hi} detik. Pilih maksimal {per_win} klip terbaik dari bagian ini.\n"
            f"Nomor baris valid: {idxs[0]} sampai {idxs[-1]}.\n\nTRANSKRIP:\n{lines}"
        )
        emit("progress", stage="detect", pct=round(wi * 100 / len(wins), 1),
             detail=f"AI membaca transkrip bagian {wi + 1} dari {len(wins)}" if len(wins) > 1 else None)
        try:
            raw = chat(provider, model, key, SYSTEM, user, job)
            data = parse_json(raw)
        except (AuthError, Cancelled):
            raise
        except Exception as e:  # noqa: BLE001  satu bagian gagal: lewati, lanjut bagian lain
            failed.append(str(e))
            emit("log", text=f"Bagian {wi + 1} dari {len(wins)} dilewati: {e}")
            continue
        for c in data.get("clips", []):
            try:
                a, b = int(c["start"]), int(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if a < idxs[0] or a > idxs[-1]:
                continue
            b = max(a, min(b, idxs[-1]))
            a, b, start, end = fit_duration(units, a, b, lo, hi, total_dur)
            if end - start < lo * 0.6:
                continue
            sc = {k: max(0, min(100, int(c.get(k, 50) or 50))) for k in ("hook", "flow", "value")}
            cands.append({
                "start_s": round(start, 3), "end_s": round(end, 3),
                "score_hook": sc["hook"], "score_flow": sc["flow"], "score_value": sc["value"],
                "score_total": round(sc["hook"] * 0.4 + sc["flow"] * 0.3 + sc["value"] * 0.3),
                "hook_title": (c.get("title") or units[a]["text"])[:80].strip(),
                "reason": (c.get("reason") or "")[:300],
            })
    emit("progress", stage="detect", pct=100)
    cands.sort(key=lambda c: c["score_total"], reverse=True)
    chosen = []
    for c in cands:
        if all(c["end_s"] <= o["start_s"] or c["start_s"] >= o["end_s"] for o in chosen):
            chosen.append(c)
        if len(chosen) >= clip_count:
            break
    if not chosen:
        if failed and len(failed) == len(wins):
            raise RuntimeError(f"Semua bagian transkrip gagal dibaca AI. Error terakhir: {failed[-1]} "
                               "Coba lagi, atau ganti model/provider di Pengaturan.")
        raise RuntimeError("AI tidak menemukan momen yang cocok. Coba durasi klip lain atau model lain.")
    if failed:
        emit("log", text=f"{len(failed)} dari {len(wins)} bagian transkrip dilewati karena error AI.")
    return chosen


TITLE_SYSTEM = """Kamu copywriter video pendek. Buat judul hook untuk klip dari transkripnya.
Aturan: bahasa yang sama dengan transkrip, maksimal 60 karakter, bikin penasaran tapi jujur sesuai isi,
tanpa hashtag, tanpa emoji. Variasikan gayanya: pertanyaan, pernyataan berani, dan angka/manfaat.
Balas HANYA JSON: {"titles": ["...", "...", "..."]}"""


def regenerate_titles(text, current, provider, model, key, job, hint=None):
    if not key:
        raise RuntimeError(f"API key {PROVIDERS[provider]['label']} belum diisi. Buka Pengaturan.")
    user = f"Judul sekarang: {current}\n"
    if hint:
        user += f"Arahan tambahan: {hint}\n"
    user += f"\nTRANSKRIP KLIP:\n{text[:6000]}"
    raw = chat(provider, model, key, TITLE_SYSTEM, user, job)
    try:
        titles = [str(t).strip()[:80] for t in parse_json(raw).get("titles", []) if str(t).strip()]
    except Exception:  # noqa: BLE001
        titles = []
    if not titles:
        raise RuntimeError("AI tidak mengembalikan judul. Coba lagi.")
    return titles[:3]
