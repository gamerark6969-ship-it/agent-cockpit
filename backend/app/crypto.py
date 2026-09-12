import base64
import hashlib
import json
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_SECRET_KEY_HINTS = ("password", "token", "secret", "key", "pat", "api_key", "access", "auth")


def _fernet() -> Fernet:
    seed = (
        settings.CONNECTOR_SECRET
        or settings.APP_TOKEN
        or "agent-cockpit-insecure-default"
    ).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


def encrypt_json(data: Dict[str, Any]) -> str:
    raw = json.dumps(data or {}, separators=(",", ":"), default=str).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(token: str | None) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError):
        return {}
    try:
        out = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def mask_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Display-safe copy of a connector config: secret-looking values masked."""
    out: Dict[str, Any] = {}
    for k, v in (config or {}).items():
        if v and any(h in k.lower() for h in _SECRET_KEY_HINTS):
            text = str(v)
            out[k] = "••••••" + text[-4:] if len(text) > 4 else "••••••"
        else:
            out[k] = v
    return out
