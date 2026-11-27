"""Cek & unduh pembaruan aplikasi Klipku langsung dari GitHub Releases.

Installer diunduh ke %APPDATA%\\Klipku\\updates, diverifikasi terhadap SHA256SUMS.txt
dari rilis GitHub yang sama, lalu dijalankan oleh proses utama Electron.
"""
import hashlib
import os
import re
import threading
from pathlib import Path

import httpx

from config import DATA, GITHUB_REPO

UPDATES_DIR = DATA / "updates"
ALLOWED_HOSTS = (
    "https://github.com/",
    "https://objects.githubusercontent.com/",
    "https://release-assets.githubusercontent.com/",
)

_lock = threading.Lock()
_state = {
    "state": "idle",  # idle | downloading | verifying | ready | error
    "version": None,
    "pct": 0.0,
    "bytes": 0,
    "total": 0,
    "installer_path": None,
    "error": None,
}
_cancel = threading.Event()


def ver_tuple(v: str | None) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", (v or "").lstrip("vV"))]
    return tuple(nums[:4]) if nums else (0,)


def check(current_version: str) -> dict:
    """Cek versi terbaru & aset installer (.exe + SHA256SUMS.txt) dari GitHub Releases."""
    releases_url = f"https://github.com/{GITHUB_REPO}/releases"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Klipku/{current_version}"}
    r = httpx.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers=headers, timeout=15, follow_redirects=True,
    )
    if r.status_code == 404:
        rt = httpx.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=1",
            headers=headers, timeout=15, follow_redirects=True,
        )
        tags = rt.json() if rt.status_code == 200 else []
        if not tags:
            return {
                "current": current_version, "latest": None, "has_update": False,
                "url": releases_url, "can_download": False,
                "message": "Belum ada rilis publik di GitHub.",
            }
        tag = (tags[0].get("name") or "").lstrip("vV")
        has_update = ver_tuple(tag) > ver_tuple(current_version)
        return {
            "current": current_version, "latest": tag, "has_update": has_update,
            "url": f"https://github.com/{GITHUB_REPO}/releases/tag/{tags[0].get('name')}",
            "can_download": False,
            "message": "Rilis sedang dibangun di GitHub Actions." if has_update else None,
        }
    if r.status_code == 403:
        raise RuntimeError("Batas cek GitHub tercapai, coba lagi beberapa menit lagi.")
    r.raise_for_status()
    rel = r.json()
    latest = (rel.get("tag_name") or rel.get("name") or "").lstrip("vV")
    has_update = bool(latest) and ver_tuple(latest) > ver_tuple(current_version)

    assets = rel.get("assets") or []
    exe = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None)
    sums = next((a for a in assets if str(a.get("name", "")).upper() == "SHA256SUMS.TXT"), None)
    asset_url = exe.get("browser_download_url") if exe else None
    if asset_url and not asset_url.startswith(ALLOWED_HOSTS):
        asset_url = None
    checksum_url = sums.get("browser_download_url") if sums else None
    if checksum_url and not checksum_url.startswith(ALLOWED_HOSTS):
        checksum_url = None

    return {
        "current": current_version,
        "latest": latest or current_version,
        "has_update": has_update,
        "url": rel.get("html_url") or releases_url,
        "name": rel.get("name") or (f"v{latest}" if latest else ""),
        "published_at": rel.get("published_at"),
        "can_download": bool(has_update and asset_url),
        "asset_name": exe.get("name") if exe else None,
        "asset_url": asset_url,
        "asset_size": int(exe.get("size") or 0) if exe else 0,
        "checksum_url": checksum_url,
    }


def status() -> dict:
    with _lock:
        return dict(_state)


def cancel():
    _cancel.set()


def start_download(current_version: str) -> dict:
    info = check(current_version)
    if not info.get("has_update"):
        raise RuntimeError(f"Klipku sudah versi terbaru (v{current_version}).")
    if not info.get("asset_url") or not info.get("asset_name"):
        raise RuntimeError("Installer belum tersedia di rilis ini. Coba beberapa menit lagi.")

    with _lock:
        if _state["state"] in ("downloading", "verifying"):
            return dict(_state)
        _cancel.clear()
        _state.update({
            "state": "downloading",
            "version": info["latest"],
            "pct": 0.0,
            "bytes": 0,
            "total": info.get("asset_size") or 0,
            "installer_path": None,
            "error": None,
        })
    threading.Thread(target=_download_worker, args=(info, current_version), daemon=True).start()
    return status()


def _parse_checksum(text: str, filename: str) -> str | None:
    target = filename.lower()
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            name = parts[-1].lstrip("*").split("/")[-1].split("\\")[-1].lower()
            if name == target:
                return parts[0].lower()
    return None


def _download_worker(info: dict, current_version: str):
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    safe_file = re.sub(r"[^A-Za-z0-9._-]", "_", info["asset_name"])
    if not safe_file.lower().endswith(".exe"):
        safe_file += ".exe"
    dest = UPDATES_DIR / safe_file
    tmp = dest.with_name(dest.name + ".part")
    headers = {"User-Agent": f"Klipku/{current_version}"}
    try:
        # Bersihkan file installer lama supaya tidak menumpuk
        for old in UPDATES_DIR.glob("*.exe"):
            if old != dest:
                old.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)

        sha = hashlib.sha256()
        got = 0
        with httpx.stream("GET", info["asset_url"], headers=headers, follow_redirects=True,
                          timeout=httpx.Timeout(60, connect=20)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or info.get("asset_size") or 0)
            with _lock:
                _state["total"] = total
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(512 * 1024):
                    if _cancel.is_set():
                        raise InterruptedError("Unduhan dibatalkan.")
                    f.write(chunk)
                    sha.update(chunk)
                    got += len(chunk)
                    with _lock:
                        _state["bytes"] = got
                        if total:
                            _state["pct"] = round(min(99.5, got * 100 / total), 1)

        with _lock:
            _state["state"] = "verifying"
            _state["pct"] = 99.5

        if info.get("checksum_url"):
            cs = httpx.get(info["checksum_url"], headers=headers, follow_redirects=True, timeout=20)
            cs.raise_for_status()
            expected = _parse_checksum(cs.text, info["asset_name"])
            if expected and sha.hexdigest().lower() != expected:
                raise RuntimeError("Checksum SHA256 installer tidak cocok, unduhan dibatalkan.")

        os.replace(tmp, dest)
        with _lock:
            _state["state"] = "ready"
            _state["pct"] = 100.0
            _state["installer_path"] = str(dest)
    except InterruptedError:
        tmp.unlink(missing_ok=True)
        with _lock:
            _state.update({"state": "idle", "pct": 0.0, "bytes": 0, "installer_path": None, "error": None})
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        with _lock:
            _state["state"] = "error"
            _state["error"] = f"Unduh update gagal: {e}"[:300]
