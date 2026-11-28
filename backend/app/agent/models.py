"""Pydantic AI model construction — native multi-provider routing + fallback.

This replaces the hand-rolled provider/failover logic that used to live in
``llm.py`` + ``loop.py``. Two providers are supported:

  * b.ai       — OpenAI-compatible endpoint (``deepseek-*`` models)
  * Google AI  — native Gemini API (``gemini-*`` models)

A ``FallbackModel`` wraps the primary model with the rest of the chain;
pydantic-ai fails over automatically on ``ModelAPIError`` (429/503/400/...).
Provider failover, streaming and thought-signature handling are all owned by
pydantic-ai instead of us re-implementing them.
"""

from __future__ import annotations

import logging
import shutil
import httpx
from openai import APIError
from pydantic_ai.exceptions import ModelAPIError
from functools import lru_cache
from typing import List, Optional

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings

log = logging.getLogger("agent.models")

# Default model when a task does not pin one and settings don't either.
PA_DEFAULT_MODEL = "deepseek-v4.1-flash"

# Failover order: tried left-to-right; whichever answers becomes sticky for
# the rest of the task. gemini-flash-latest is deliberately absent — it is an
# alias of gemini-3.8-flash and shares the same quota.
PA_FALLBACK_MODELS = ("deepseek-v4.1-flash", "gemini-3.8-flash")

# Known-good, currently available chat models surfaced in the app. Deprecated
# ids (e.g. gemini-2.5-pro) are deliberately excluded.
GEMINI_MODEL_CATALOG = [
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-pro-latest",
    "gemini-3.1-pro-preview",
]

BAI_MODELS = ["deepseek-v4.1-flash"]

# Order matters: first entry is the UI default, the rest are failover targets.
MODEL_CATALOG = [PA_DEFAULT_MODEL, *GEMINI_MODEL_CATALOG]


def list_pa_models() -> List[str]:
    """Model ids to surface in the app, filtered by configured providers."""
    out: List[str] = []
    from .provider_store import disabled_routes, runtime_routes
    if settings.FREEGG_API_KEY:
        out.append("freegg/gpt-6.1-sol")
    out.extend(name for name in sorted(runtime_routes,key=lambda name: (not name.startswith("freegg/"),name)) if name not in out)
    if settings.AGENTROUTER_OPENAI_API_KEY:
        out.append("agentrouter-openai/gpt-6.1-sol")
    if settings.XINGCHI_BASE_URL and settings.XINGCHI_API_KEY:
        out.append("xingchi2/gpt-6.1-sol")
    if settings.OPENCODE_API_KEY:
        out.append("opencode/gpt-6.1-sol")
    if settings.BAI_API_KEY:
        out.extend([m for m in BAI_MODELS if m not in out])
    if settings.AGENTROUTER_API_KEY:
        out.extend([m for m in GEMINI_MODEL_CATALOG if m not in out])
    return [name for name in dict.fromkeys(out) if name not in disabled_routes]


def _is_bai_model(name: str) -> bool:
    return name.startswith("deepseek") or name.startswith("b.ai/")


def _is_gemini_model(name: str) -> bool:
    clean = name.split("/", 1)[1] if name.startswith("models/") else name
    return clean.startswith("gemini")


@lru_cache(maxsize=32)
def build_single_model(name: str) -> Optional[Model]:
    """Construct one provider-backed model, or None if its key is missing."""
    from .provider_store import disabled_routes, runtime_routes
    if name in disabled_routes:
        return None
    route = runtime_routes.get(name)
    if not route and name == "freegg/gpt-6.1-sol" and settings.FREEGG_API_KEY:
        route = {"base_url":settings.FREEGG_BASE_URL, "api_key":settings.FREEGG_API_KEY, "api_style":"responses"}
    elif not route and name == "agentrouter-openai/gpt-6.1-sol" and settings.AGENTROUTER_OPENAI_API_KEY:
        route = {"base_url":settings.AGENTROUTER_OPENAI_BASE_URL, "api_key":settings.AGENTROUTER_OPENAI_API_KEY, "api_style":"responses"}
    if route:
        provider = OpenAIProvider(base_url=route["base_url"],api_key=route["api_key"])
        factory = OpenAIChatModel if route["api_style"] == "chat" else OpenAIResponsesModel
        return factory(name.split("/",1)[1],provider=provider,settings={"max_tokens":8192,"openai_reasoning_effort":"low"} if route["api_style"] == "responses" else {"max_tokens":8192})
    if name.startswith(("xingchi2/", "xingchi/")):
        if not settings.XINGCHI_BASE_URL or not settings.XINGCHI_API_KEY:
            return None
        provider = OpenAIProvider(base_url=settings.XINGCHI_BASE_URL, api_key=settings.XINGCHI_API_KEY)
        factory = OpenAIChatModel if settings.XINGCHI_API_STYLE == "chat" else OpenAIResponsesModel
        return factory(name.split("/", 1)[1], provider=provider)
    if name == "opencode/gpt-6.1-sol":
        if not settings.OPENCODE_API_KEY:
            return None
        return OpenAIResponsesModel("gpt-6.1-sol", provider=OpenAIProvider(
            base_url=settings.OPENCODE_BASE_URL, api_key=settings.OPENCODE_API_KEY))
    if _is_bai_model(name):
        if not settings.BAI_API_KEY:
            log.warning("BAI_API_KEY not configured; cannot use %r", name)
            return None
        clean = name.split("/", 1)[1] if name.startswith("b.ai/") else name
        provider = OpenAIProvider(
            base_url=settings.BAI_BASE_URL or "https://api.b.ai/v1",
            api_key=settings.BAI_API_KEY,
        )
        return OpenAIChatModel(clean, provider=provider)

    if _is_gemini_model(name):
        if not settings.AGENTROUTER_API_KEY:
            log.warning("AGENTROUTER_API_KEY not configured; cannot use %r", name)
            return None
        clean = name.split("/", 1)[1] if name.startswith("models/") else name
        provider = GoogleProvider(api_key=settings.AGENTROUTER_API_KEY)
        return GoogleModel(clean, provider=provider)

    log.warning("unknown model %r — no provider route", name)
    return None


