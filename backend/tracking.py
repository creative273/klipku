"""Analisis wajah per klip (format-independen), hasilnya di-cache di crop_tracks.

Cara kerja:
1. ffmpeg decode rentang klip ke frame kecil (lebar 640) pada 8 fps, dibaca lewat pipe.
2. YuNet mendeteksi wajah tiap frame. Histogram warna mendeteksi pergantian shot.
3. Per shot: wajah dikelompokkan berdasarkan posisi -> "track" (orang).
   Track yang muncul di cukup banyak frame dianggap orang di shot itu.
4. Facecam (untuk format Gaming): wajah yang posisinya hampir diam sepanjang klip.

Semua koordinat dinormalisasi 0..1 terhadap frame sumber.
"""
import math
import subprocess

import numpy as np

from config import FFMPEG, NO_WINDOW, YUNET

FPS = 8
AW = 640                  # lebar frame analisis
MIN_SCORE = 0.72
SHOT_CORREL = 0.55        # korelasi histogram di bawah ini = shot baru
SHOT_DIFF = 0.14          # atau rata-rata beda piksel (0..1) di atas ini
MIN_SHOT = 0.5            # detik; shot lebih pendek digabung ke tetangga
PRESENCE = 0.35           # track harus muncul di >= 35% frame shot
EDGE_T = 4.0              # gradien stabil minimum untuk dianggap garis tepi
BORDER_COV = 0.5          # skor sisi yang menempel ke tepi frame
MIN_COV = 0.45            # semua sisi kotak webcam harus tertutup garis tepi >= 45%


def _frames(src, start, dur, src_w, src_h, job, fps=FPS):
    ah = int(round(AW * src_h / src_w / 2)) * 2
    args = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
            "-i", str(src), "-an", "-vf", (f"fps={fps}," if fps else "") + f"scale={AW}:{ah}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    size = AW * ah * 3
    i = 0
    try:
        while True:
            if job.cancelled.is_set():
                break
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield i / (fps or 1), np.frombuffer(buf, np.uint8).reshape(ah, AW, 3)
            i += 1
    finally:
        p.kill()
        p.wait()
    job.check()


