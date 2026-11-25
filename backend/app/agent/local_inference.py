"""Free CPU inference on the scheduled worker; never route to Ollama Cloud."""

import httpx
from urllib.parse import urlparse

from ..config import settings


async def generate_local(prompt: str, system_prompt: str = "") -> str:
    if not settings.LOCAL_LLM_ENABLED or settings.LOCAL_LLM_MODEL.endswith(":cloud"):
        raise RuntimeError("Local CPU inference is not enabled")
    base = settings.LOCAL_LLM_BASE_URL.rstrip("/")
    if urlparse(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Local inference must use the worker's loopback address")
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(base + "/api/chat", json={
            "model": settings.LOCAL_LLM_MODEL,
            "messages": [{"role":"system", "content":system_prompt}, {"role":"user", "content":prompt}],
            "stream": False, "think": False,
            "options": {"num_ctx":8192, "num_predict":2500, "temperature":0.7},
        })
        if response.is_error:
            raise RuntimeError("Local model is unavailable; check the scheduled worker setup")
        result = response.json().get("message", {}).get("content", "").strip()
    if not result:
        raise RuntimeError("The local model returned no text")
    return result
