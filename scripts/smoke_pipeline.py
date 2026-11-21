"""Smoke test pipeline penuh di Windows (CI) dengan AI palsu.

Menguji: file lokal dengan spasi/apostrof/huruf non-ASCII di path, semua format,
caption ASS, folder output custom, cancel, endpoint video ber-token.
Jalankan: engine\\python\\python.exe scripts\\smoke_pipeline.py <folder kerja>
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

base = Path(sys.argv[1]).resolve() / "Tés O'Neil folder"
base.mkdir(parents=True, exist_ok=True)
os.environ["KLIPKU_DATA"] = str(base / "data")
os.environ["KLIPKU_OUTPUT"] = str(base / "out")
os.environ["KLIPKU_TOKEN"] = "smoke"

import config  # noqa: E402
import detect  # noqa: E402
import pipeline  # noqa: E402

src = base / "video ku's tés.mp4"
subprocess.run([str(config.FFMPEG), "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=80",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=80",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)

words, t = [], 0.0
for w in ("Ini rahasia yang jarang orang tahu. Kenapa konten kamu sepi? Karena hook kamu lemah! " * 12).split():
    words.append({"t": w, "s": round(t, 2), "e": round(t + 0.35, 2)})
    t += 0.42
pipeline.transcribe_online = lambda *a, **k: {"engine": "fake", "words": words, "segments": [], "language": "id"}
detect.chat = lambda *a, **k: json.dumps({"clips": [
    {"start": 0, "end": 12, "hook": 90, "flow": 80, "value": 70, "title": "Rahasia hook: kuat 100%", "reason": "x"},
    {"start": 20, "end": 40, "hook": 60, "flow": 60, "value": 60, "title": "Kenapa sepi?", "reason": "y"}]})
pipeline.keys.get = lambda p: "fake"

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

H = {"X-Klipku-Token": "smoke"}
fail = 0


def wait(c, sid, limit=240):
    for _ in range(limit * 2):
        s = c.get(f"/sessions/{sid}", headers=H).json()
        if s["stage"] in ("done", "error", "cancelled"):
            return s
        time.sleep(0.5)
    return s


with TestClient(server.app) as c:
    out_custom = base / "hasil d'sini"
    r = c.put("/settings", json={"output_dir": str(out_custom)}, headers=H)
    assert r.status_code == 200, r.text
    for fmt in ["face916", "gaming916", "blurbg916", "square", "landscape"]:
        sid = c.post("/sessions", headers=H, json={"file_path": str(src), "clip_count": 2, "clip_duration": "30",
                                                    "format": fmt, "resolution": "720p"}).json()["id"]
        s = wait(c, sid)
        ok = s["stage"] == "done" and len(s["clips"]) == 2 and all(x["status"] == "done" for x in s["clips"])
        files_ok = all(Path(x["output_path"]).exists() for x in s["clips"] if x["output_path"])
        print("OK  " if ok and files_ok else "FAIL", fmt, s["stage"], s["error"],
              [(x["status"], x["error"]) for x in s["clips"]])
        fail += not (ok and files_ok)
    cid = s["clips"][0]["id"]
    v1 = c.get(f"/clips/{cid}/video?t=smoke").status_code
    v2 = c.get(f"/clips/{cid}/video").status_code
    print("OK  " if (v1, v2) == (200, 401) else "FAIL", "video token", v1, v2)
    fail += (v1, v2) != (200, 401)

    sid = c.post("/sessions", headers=H, json={"file_path": str(src), "clip_count": 2, "clip_duration": "60",
                                                "format": "face916", "resolution": "4k"}).json()["id"]
    time.sleep(2)
    c.post(f"/sessions/{sid}/cancel", headers=H)
    s = wait(c, sid, 60)
    parts = list(out_custom.rglob("*.part.mp4"))
    print("OK  " if s["stage"] == "cancelled" and not parts else "FAIL", "cancel", s["stage"], parts)
    fail += not (s["stage"] == "cancelled" and not parts)

sys.exit(1 if fail else 0)
