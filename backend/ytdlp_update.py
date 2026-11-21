"""Update yt-dlp dari dalam app tanpa pip dan tanpa menulis .exe.

Wheel resmi (py3-none-any) diunduh dari PyPI, dicek SHA256-nya terhadap metadata PyPI,
lalu diekstrak ke %APPDATA%\\Klipku\\pylib. Saat start, folder itu ditaruh paling depan
di sys.path hanya kalau versinya lebih baru dari yang ikut di installer.
Perubahan berlaku setelah app dibuka ulang.
"""
import hashlib
import io
import re
import shutil
import sys
import zipfile
from pathlib import Path

from config import DATA

PYLIB = DATA / "pylib"


def _ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:4])


def _read_version(pkg_dir: Path):
    f = pkg_dir / "yt_dlp" / "version.py"
    if not f.exists():
        return None
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", f.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else None


def bundled_version():
    for p in sys.path:
        if p and Path(p).resolve() != PYLIB.resolve():
            v = _read_version(Path(p))
            if v:
                return v
    return None


def activate():
    """Dipanggil sekali saat start, sebelum yt_dlp di-import."""
    v_local = _read_version(PYLIB)
    if v_local and _ver_tuple(v_local) > _ver_tuple(bundled_version()):
        sys.path.insert(0, str(PYLIB))


def active_version():
    try:
        import yt_dlp.version as v
        return v.__version__
    except Exception:  # noqa: BLE001
        return None


def check_latest():
    import httpx
    r = httpx.get("https://pypi.org/pypi/yt-dlp/json", timeout=20)
    r.raise_for_status()
    info = r.json()
    ver = info["info"]["version"]
    wheel = next((u for u in info["urls"] if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("py3-none-any.whl")), None)
    if not wheel:
        raise RuntimeError("Wheel yt-dlp tidak ditemukan di PyPI")
    return ver, wheel


def update():
    import httpx
    ver, wheel = check_latest()
    current = active_version()
    if current and _ver_tuple(current) >= _ver_tuple(ver):
        return {"updated": False, "version": current, "latest": ver}
    r = httpx.get(wheel["url"], timeout=120, follow_redirects=True)
    r.raise_for_status()
    data = r.content
    if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
        raise RuntimeError("Checksum wheel yt-dlp tidak cocok, update dibatalkan")
    tmp = PYLIB.with_name("pylib.part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            # hanya paket yt_dlp + metadata; tolak path aneh
            if (n.startswith("yt_dlp/") or ".dist-info/" in n) and ".." not in n and not n.startswith("/"):
                if n.lower().endswith((".exe", ".dll", ".pyd")):
                    continue
                z.extract(n, tmp)
    if _read_version(tmp) != ver:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("Isi wheel tidak sesuai versi")
    shutil.rmtree(PYLIB, ignore_errors=True)
    tmp.rename(PYLIB)
    return {"updated": True, "version": current, "latest": ver, "restart": True}
