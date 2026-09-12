from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)


async def require_token(request: Request, creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    if request.url.path == "/api/health":
        return
    if creds is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if creds.credentials != settings.APP_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
