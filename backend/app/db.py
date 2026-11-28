from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings

def create_app_engine(url: str):
    db_engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    if db_engine.dialect.name == "sqlite":
        @event.listens_for(db_engine.sync_engine, "connect")
        def enable_foreign_keys(connection, _):
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
    return db_engine


engine = create_app_engine(settings.DATABASE_URL)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session


# â”€â”€ settings store (single JSON row) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _default_settings() -> dict:
    from .schemas import SettingsOut
    from .agent.models import PA_DEFAULT_MODEL, list_pa_models

    data = SettingsOut().model_dump()
    models = list_pa_models()
    data["default_model"] = models[0] if models else PA_DEFAULT_MODEL
    return data


async def get_settings_data(session) -> dict:
    """Stored settings merged over defaults (so the shape is always complete)."""
    from .models import Setting
    from sqlalchemy import select

    row = (await session.execute(select(Setting).where(Setting.id == 1))).scalar_one_or_none()
    stored = dict(row.data) if row is not None and row.data else {}
    defaults = _default_settings()
    merged = {**defaults, **stored}
    from .agent.models import list_pa_models
    available = list_pa_models()
    if available and merged.get("default_model") == "deepseek-v4.1-flash" and "deepseek-v4.1-flash" not in available:
        merged["default_model"] = available[0]
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
            return
        # One-time migration: the old default (50) capped long, multi-file tasks.
        # Only bump it when it still holds the legacy default.
        data = dict(row.data or {})
        from .agent.models import list_pa_models
        available_models = list_pa_models()
        if available_models and data.get("default_model") == "deepseek-v4.1-flash" and "deepseek-v4.1-flash" not in available_models:
            data["default_model"] = available_models[0]
            row.data = data
            await session.commit()
        if data.get("max_iterations") in (None, 50):
            data["max_iterations"] = _default_settings()["max_iterations"]
            row.data = data
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
