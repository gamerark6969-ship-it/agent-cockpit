"""Encrypted model connector configuration, shared by the API and cloud worker."""

from urllib.parse import urlsplit

PROVIDER_DEFAULTS = {
    "freegg": "https://freegg.app/v1",
    "agentrouter-openai": "https://agentrouter.org/v1",
    "agentrouter-openai-two": "https://agentrouter.org/v1",
    "xingchi": "https://api.xingchiapi.com/v1",
    "xingchi2": "https://api.xingchiapi.com/v1",
}
runtime_routes: dict[str, dict[str, str]] = {}


def validate_provider_config(kind: str, config: dict) -> dict[str, str]:
    base = str(config.get("base_url") or PROVIDER_DEFAULTS[kind]).rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query:
        raise ValueError("Model providers require an HTTPS base URL without embedded credentials")
    style = str(config.get("api_style") or "responses")
    if style not in ("responses", "chat"):
        raise ValueError("Model API style must be responses or chat")
    return {"base_url":base, "api_key":str(config.get("api_key") or ""), "api_style":style}


async def refresh_provider_routes() -> None:
    from sqlalchemy import select
    from ..crypto import decrypt_json
    from ..db import SessionLocal
    from ..models import Connector
    from .models import build_single_model

    routes = {}
    async with SessionLocal() as session:
        rows = (await session.execute(select(Connector).where(
            Connector.kind.in_(tuple(PROVIDER_DEFAULTS)), Connector.enabled.is_(True)
        ))).scalars().all()
    for row in rows:
        try:
            config = validate_provider_config(row.kind,decrypt_json(row.config_enc))
        except ValueError:
            continue
        if config["api_key"]:
            routes[f"{row.kind}/gpt-6.1-sol"] = config
    runtime_routes.clear()
    runtime_routes.update(routes)
    build_single_model.cache_clear()
