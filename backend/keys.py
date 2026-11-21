"""API key disimpan di Windows Credential Manager (keyring), tidak pernah di DB atau file."""
import keyring

SERVICE = "Klipku"


def get(provider: str):
    try:
        return keyring.get_password(SERVICE, provider)
    except Exception:  # noqa: BLE001
        return None


def set_(provider: str, key: str):
    key = (key or "").strip()
    if key:
        keyring.set_password(SERVICE, provider, key)
    else:
        try:
            keyring.delete_password(SERVICE, provider)
        except Exception:  # noqa: BLE001
            pass


def status(providers) -> dict:
    return {p: bool(get(p)) for p in providers}
