"""CI: uji transkrip offline end-to-end dengan model tiny + suara SAPI Windows.

Jalankan: engine\\python\\python.exe scripts\\test_offline.py <wav>
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KLIPKU_DATA", str(Path(os.environ.get("RUNNER_TEMP", ".")) / "klipku-offline"))
import whisper_local as W  # noqa: E402
from proc import Job  # noqa: E402

W.MODELS["tiny"] = {"repo": "Systran/faster-whisper-tiny", "label": "tiny (tes CI)"}
W.start_download("tiny")
for _ in range(600):
    st = W.status()["tiny"]
    if st["installed"] or st["error"]:
        break
    time.sleep(1)
print("download:", st)
if not st["installed"]:
    sys.exit(1)
logs = []
res = W.transcribe_offline(sys.argv[1], 6.0, "tiny", "en", Job(), lambda k, **d: logs.append((k, d)))
text = " ".join(w["t"] for w in res["words"]).lower()
print("words:", len(res["words"]), "|", text)
ok = "test" in text and all(w["e"] >= w["s"] for w in res["words"])
print("OK  offline transcribe" if ok else "FAIL offline transcribe")
sys.exit(0 if ok else 1)
