import subprocess

from config import NO_WINDOW


def run(args, timeout=60):
    """Jalankan executable langsung (tanpa shell). Kembalikan (ok, output)."""
    try:
        p = subprocess.run(
            [str(a) for a in args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=NO_WINDOW,
        )
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return False, f"tidak ditemukan: {args[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout}s: {args[0]}"


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else ""
