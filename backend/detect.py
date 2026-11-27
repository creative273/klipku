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
- Jika ada tanda [MOST REPLAYED XX%], prioritaskan momen yang mencakup bagian ramai tersebut beserta setup/konteks beberapa detik sebelumnya supaya ceritanya utuh.
- Jangan pilih bagian sponsor, iklan, ajakan subscribe, salam pembuka, atau penutup.
- Klip tidak boleh saling tumpang tindih.

Skor 0-100:
- hook: seberapa kuat 3 detik pertama menahan penonton
- flow: seberapa utuh dan enak alurnya dari awal sampai akhir
- value: seberapa berguna, lucu, mengejutkan, atau emosional isinya

peak: nomor baris di dalam klip yang paling klimaks, paling heboh/lucu, atau paling viral (akan dipakai untuk potongan teaser 5 detik di awal).
Judul hook: bahasa yang sama dengan transkrip, maksimal 60 karakter, gaya AIDA (menarik perhatian dan bikin penasaran), tanpa hashtag, tanpa emoji, tanpa clickbait palsu.

Balas HANYA JSON dengan bentuk:
{"clips":[{"start":<nomor baris awal>,"end":<nomor baris akhir>,"peak":<nomor baris klimaks>,"hook":<0-100>,"flow":<0-100>,"value":<0-100>,"title":"...","reason":"<1 kalimat alasan>"}]}"""


def _heat_max(heatmap, s, e):
    """Nilai heatmap maksimum (0..1) yang beririsan dengan rentang [s, e]."""
    best = 0.0
    for h in heatmap or ():
        if h["e"] > s and h["s"] < e and h["v"] > best:
            best = h["v"]
    return best


def _heatmap_peak_s(heatmap, s, e, hook_dur=5.0):
    """Detik awal momen paling ramai (heatmap >= 0.55) di dalam [s, e], atau None."""
    best_v, best_mid = 0.0, None
    # utamakan puncak setelah 3 detik pertama supaya teaser benar-benar menampilkan klimaks
    pref_s = s + min(3.0, max(0.0, (e - s) * 0.2))
    for h in heatmap or ():
        if h["e"] <= s or h["s"] >= e:
            continue
        mid = (max(h["s"], s) + min(h["e"], e)) / 2.0
        bonus = 0.04 if mid >= pref_s else 0.0
        if h["v"] + bonus > best_v:
            best_v = h["v"] + bonus
            best_mid = mid
    if best_mid is None or best_v < 0.55:
        return None
    hi = max(s, e - hook_dur)
    return round(max(s, min(hi, best_mid - hook_dur * 0.45)), 3)


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


COMPILE_SYSTEM = """Kamu editor video kompilasi pendek (TikTok, Reels, Shorts).
Tugasmu: dari transkrip bernomor, kumpulkan SEMUA potongan singkat (2-14 detik per potongan) yang cocok dengan permintaan pengguna untuk digabung menjadi 1 video kompilasi.

Aturan memilih potongan:
- Fokus PENUH pada permintaan pengguna. Ambil potongan-potongan yang benar-benar relevan, lucu, atau kuat.
- Jika pengguna meminta bahasa tertentu di video campur bahasa (misalnya bahasa Indonesia di livestream berbahasa Inggris), tetap deteksi kata/frasa bahasa Indonesia meski transkrip mengejanya agak fonetik atau tercampur bahasa Inggris.
- Setiap potongan harus utuh di batas ucapan (jangan terpotong di tengah kata) dan idealnya 2-14 detik.
- Potongan tidak boleh saling tumpang tindih.
- Jika tidak ada bagian yang cocok di jendela ini, balas {"snippets":[]}.

