import hashlib
import hmac
import time
from collections import defaultdict, deque
from urllib.parse import urlencode

from fastapi import HTTPException, Request

from .config import settings

_requests: dict[str, deque[float]] = defaultdict(deque)


def preview_signature(artifact_id: str, expires: int) -> str:
    payload = f"{artifact_id}:{expires}".encode()
    return hmac.new(settings.APP_TOKEN.encode(), payload, hashlib.sha256).hexdigest()


def signed_preview_path(artifact_id: str, now: int | None = None) -> str:
    expires = (now if now is not None else int(time.time())) + settings.PREVIEW_URL_TTL_S
    query = urlencode({"expires": expires, "signature": preview_signature(artifact_id, expires)})
    return f"/s/{artifact_id}?{query}"


def verify_preview_signature(artifact_id: str, expires: int, signature: str) -> None:
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="preview link expired")
    expected = preview_signature(artifact_id, expires)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="invalid preview signature")


def enforce_preview_rate_limit(request: Request) -> None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    client = forwarded or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    cutoff = now - settings.PREVIEW_RATE_WINDOW_S
    requests = _requests[client]
    while requests and requests[0] <= cutoff:
        requests.popleft()
    if len(requests) >= settings.PREVIEW_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="preview rate limit exceeded")
    requests.append(now)
