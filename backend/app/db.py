from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings

_ensure_sqlite_driver = settings.DATABASE_URL

engine = create_async_engine(_ensure_sqlite_driver, echo=False, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session


# ── settings store (single JSON row) ─────────────────────


def _default_settings() -> dict:
    from .schemas import SettingsOut
    from .agent.models import PA_DEFAULT_MODEL

    data = SettingsOut().model_dump()
    data["default_model"] = PA_DEFAULT_MODEL
    return data


async def get_settings_data(session) -> dict:
    """Stored settings merged over defaults (so the shape is always complete)."""
    from .models import Setting
    from sqlalchemy import select

    row = (await session.execute(select(Setting).where(Setting.id == 1))).scalar_one_or_none()
    stored = dict(row.data) if row is not None and row.data else {}
    defaults = _default_settings()
    merged = {**defaults, **stored}
    merged["permissions"] = {
        **defaults["permissions"],
        **(stored.get("permissions") or {}),
    }
    return merged


async def seed_settings() -> None:
    from .models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, 1)
        if row is None:
            session.add(Setting(id=1, data=_default_settings()))
            await session.commit()


async def save_settings(data: dict) -> None:
    from .models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, 1)
        if row is None:
            session.add(Setting(id=1, data=data))
        else:
            row.data = data
        await session.commit()