def _sig(frame):
    """Sidik frame untuk deteksi shot: histogram HSV + thumbnail abu-abu 64x36."""
    import cv2
    small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    gray = cv2.cvtColor(cv2.resize(small, (64, 36), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return cv2.normalize(h, h).flatten(), gray.astype(np.int16)


def _is_cut(a, b):
    import cv2
    corr = cv2.compareHist(a[0], b[0], cv2.HISTCMP_CORREL)
    diff = float(np.mean(np.abs(a[1] - b[1]))) / 255
    return corr < SHOT_CORREL or diff > SHOT_DIFF


def _refine_cut(src, start, t0, t1, src_w, src_h, job, src_fps):
    """Cari frame persis tempat shot berganti di antara dua sampel (decode semua frame)."""
    frames = list(_frames(src, start + t0, (t1 - t0) + 1.0 / FPS, src_w, src_h, job, fps=0))
    if len(frames) < 2:
        return t1 - 0.5 / FPS
    sigs = [_sig(f) for _, f in frames]
    diffs = [float(np.mean(np.abs(sigs[k - 1][1] - sigs[k][1]))) for k in range(1, len(sigs))]
    k = int(np.argmax(diffs)) + 1
    # frame pertama hasil decode = frame sumber pertama di/ setelah (start + t0)
    first = math.ceil((start + t0) * src_fps - 1e-6) / src_fps - start
    # batas potong di tengah antara frame k-1 dan k
    return max(0.0, first + (k - 0.5) / src_fps)


def analyze(src, start, dur, src_w, src_h, job, src_fps=30.0) -> dict:
    import cv2

    det = None
    samples = []  # (t, faces[], hist)
    keep = []     # frame kecil (setiap 0.5 dtk, maks 60) untuk deteksi kotak facecam
    for t, fr in _frames(src, start, dur, src_w, src_h, job):
        if len(samples) % (FPS // 2) == 0 and len(keep) < 60:
            keep.append((len(samples), fr))
        if det is None:
            det = cv2.FaceDetectorYN.create(str(YUNET), "", (fr.shape[1], fr.shape[0]), MIN_SCORE, 0.3, 50)
        _, res = det.detect(fr)
        faces = []
        if res is not None:
            H, W = fr.shape[:2]
            for r in res:
                x, y, w, h, score = float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[14])
                if w < W * 0.025 or score < MIN_SCORE:
                    continue
                faces.append({"cx": (x + w / 2) / W, "cy": (y + h / 2) / H, "w": w / W, "h": h / H, "sc": score})
        samples.append((t, faces, _sig(fr)))
    if not samples:
        return {"shots": [{"s": 0.0, "e": dur, "tracks": []}], "facecam": None, "samples": 0}

    # pergantian shot
    cuts = [0.0]
    for i in range(1, len(samples)):
        if _is_cut(samples[i - 1][2], samples[i][2]):
            cuts.append(_refine_cut(src, start, samples[i - 1][0], samples[i][0], src_w, src_h, job, src_fps or 30.0))
    cuts.append(dur)
    shots = []
    for a, b in zip(cuts, cuts[1:]):
        if shots and b - a < MIN_SHOT:
            shots[-1]["e"] = b
            continue
        shots.append({"s": max(0.0, a), "e": b})
    if len(shots) > 1 and shots[0]["e"] - shots[0]["s"] < MIN_SHOT:
        shots[1]["s"] = 0.0
        shots.pop(0)

    for sh in shots:
        ss = [s for s in samples if sh["s"] <= s[0] < sh["e"]]
        sh["tracks"] = _tracks(ss)
    fc = _facecam(samples)
    if fc:
        fc["rect"] = _facecam_rect(fc, samples, keep)
    return {"shots": shots, "facecam": fc, "samples": len(samples)}


def _tracks(ss):
    """Kelompokkan wajah dalam satu shot berdasarkan posisi horizontal."""
    clusters = []
    for _, faces, _ in ss:
        for f in faces:
            best = None
            for c in clusters:
                if abs(c["cx"] - f["cx"]) < max(0.08, c["w"] * 1.2) and abs(c["cy"] - f["cy"]) < 0.25:
                    best = c
                    break
            if best is None:
                clusters.append({"cx": f["cx"], "cy": f["cy"], "w": f["w"], "h": f["h"], "items": [f]})
            else:
                best["items"].append(f)
                n = len(best["items"])
                for k in ("cx", "cy", "w", "h"):
                    best[k] += (f[k] - best[k]) / n
    n = max(1, len(ss))
    out = []
    for c in clusters:
        pres = len(c["items"]) / n
        if pres < PRESENCE:
            continue
        it = c["items"]
        out.append({
            "cx": float(np.median([f["cx"] for f in it])), "cy": float(np.median([f["cy"] for f in it])),
            "w": float(np.median([f["w"] for f in it])), "h": float(np.median([f["h"] for f in it])),
            "presence": round(min(1.0, pres), 3),
        })
    out.sort(key=lambda t: t["presence"] * t["w"], reverse=True)
    return out[:4]


def _facecam(samples):
    """Wajah yang posisinya hampir tidak bergerak sepanjang klip (overlay webcam)."""
    pts = [f for _, faces, _ in samples for f in faces]
    if not pts:
        return None
    best, best_n = None, 0
    for f in pts:
        near = [g for g in pts if abs(g["cx"] - f["cx"]) < 0.04 and abs(g["cy"] - f["cy"]) < 0.06]
        if len(near) > best_n:
            best, best_n = near, len(near)
    if best_n < max(4, len(samples) * 0.2):
        return None
    cx = [g["cx"] for g in best]
    cy = [g["cy"] for g in best]
    if np.std(cx) > 0.02 or np.std(cy) > 0.03:
        return None  # bergerak: karakter game, bukan webcam
    return {
        "cx": float(np.median(cx)), "cy": float(np.median(cy)),
        "w": float(np.median([g["w"] for g in best])), "h": float(np.median([g["h"] for g in best])),
        "presence": round(best_n / len(samples), 3),
    }


def _facecam_rect(fc, samples, keep):
    """Cari kotak webcam di sekitar wajah facecam.

    Bingkai webcam diam, gameplay di sekitarnya berubah. Ambil gradien yang stabil
    (persentil 20 dari semua frame), lalu cari persegi panjang yang keempat sisinya
    paling konsisten berada di atas garis tepi stabil. Kembalikan [x0,y0,x1,y1] 0..1.
    """
    import cv2

    frames = [fr for _, fr in keep]
    if len(frames) < 4:
        return None
    gxs, gys = [], []
    for fr in frames:
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gxs.append(np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)))
        gys.append(np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)))
    gx = np.percentile(np.stack(gxs), 20, axis=0) > EDGE_T
    gy = np.percentile(np.stack(gys), 20, axis=0) > EDGE_T
    # toleransi 1 piksel: tepi hasil scaling bisa jatuh di dua kolom/baris
    gx = gx | np.roll(gx, 1, axis=1)
    gy = gy | np.roll(gy, 1, axis=0)
    H, W = gx.shape
    fx0, fx1 = int((fc["cx"] - fc["w"] / 2) * W), int((fc["cx"] + fc["w"] / 2) * W)
    fy0, fy1 = int((fc["cy"] - fc["h"] / 2) * H), int((fc["cy"] + fc["h"] / 2) * H)
    fw, fh = max(4, fx1 - fx0), max(4, fy1 - fy0)

    def cands(lo, hi, edge_val):
        out = list(range(max(0, lo), min(edge_val, hi) + 1, 1))
        return out

    L = [x for x in cands(fx0 - fw * 4, fx0 - fw // 8, W)] + ([0] if fx0 - fw * 4 <= 0 else [])
    R = [x for x in cands(fx1 + fw // 8, fx1 + fw * 4, W - 1)] + ([W - 1] if fx1 + fw * 4 >= W - 1 else [])
    T = [y for y in cands(fy0 - fh * 4, fy0 - fh // 8, H)] + ([0] if fy0 - fh * 4 <= 0 else [])
    B = [y for y in cands(fy1 + fh // 8, fy1 + fh * 4, H - 1)] + ([H - 1] if fy1 + fh * 4 >= H - 1 else [])
    if not (L and R and T and B):
        return None

    def vcov(x, y0, y1):  # sisi vertikal
        if x in (0, W - 1):
            return BORDER_COV
        return float(gx[y0:y1, x].mean()) if y1 > y0 else 0.0

    def hcov(y, x0, x1):  # sisi horizontal
        if y in (0, H - 1):
            return BORDER_COV
        return float(gy[y, x0:x1].mean()) if x1 > x0 else 0.0

    # kandidat per sisi: puncak lokal cakupan garis (jarak >= 3 px), diukur di rentang
    # sedikit lebih lebar dari wajah. Lalu coba semua kombinasi 8 teratas tiap sisi.
    def top(cs, f, n=8):
        scored = sorted(((f(c), c) for c in cs), reverse=True)
        out = []
        for sc, c in scored:
            if sc <= 0.05:
                break
            if all(abs(c - o) >= 3 for o in out):
                out.append(c)
            if len(out) >= n:
                break
        return out
    ry0, ry1 = max(0, fy0 - fh // 2), min(H, fy1 + fh // 2)
    rx0, rx1 = max(0, fx0 - fw // 2), min(W, fx1 + fw // 2)
    Lc = top(L, lambda x: vcov(x, ry0, ry1))
    Rc = top(R, lambda x: vcov(x, ry0, ry1))
    Tc = top(T, lambda y: hcov(y, rx0, rx1))
    Bc = top(B, lambda y: hcov(y, rx0, rx1))
    found = []
    for x0 in Lc:
        for x1 in Rc:
            for y0 in Tc:
                for y1 in Bc:
                    w, h = x1 - x0, y1 - y0
                    if w <= 0 or h <= 0 or not (0.9 <= (w * W / W) / h * 1 <= 2.4):
                        continue
                    if not (1.4 <= h / fh <= 6):
                        continue
                    sc = min(vcov(x0, y0, y1), vcov(x1, y0, y1), hcov(y0, x0, x1), hcov(y1, x0, x1))
                    if sc >= MIN_COV:
                        found.append((sc, w * h, (x0, y0, x1 + 1, y1 + 1)))
    if not found:
        return None
    # di antara kotak yang hampir sama kuat, pilih yang paling rapat ke wajah
    top_sc = max(f[0] for f in found)
    x0, y0, x1, y1 = min((f for f in found if f[0] >= top_sc * 0.85), key=lambda f: f[1])[2]
    return [x0 / W, y0 / H, min(1.0, x1 / W), min(1.0, y1 / H)]


# ---------- dari analisis ke layout render ----------

def _even(x):
    return max(2, int(round(x / 2)) * 2)


def _fit(w, h, x, y, src_w, src_h):
    w, h = min(_even(w), src_w - src_w % 2), min(_even(h), src_h - src_h % 2)
    x = min(max(0, _even(x)), src_w - w)
    y = min(max(0, _even(y)), src_h - h)
    return (w, h, x - x % 2, y - y % 2)


def _box(cx, cy, face_h, ratio, src_w, src_h, scale_face=2.8, full_height=False):
    """Kotak crop rasio w/h di sekitar wajah, di dalam frame. Koordinat piksel sumber."""
    h = src_h if full_height else min(src_h, max(src_h * 0.32, face_h * src_h * scale_face))
    w = h * ratio
    if w > src_w:
        w, h = src_w, src_w / ratio
    # wajah sekitar sepertiga atas kotak
    return _fit(w, h, cx * src_w - w / 2, cy * src_h - h * 0.38, src_w, src_h)


def layout_segments(analysis, ratio, src_w, src_h, allow_dual=True):
    """Ubah analisis jadi segmen render: [{s,e,kind,boxes:[(w,h,x,y),...]}].

    ratio = W/H output. kind: single (1 kotak) | dual (2 kotak, atas-bawah).
    """
    segs = []
    single_w_frac = min(1.0, src_h * ratio / src_w)  # lebar kotak tinggi-penuh relatif frame
    prev = None
    for sh in analysis["shots"]:
        tr = sh["tracks"]
        if len(tr) >= 2 and allow_dual:
            a, b = sorted(tr[:2], key=lambda t: t["cx"])
            if b["cx"] - a["cx"] + max(a["w"], b["w"]) <= single_w_frac * 0.92:
                # dua wajah muat dalam satu crop tinggi-penuh: crop di tengah keduanya
                cx = (a["cx"] + b["cx"]) / 2
                segs.append({"s": sh["s"], "e": sh["e"], "kind": "single",
                             "boxes": [_box(cx, (a["cy"] + b["cy"]) / 2, 0, ratio, src_w, src_h, full_height=True)]})
            else:
                half = ratio * 2  # tiap panel = W x H/2
                fh = max(a["h"], b["h"])
                segs.append({"s": sh["s"], "e": sh["e"], "kind": "dual",
                             "boxes": [_box(a["cx"], a["cy"], fh, half, src_w, src_h),
                                       _box(b["cx"], b["cy"], fh, half, src_w, src_h)]})
        elif tr:
            t = tr[0]
            segs.append({"s": sh["s"], "e": sh["e"], "kind": "single",
                         "boxes": [_box(t["cx"], t["cy"], t["h"], ratio, src_w, src_h, full_height=True)]})
        else:
            # tanpa wajah: pakai posisi shot sebelumnya kalau ada, kalau tidak di tengah
            box = prev["boxes"][0] if prev and prev["kind"] == "single" else \
                _box(0.5, 0.5, 0, ratio, src_w, src_h, full_height=True)
            segs.append({"s": sh["s"], "e": sh["e"], "kind": "single", "boxes": [box]})
        prev = segs[-1]
    # gabungkan segmen berurutan yang layoutnya sama persis
    merged = []
    for sg in segs:
        if merged and merged[-1]["kind"] == sg["kind"] and merged[-1]["boxes"] == sg["boxes"]:
            merged[-1]["e"] = sg["e"]
        else:
            merged.append(dict(sg))
    return merged


def facecam_box(fc, ratio, src_w, src_h):
    """Kotak bagian atas format Gaming.

    Kalau tepi kotak webcam ketemu: crop di dalam kotak itu (isi penuh panel, tanpa gameplay).
    Kalau tidak: kotak di sekitar wajah, sekitar 2.4x tinggi wajah.
    """
    r = fc.get("rect")
    if r:
        x0, y0, x1, y1 = r[0] * src_w, r[1] * src_h, r[2] * src_w, r[3] * src_h
        # buang 1% di tiap sisi supaya garis bingkai tidak ikut
        px, py = (x1 - x0) * 0.01, (y1 - y0) * 0.01
        x0, x1, y0, y1 = x0 + px, x1 - px, y0 + py, y1 - py
        rw, rh = x1 - x0, y1 - y0
        if rw / rh > ratio:
            w, h = rh * ratio, rh
            x = min(max(x0, fc["cx"] * src_w - w / 2), x1 - w)
            y = y0
        else:
            w, h = rw, rw / ratio
            x = x0
            y = min(max(y0, fc["cy"] * src_h - h * 0.45), y1 - h)
        return _fit(w, h, x, y, src_w, src_h)
    h = min(src_h, max(fc["h"] * src_h * 2.4, src_h * 0.15))
    w = h * ratio
    if w > src_w:
        w, h = src_w, src_w / ratio
    return _fit(w, h, fc["cx"] * src_w - w / 2, fc["cy"] * src_h - h * 0.45, src_w, src_h)
