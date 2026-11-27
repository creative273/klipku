"""Path dan konstanta bersama. Semua nilai datang dari env yang di-set Electron."""
import os
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""
ENGINE = Path(os.environ.get("KLIPKU_ENGINE", Path(__file__).resolve().parent.parent))
DATA = Path(os.environ.get("KLIPKU_DATA", Path.home() / ".klipku"))
DEFAULT_OUTPUT = Path(os.environ.get("KLIPKU_OUTPUT", Path.home() / "Videos" / "Klipku"))
TOKEN = os.environ.get("KLIPKU_TOKEN", "")

BIN = ENGINE / "bin"
FFMPEG = BIN / f"ffmpeg{EXE}"
FFPROBE = BIN / f"ffprobe{EXE}"
DENO = BIN / f"deno{EXE}"
YUNET = ENGINE / "models" / "face_detection_yunet_2023mar.onnx"
FONTS = ENGINE / "fonts"
DB_PATH = DATA / "klipku.db"
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

# yt-dlp mencari deno & ffmpeg di PATH: taruh folder bin app paling depan.
os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")

# Provider AI: semuanya punya endpoint kompatibel OpenAI (chat completions).
PROVIDERS = {
    "groq": {
        "label": "Groq",
        "base": "https://api.groq.com/openai/v1",
        "models": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"],
        "key_url": "https://console.groq.com/keys",
    },
    "gemini": {
        "label": "Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "key_url": "https://aistudio.google.com/apikey",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "models": ["openrouter/free"],
        "key_url": "https://openrouter.ai/keys",
    },
}
GROQ_STT_MODEL = "whisper-large-v3-turbo"
GITHUB_REPO = "creative273/klipku"
