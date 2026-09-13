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
from functools import lru_cache
from typing import List, Optional

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
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


def _is_bai_model(name: str) -> bool:
    return name.startswith("deepseek") or name.startswith("b.ai/")


def _is_gemini_model(name: str) -> bool:
    clean = name.split("/", 1)[1] if name.startswith("models/") else name
    return clean.startswith("gemini")


@lru_cache(maxsize=32)
def build_single_model(name: str) -> Optional[Model]:
    """Construct one provider-backed model, or None if its key is missing."""
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
    chain: List[str] = [primary] + [m for m in PA_FALLBACK_MODELS if m != primary]
    models = [m for m in (build_single_model(name) for name in chain) if m is not None]
    if not models:
        return None
    if len(models) == 1:
        return models[0]
    return FallbackModel(models[0], *models[1:])


def any_configured() -> bool:
    return any(build_single_model(name) is not None for name in PA_FALLBACK_MODELS)
