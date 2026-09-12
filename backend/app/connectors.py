import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from .config import settings
from .crypto import decrypt_json, encrypt_json, mask_config
from .db import SessionLocal
from .models import Connector, new_id, utcnow

log = logging.getLogger("connectors")

KNOWN_KINDS = ("github", "gmail", "slack", "notion", "google")

DEFAULT_NAMES = {
    "github": "GitHub",
    "gmail": "Gmail",
    "slack": "Slack",
    "notion": "Notion",
    "google": "Google Calendar & Drive",
}


def to_out(c: Connector) -> Dict[str, Any]:
    return {
        "id": c.id,
        "kind": c.kind,
        "name": c.name or DEFAULT_NAMES.get(c.kind, c.kind),
        "enabled": bool(c.enabled),
        "config": mask_config(decrypt_json(c.config_enc)),
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


async def list_connectors() -> List[Connector]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(Connector).order_by(Connector.kind))
        ).scalars().all()
    return list(rows)


async def get_by_kind(kind: str) -> Optional[Connector]:
    async with SessionLocal() as session:
        return (
            await session.execute(select(Connector).where(Connector.kind == kind))
        ).scalar_one_or_none()


async def get_config(kind: str) -> Dict[str, Any]:
    """Decrypted connector config, with env fallbacks for built-ins."""
    connector = await get_by_kind(kind)
    config: Dict[str, Any] = {}
    if connector is not None and connector.enabled:
        config = decrypt_json(connector.config_enc)
    if kind == "github":
        if not config.get("pat") and settings.GITHUB_PAT:
            config["pat"] = settings.GITHUB_PAT
    return config


async def is_enabled(kind: str) -> bool:
    connector = await get_by_kind(kind)
    if connector is None:
        return bool(kind == "github" and settings.GITHUB_PAT)
    return bool(connector.enabled)


async def upsert(
    kind: str,
    *,
    name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    enabled: bool = True,
) -> Connector:
    async with SessionLocal() as session:
        connector = (
            await session.execute(select(Connector).where(Connector.kind == kind))
        ).scalar_one_or_none()
        if connector is None:
            connector = Connector(
                id=new_id(),
                kind=kind,
                name=name or DEFAULT_NAMES.get(kind, kind),
                enabled=enabled,
                config_enc=encrypt_json(config or {}),
            )
            session.add(connector)
        else:
            if name is not None:
                connector.name = name
            if config is not None:
                merged = decrypt_json(connector.config_enc)
                merged.update(config)
                connector.config_enc = encrypt_json(merged)
            connector.enabled = enabled
            connector.updated_at = utcnow()
        await session.commit()
        await session.refresh(connector)
    return connector


async def update(
    connector_id: str,
    *,
    name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> Optional[Connector]:
    async with SessionLocal() as session:
        connector = await session.get(Connector, connector_id)
        if connector is None:
            return None
        if name is not None:
            connector.name = name
        if config is not None:
            merged = decrypt_json(connector.config_enc)
            merged.update(config)
            connector.config_enc = encrypt_json(merged)
        if enabled is not None:
            connector.enabled = enabled
        connector.updated_at = utcnow()
        await session.commit()
        await session.refresh(connector)
    return connector


async def delete(connector_id: str) -> bool:
    async with SessionLocal() as session:
        connector = await session.get(Connector, connector_id)
        if connector is None:
            return False
        await session.delete(connector)
        await session.commit()
    return True


async def seed_from_env() -> None:
    """Create a GitHub connector from GITHUB_PAT on first boot (if none exists)."""
    if not settings.GITHUB_PAT:
        return
    existing = await get_by_kind("github")
    if existing is not None:
        return
    await upsert("github", config={"pat": settings.GITHUB_PAT})
    log.info("seeded GitHub connector from GITHUB_PAT")
