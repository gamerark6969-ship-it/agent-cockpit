import asyncio
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx

from .config import settings

# Gemini's OpenAI-compatible /models endpoint lists every model (embeddings,
# TTS, image, video, ...). The agent only does chat, so keep generative chat
# models and drop the rest.
_NON_CHAT_MARKERS = (
    "embedding",
    "tts",
    "image",
    "audio",
    "live",
    "veo",
    "lyria",
    "transcribe",
    "robotics",
    "computer-use",
    "nano-banana",
    "antigravity",
    "deep-research",
    "aqa",
)


def _normalize_model_id(raw: str) -> str:
    mid = (raw or "").strip()
    if mid.startswith("models/"):
        mid = mid[len("models/"):]
    return mid


def _is_chat_model(mid: str) -> bool:
    if not mid or "gemini" not in mid.lower():
        return False
    return not any(marker in mid.lower() for marker in _NON_CHAT_MARKERS)


class LLMError(Exception):
    pass


class LLMNotConfigured(LLMError):
    pass


# "Please retry in 50.690982986s." — respect the provider's own backoff hint.
_RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)\s*s", re.IGNORECASE)


def _retry_delay(text: str, attempt: int) -> float:
    m = _RETRY_DELAY_RE.search(text or "")
    if m:
        try:
            return min(float(m.group(1)) + 1.0, 90.0)
        except ValueError:
            pass
    return min(2.0**attempt, 30.0)


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
        for attempt in range(4):
            try:
                resp = await client.request(
                    method, url, headers=self._headers(), json=json_body
                )
                if resp.status_code == 429:
                    last_exc = LLMError(f"LLM HTTP 429: {resp.text[:500]}")
                    if attempt >= 1:
                        # Quota errors rarely clear on a quick retry — surface
                        # immediately so the caller can fail over to another model.
                        raise last_exc
                    await asyncio.sleep(_retry_delay(resp.text, attempt))
                    continue
                if resp.status_code >= 500:
                    last_exc = LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
                    await asyncio.sleep(_retry_delay(resp.text, attempt))
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
        return self._finalize_message(message, usage)

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        on_delta: Optional[Callable[[str], Any]] = None,
    ) -> Dict[str, Any]:
        """Stream a chat completion, invoking ``on_delta(text)`` per content chunk.

        Returns the same assistant message shape as :meth:`chat`. Tool call
        deltas are merged field-by-field so provider extras (e.g. Gemini's
        ``extra_content.google.thought_signature``) survive the round trip —
        dropping them makes Gemini 3.x reject the next request. Falls back to
        a non-streaming request if the provider ignores ``stream=True``.
        """
        if not self.configured:
            raise LLMNotConfigured("LLM provider not configured")
        body: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_tokens"] = max_tokens
        url = f"{self.base_url}/chat/completions"

        client = await self._acquire()
        last_exc: Exception | None = None
        for attempt in range(4):
            content_parts: List[str] = []
            tool_acc: Dict[int, Dict[str, Any]] = {}
            usage: Dict[str, Any] = {}
            got_any = False
            try:
                async with client.stream("POST", url, headers=self._headers(), json=body) as resp:
                    if resp.status_code == 429:
                        raw = await resp.aread()
                        text = raw[:500].decode("utf-8", "replace")
                        last_exc = LLMError(f"LLM HTTP 429: {text}")
                        if attempt >= 1:
                            raise last_exc
                        await asyncio.sleep(_retry_delay(text, attempt))
                        continue
                    if resp.status_code >= 500:
                        raw = await resp.aread()
                        text = raw[:500].decode("utf-8", "replace")
                        last_exc = LLMError(f"LLM HTTP {resp.status_code}: {text}")
                        await asyncio.sleep(_retry_delay(text, attempt))
                        continue
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        raise LLMError(
                            f"LLM HTTP {resp.status_code}: {raw[:500].decode('utf-8', 'replace')}"
                        )
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except (ValueError, TypeError):
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if piece:
                                got_any = True
                                content_parts.append(piece)
                                if on_delta is not None:
                                    await on_delta(piece)
                            for tcd in delta.get("tool_calls") or []:
                                got_any = True
                                self._merge_tool_delta(tool_acc, tcd)

                if not got_any:
                    # Provider ignored stream=True (or returned nothing streamable).
                    return await self.chat(
                        messages, tools=tools, model=model, max_tokens=max_tokens
                    )

                calls: List[Dict[str, Any]] = []
                for i in sorted(tool_acc):
                    call = tool_acc[i]
                    fn = call.get("function") or {}
                    if not fn.get("name"):
                        continue
                    if not call.get("id"):
                        call["id"] = f"call_{uuid.uuid4().hex}"
                    call["type"] = "function"
                    calls.append(call)

                message: Dict[str, Any] = {
                    "content": "".join(content_parts) or None,
                    "tool_calls": calls,
                }
                return self._finalize_message(message, usage)
            except httpx.TimeoutException as exc:
                last_exc = LLMError(f"LLM timeout: {exc}")
                if not got_any:
                    await asyncio.sleep(min(2**attempt, 30))
                    continue
                raise last_exc
            except httpx.HTTPError as exc:
                last_exc = LLMError(f"LLM transport error: {exc}")
                if not got_any:
                    await asyncio.sleep(min(2**attempt, 30))
                    continue
                raise last_exc
        raise last_exc or LLMError("LLM stream request failed")

    @staticmethod
    def _merge_tool_delta(tool_acc: Dict[int, Dict[str, Any]], tcd: Dict[str, Any]) -> None:
        """Merge a streamed tool-call delta, preserving unknown provider fields."""
        try:
            idx = int(tcd.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        slot = tool_acc.setdefault(idx, {})
        for k, v in tcd.items():
            if k == "index" or v is None or v == "":
                continue
            if k == "function":
                fn = slot.setdefault("function", {})
                if not isinstance(v, dict):
                    continue
                for fk, fv in v.items():
                    if fv is None or fv == "":
                        continue
                    if fk in ("name", "arguments") and isinstance(fv, str):
                        fn[fk] = (fn.get(fk) or "") + fv
                    else:
                        fn[fk] = fv
            elif k == "id":
                slot["id"] = v
            elif isinstance(v, str) and isinstance(slot.get(k), str):
                slot[k] = slot[k] + v
            else:
                slot[k] = v

    @staticmethod
    def _finalize_message(message: Dict[str, Any], usage: Dict[str, Any]) -> Dict[str, Any]:
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
            raise LLMNotConfigured("LLM provider not configured")
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
                models: List[str] = []
                for m in data.get("data", []):
                    mid = _normalize_model_id(str(m.get("id") or ""))
                    if _is_chat_model(mid) and mid not in models:
                        models.append(mid)
                return models
            except httpx.HTTPError as exc:
                last_exc = LLMError(f"models transport error: {exc}")
                await asyncio.sleep(2**attempt)
        raise last_exc or LLMError("models request failed")


llm = LLMClient(
    base_url=settings.AGENTROUTER_BASE_URL,
    api_key=settings.AGENTROUTER_API_KEY,
    model=settings.AGENTROUTER_DEFAULT_MODEL,
)

# Secondary provider (api.b.ai). Only the models listed in BAI_MODELS are
# exposed; everything else keeps routing to the primary client.
llm_bai = LLMClient(
    base_url=settings.BAI_BASE_URL,
    api_key=settings.BAI_API_KEY,
    model="deepseek-v4.1-flash",
)
BAI_MODELS = ("deepseek-v4.1-flash",)

# Known-good, currently available chat models surfaced in the app. Deprecated
# ids (e.g. gemini-2.5-pro) are deliberately excluded.
GEMINI_MODEL_CATALOG = [
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-pro-latest",
    "gemini-3.1-pro-preview",
]

# Order matters: first entry is the UI default, the rest are failover targets.
MODEL_CATALOG = ["gemini-3.8-flash", *BAI_MODELS, *GEMINI_MODEL_CATALOG[1:]]


def client_for(model: Optional[str]) -> LLMClient:
    if model and model.startswith("deepseek"):
        return llm_bai
    return llm


def any_configured() -> bool:
    return llm.configured or llm_bai.configured


async def chat(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    return await client_for(model).chat(messages, tools=tools, model=model, max_tokens=max_tokens)


async def chat_stream(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    on_delta: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    return await client_for(model).chat_stream(
        messages, tools=tools, model=model, max_tokens=max_tokens, on_delta=on_delta
    )


async def list_all_models() -> List[str]:
    out: List[str] = []
    if llm.configured:
        try:
            live = await llm.list_models()
        except Exception:
            live = []
        out.extend(GEMINI_MODEL_CATALOG)
        # Surface any other current-gen flash/pro text models we do not know yet.
        for m in live:
            if (
                m not in out
                and re.match(r"^gemini-3\.\d+-(flash|pro)$", m)
            ):
                out.append(m)
    if llm_bai.configured:
        out.extend([m for m in BAI_MODELS if m not in out])
    return out
