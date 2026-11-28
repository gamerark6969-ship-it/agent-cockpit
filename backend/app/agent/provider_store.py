"""Encrypted model connector configuration, shared by the API and cloud worker."""

from urllib.parse import urlsplit

import httpx

PROVIDER_DEFAULTS = {
    "freegg": "https://freegg.app/v1",
    "agentrouter-openai": "https://agentrouter.org/v1",
    "agentrouter-openai-two": "https://agentrouter.org/v1",
    "xingchi": "https://api.xingchiapi.com/v1",
    "xingchi2": "https://api.xingchiapi.com/v1",
}
runtime_routes: dict[str, dict[str, str]] = {}
disabled_routes: set[str] = set()


def validate_provider_config(kind: str, config: dict) -> dict[str, str]:
    base = str(config.get("base_url") or PROVIDER_DEFAULTS[kind]).rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Model providers require an HTTPS base URL without embedded credentials")
    style = str(config.get("api_style") or "responses")
    if style not in ("responses", "chat"):
        raise ValueError("Model API style must be responses or chat")
    return {"base_url":base, "api_key":str(config.get("api_key") or ""), "api_style":style}


def merge_provider_config(kind: str, saved: dict, changes: dict, enabled: bool) -> dict:
    changes = dict(changes)
    if not str(changes.get("api_key") or "").strip():
        changes.pop("api_key", None)
    config = validate_provider_config(kind, {**saved, **changes})
    old_base = saved.get("base_url") or PROVIDER_DEFAULTS[kind]
    if saved.get("api_key") and "api_key" not in changes and urlsplit(old_base).netloc != urlsplit(config["base_url"]).netloc:
        raise ValueError("Enter the key for the new provider host.")
    if enabled and not config["api_key"].strip():
        raise ValueError("Enter an API key to enable this provider.")
    return config


async def check_model_catalog(kind: str, config: dict) -> dict:
    """Verify the built-in provider catalog without incurring generation charges."""
    base = config.get("base_url", "").rstrip("/")
    if base != PROVIDER_DEFAULTS[kind]:
        return {"ok": False, "detail": "Automatic catalog checks are available for the standard provider URL."}
    if not config.get("api_key"):
        return {"ok": False, "detail": "Save a provider key first."}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.get(base + "/models", headers={"Authorization": "Bearer " + config["api_key"]})
        if response.status_code in (401, 403):
            return {"ok": False, "detail": f"Provider denied catalog access (HTTP {response.status_code}). Check the key and allowed clients in your provider account."}
        if response.status_code != 200:
            return {"ok": False, "detail": f"Catalog unavailable (HTTP {response.status_code})."}
        catalog = response.json()
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "detail": "The provider catalog could not be read. Try again later."}
    models = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        return {"ok": False, "detail": "The provider returned an unrecognized model catalog."}
    if any(isinstance(entry, dict) and entry.get("id") == "gpt-6.1-sol" for entry in models):
        return {"ok": True, "detail": "GPT-6.1 Sol is listed for this key."}
    return {"ok": False, "detail": "GPT-6.1 Sol is missing from this key’s model catalog."}


async def refresh_provider_routes() -> None:
    from sqlalchemy import select
    from ..crypto import decrypt_json
    from ..db import SessionLocal
    from ..models import Connector
    from .models import build_single_model

    routes = {}
    async with SessionLocal() as session:
        rows = (await session.execute(select(Connector).where(Connector.kind.in_(tuple(PROVIDER_DEFAULTS))))).scalars().all()
    disabled = set()
    for row in rows:
        route = f"{row.kind}/gpt-6.1-sol"
        if not row.enabled:
            disabled.add(route)
            continue
        try:
            config = validate_provider_config(row.kind,decrypt_json(row.config_enc))
        except ValueError:
            continue
        if config["api_key"]:
            routes[route] = config
    disabled_routes.clear()
    disabled_routes.update(disabled)
    runtime_routes.clear()
    runtime_routes.update(routes)
    build_single_model.cache_clear()