Balas HANYA JSON dengan bentuk:
{"title":"<judul kompilasi max 60 karakter, bahasa sama dengan transkrip, tanpa hashtag/emoji>","snippets":[{"start":<nomor baris awal>,"end":<nomor baris akhir>,"score":<0-100>,"note":"<ringkasan singkat 3-7 kata>"}]}"""

COMPILE_TARGETS = {"30": 35.0, "60": 65.0, "30-60": 55.0}


def _detect_compile(units, wins, total_dur, clip_duration, provider, model, key, job, emit, heatmap, prompt):
    target_dur = COMPILE_TARGETS.get(clip_duration, 55.0)
    per_win = max(5, math.ceil(16 / len(wins)) + 2)
    focus = prompt or "Momen paling heboh, lucu, dan paling sering diputar ulang di video ini"
    cands = []
    titles = []
    failed = []
    for wi, idxs in enumerate(wins):
        def fmt_line(i):
            u = units[i]
            hv = _heat_max(heatmap, u["s"], u["e"]) if heatmap else 0.0
            tag = f" [MOST REPLAYED {round(hv * 100)}%]" if hv >= 0.65 else ""
            return f"[{i}] ({fmt_time(u['s'])}){tag} {u['text']}"

        lines = "\n".join(fmt_line(i) for i in idxs)
        user = (
            f"PERMINTAAN PENGGUNA: {focus}\n"
            f"Pilih maksimal {per_win} potongan singkat terbaik (2-14 detik tiap potongan) dari bagian ini yang cocok dengan permintaan di atas.\n"
            f"Nomor baris valid: {idxs[0]} sampai {idxs[-1]}.\n\nTRANSKRIP:\n{lines}"
        )
        emit("progress", stage="detect", pct=round(wi * 100 / len(wins), 1),
             detail=f"AI mencari potongan kompilasi bagian {wi + 1} dari {len(wins)}" if len(wins) > 1
             else "AI mencari potongan kompilasi")
        try:
            raw = chat(provider, model, key, COMPILE_SYSTEM, user, job)
            data = parse_json(raw)
        except (AuthError, Cancelled):
            raise
        except Exception as e:  # noqa: BLE001
            failed.append(str(e))
            emit("log", text=f"Bagian {wi + 1} dari {len(wins)} dilewati: {e}")
            continue
        t = (data.get("title") or "").strip()[:80]
        if t:
            titles.append(t)
        for sp in data.get("snippets") or data.get("clips") or []:
            try:
                a, b = int(sp["start"]), int(sp["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if a < idxs[0] or a > idxs[-1]:
                continue
            b = max(a, min(b, idxs[-1]))
            # batasi tiap potongan maksimal ~15 detik supaya kompilasi terasa padat
            while b > a and units[b]["e"] - units[a]["s"] > 15.0:
                b -= 1
            s = max(0.0, units[a]["s"] - 0.20)
            e = min(total_dur, units[b]["e"] + 0.35)
            if e - s < 1.2:
                e = min(total_dur, s + 2.0)
            if e - s < 0.8:
                continue
            raw_score = sp.get("score")
            if raw_score is None:
                raw_score = round((int(sp.get("hook", 70) or 70) + int(sp.get("value", 70) or 70)) / 2)
            score = max(0, min(100, int(raw_score or 70)))
            heat = _heat_max(heatmap, s, e) if heatmap else 0.0
            blended = min(100, round(score * 0.85 + heat * 100 * 0.15)) if heat >= 0.60 else score
            note = (sp.get("note") or sp.get("reason") or units[a]["text"])[:70].strip()
            cands.append({"s": round(s, 3), "e": round(e, 3), "score": blended, "heat": heat, "note": note})

    emit("progress", stage="detect", pct=100)
    if not cands:
        if failed and len(failed) == len(wins):
            raise RuntimeError(f"Semua bagian transkrip gagal dibaca AI. Error terakhir: {failed[-1]} "
                               "Coba lagi, atau ganti model/provider di Pengaturan.")
        raise RuntimeError("AI tidak menemukan potongan yang cocok dengan permintaanmu. Coba ubah deskripsi momennya.")

    # Pilih potongan terkuat tanpa tumpang tindih hingga mencapai target durasi (maks 14 potongan)
    cands.sort(key=lambda x: x["score"], reverse=True)
    picked = []
    acc_dur = 0.0
    max_dur = target_dur * 1.15
    for c in cands:
        if any(not (c["e"] <= o["s"] or c["s"] >= o["e"]) for o in picked):
            continue
        d = c["e"] - c["s"]
        if picked and acc_dur >= target_dur and acc_dur + d > max_dur:
            continue
        picked.append(c)
        acc_dur += d
        if len(picked) >= 14 or acc_dur >= target_dur:
            break

    # Urutkan kronologis dan gabungkan potongan yang jaraknya < 1.2 detik agar transisi halus
    picked.sort(key=lambda x: x["s"])
    merged = []
    for p in picked:
        if merged and p["s"] - merged[-1]["e"] < 1.2 and (p["e"] - merged[-1]["s"]) <= 18.0:
            merged[-1]["e"] = p["e"]
            merged[-1]["score"] = max(merged[-1]["score"], p["score"])
            merged[-1]["heat"] = max(merged[-1]["heat"], p["heat"])
        else:
            merged.append(dict(p))

    best = max(merged, key=lambda x: (x["score"], x["heat"]))
    avg_sc = max(50, min(100, round(sum(x["score"] for x in merged) / len(merged))))
    max_heat = max(x["heat"] for x in merged)
    sum_dur = sum(x["e"] - x["s"] for x in merged)
    segs = [{"s": x["s"], "e": x["e"], "note": x["note"]} for x in merged]
    title = titles[0] if titles else (f"Kompilasi: {prompt[:48]}" if prompt else "Kompilasi momen terbaik")
    reason = f"Kompilasi {len(segs)} potongan ({round(sum_dur)} dtk)" + (f" · {prompt}" if prompt else "")
    if failed:
        emit("log", text=f"{len(failed)} dari {len(wins)} bagian transkrip dilewati karena error AI.")
    return [{
        "start_s": segs[0]["s"],
        "end_s": max(segs[-1]["e"], segs[0]["s"] + 1.0),
        "peak_s": best["s"],
        "heat_score": round(max_heat * 100),
        "score_hook": avg_sc,
        "score_flow": avg_sc,
        "score_value": avg_sc,
        "score_total": avg_sc,
        "hook_title": title[:80],
        "reason": reason[:300],
        "segments": segs,
    }]


def detect(transcript, total_dur, clip_count, clip_duration, provider, model, key, job, emit,
           heatmap=None, prompt=None, compile_mode=False):
    if not key:
        raise RuntimeError(f"API key {PROVIDERS[provider]['label']} belum diisi. Buka Pengaturan.")
    units = build_units(transcript["words"])
    wins = windows(units)
    prompt = (prompt or "").strip()[:300]
    if compile_mode:
        return _detect_compile(units, wins, total_dur, clip_duration, provider, model, key, job, emit, heatmap, prompt)

    lo, hi = DURATIONS.get(clip_duration, DURATIONS["30-60"])
    per_win = max(2, math.ceil(clip_count * 1.5 / len(wins)) + 1)
    sys_prompt = SYSTEM
    if prompt:
        sys_prompt += (
            f"\n\nFOKUS UTAMA DARI PENGGUNA: {prompt}\n"
            "Prioritaskan dan HANYA pilih momen yang relevan dengan fokus pengguna di atas. "
            "Jika pengguna meminta bahasa tertentu di video campur bahasa, tetap kenali kata yang dieja fonetik."
        )
    cands = []
    failed = []
    for wi, idxs in enumerate(wins):
        def fmt_line(i):
            u = units[i]
            hv = _heat_max(heatmap, u["s"], u["e"]) if heatmap else 0.0
            tag = f" [MOST REPLAYED {round(hv * 100)}%]" if hv >= 0.65 else ""
            return f"[{i}] ({fmt_time(u['s'])}){tag} {u['text']}"

        lines = "\n".join(fmt_line(i) for i in idxs)
        focus_line = f"FOKUS MOMEN YANG DIMINTA: {prompt}\n" if prompt else ""
        user = (
            f"{focus_line}Durasi tiap klip harus {lo}-{hi} detik. Pilih maksimal {per_win} klip terbaik dari bagian ini.\n"
            f"Nomor baris valid: {idxs[0]} sampai {idxs[-1]}.\n\nTRANSKRIP:\n{lines}"
        )
        emit("progress", stage="detect", pct=round(wi * 100 / len(wins), 1),
             detail=f"AI membaca transkrip bagian {wi + 1} dari {len(wins)}" if len(wins) > 1 else None)
        try:
            raw = chat(provider, model, key, sys_prompt, user, job)
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
            base_total = sc["hook"] * 0.4 + sc["flow"] * 0.3 + sc["value"] * 0.3
            heat = _heat_max(heatmap, start, end) if heatmap else 0.0
            total = min(100, round(base_total * 0.80 + heat * 100 * 0.20)) if heat >= 0.60 else round(base_total)

            # Tentukan titik awal momen klimaks (5 dtk) untuk fitur Hook 5 detik di depan
            peak_s = _heatmap_peak_s(heatmap, start, end) if heatmap else None
            if peak_s is None:
                try:
                    pk_idx = int(c["peak"])
                    if a <= pk_idx <= b:
                        peak_s = round(max(start, min(max(start, end - 5.0), units[pk_idx]["s"])), 3)
                except (KeyError, TypeError, ValueError):
                    pass
            if peak_s is None:
                peak_s = round(max(start, min(max(start, end - 5.0), start + (end - start) * 0.62)), 3)

            cands.append({
                "start_s": round(start, 3), "end_s": round(end, 3),
                "peak_s": peak_s, "heat_score": round(heat * 100),
                "score_hook": sc["hook"], "score_flow": sc["flow"], "score_value": sc["value"],
                "score_total": total,
                "hook_title": (c.get("title") or units[a]["text"])[:80].strip(),
                "reason": (c.get("reason") or "")[:300],
                "segments": [],
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
        raise RuntimeError("AI tidak menemukan momen yang cocok. Coba durasi klip lain, deskripsi lain, atau model lain.")
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