def build_model_chain(primary: str) -> Optional[Model]:
    """Primary model + configured fallbacks as a single pydantic-ai Model."""
    # Explicit new providers never silently fall back to an unrelated paid route.
    chain: List[str] = [primary] if "/" in primary and not primary.startswith("models/") else [primary] + [m for m in PA_FALLBACK_MODELS if m != primary]
    models = [m for m in (build_single_model(name) for name in chain) if m is not None]
    if not models:
        return None
    if len(models) == 1:
        return models[0]
    return FallbackModel(models[0], *models[1:])


def any_configured() -> bool:
    return bool(list_pa_models())


FREE_MODELS = ("opencode/deepseek-v4.1-flash-free", "opencode/big-pickle", "opencode/mimo-v2.6-flash-free", "opencode/beam-free", "opencode/nemotron-3.5-lightning-free")

SOL_FAILOVER = "auto/gpt-6.1-sol"
SOL_ROUTES = tuple(f"{provider}/gpt-6.1-sol" for provider in (
    "freegg", "agentrouter-openai", "agentrouter-openai-two", "xingchi", "xingchi2",
))


def list_generation_models() -> List[str]:
    """Free models use the real OpenCode client, not a spoofed gateway request."""
    local = [f"local/{settings.LOCAL_LLM_MODEL}"] if settings.LOCAL_LLM_ENABLED or settings.LOCAL_LLM_REMOTE_WORKER else []
    configured = list_pa_models()
    pool = [SOL_FAILOVER] if any(route in configured for route in SOL_ROUTES) else []
    return [*(FREE_MODELS if settings.OPENCODE_REMOTE_WORKER or shutil.which(settings.OPENCODE_BIN) else ()), *local, *pool, *configured]


async def generate_text(prompt: str, model: str | None = None, system_prompt: str = "") -> str:
    from .opencode_runtime import FreeModelUnavailable, run_free_model
    from .local_inference import generate_local

    if model == SOL_FAILOVER:
        configured = list_pa_models()
        attempted = []
        for route in SOL_ROUTES:
            if route in configured:
                attempted.append(route)
                try:
                    return await _generate_provider_text(prompt, route, system_prompt)
                except (ModelAPIError, APIError, httpx.TransportError, TimeoutError):
                    log.warning("Video generation provider unavailable: %s", route)
        if attempted:
            raise RuntimeError("GPT-6.1 Sol providers unavailable: " + ", ".join(attempted))
        raise RuntimeError("No GPT-6.1 Sol failover provider is configured")

    if model is None and "freegg/gpt-6.1-sol" in list_pa_models():
        try:
            return await generate_text(prompt,model="freegg/gpt-6.1-sol",system_prompt=system_prompt)
        except Exception:
            # FreeGG can be unavailable too; the remaining automatic routes cost no money.
            pass

    selected = model or (FREE_MODELS[0] if shutil.which(settings.OPENCODE_BIN) else None)
    if selected == f"local/{settings.LOCAL_LLM_MODEL}":
        return await generate_local(prompt, system_prompt)
    if selected in FREE_MODELS:
        try:
            return await run_free_model(selected, f"{system_prompt}\n\n{prompt}")
        except FreeModelUnavailable:
            if model is not None or not settings.LOCAL_LLM_ENABLED:
                raise
            return await generate_local(prompt, system_prompt)
    if not selected:
        if settings.LOCAL_LLM_ENABLED:
            return await generate_local(prompt, system_prompt)
        raise RuntimeError("Install OpenCode to use free models, or explicitly select a configured provider")
    if selected not in list_pa_models():
        raise RuntimeError("The selected model is not configured")
    return await _generate_provider_text(prompt, selected, system_prompt)


async def _generate_provider_text(prompt: str, selected: str, system_prompt: str) -> str:
    from pydantic_ai import Agent

    backed = build_single_model(selected)
    if backed is None:
        raise RuntimeError("The selected model is unavailable")
    import asyncio
    async with asyncio.timeout(settings.GENERATION_TIMEOUT_S):
        model_settings = {"max_tokens":8192}
        if selected.endswith("/gpt-6.1-sol"):
            model_settings["openai_reasoning_effort"] = "low"
        async with Agent(backed, system_prompt=system_prompt, model_settings=model_settings).run_stream(prompt) as result:
            return str(await result.get_output())
