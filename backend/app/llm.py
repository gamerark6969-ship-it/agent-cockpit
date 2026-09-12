import asyncio
from typing import Any, Dict, List, Optional

import httpx

from .config import settings


class LLMError(Exception):
    pass


class LLMNotConfigured(LLMError):
    pass


class LLMClient:
    def __init__(self, base_url: Optional[str], api_key: Optional[str], model: str, timeout: float = 300.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url) and bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _acquire(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _request_with_retries(self, method: str, url: str, json_body: dict) -> httpx.Response:
        if not self.configured:
            raise LLMNotConfigured("AGENTROUTER_BASE_URL/AGENTROUTER_API_KEY not configured")
        client = await self._acquire()
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                resp = await client.request(
                    method, url, headers=self._headers(), json=json_body
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
                    await asyncio.sleep(min(2**attempt, 30))
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
                return resp
            except httpx.TimeoutException as exc:
                last_exc = LLMError(f"LLM timeout: {exc}")
                break
            except httpx.HTTPError as exc:
                last_exc = LLMError(f"LLM transport error: {exc}")
                await asyncio.sleep(min(2**attempt, 30))
        raise last_exc or LLMError("LLM request failed")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Returns an assistant message dict (content / tool_calls) and accumulates usage."""
        body: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_tokens"] = max_tokens
        url = f"{self.base_url}/chat/completions"
        resp = await self._request_with_retries("POST", url, body)
        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice.get("message", {})
            usage = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed LLM response: {exc}") from exc
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
        message.setdefault("content", None)
        message.setdefault("tool_calls", [])
        message["_usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens or (prompt_tokens + completion_tokens),
        }
        return message

    async def list_models(self) -> List[str]:
        if not self.configured:
            raise LLMNotConfigured("AGENTROUTER_BASE_URL/AGENTROUTER_API_KEY not configured")
        client = await self._acquire()
        url = f"{self.base_url}/models"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code in (429,) or resp.status_code >= 500:
                    last_exc = LLMError(f"models HTTP {resp.status_code}")
                    await asyncio.sleep(2**attempt)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"models HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                return [str(m.get("id")) for m in data.get("data", []) if m.get("id")]
            except httpx.HTTPError as exc:
                last_exc = LLMError(f"models transport error: {exc}")
                await asyncio.sleep(2**attempt)
        raise last_exc or LLMError("models request failed")


llm = LLMClient(
    base_url=settings.AGENTROUTER_BASE_URL,
    api_key=settings.AGENTROUTER_API_KEY,
    model=settings.AGENTROUTER_DEFAULT_MODEL,
)
