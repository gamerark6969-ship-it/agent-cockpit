"""Database-agnostic backup/restore as a portable, versioned JSON document.

Works for both SQLite and PostgreSQL deployments: every table is dumped to JSON
rows (datetimes -> ISO strings, bytes -> base64 markers) and restored in
foreign-key order into an existing or empty schema. PostgreSQL identity
sequences are advanced past restored ids so later inserts cannot collide.
"""

import base64
import sqlalchemy as sa
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from .models import Base

FORMAT_VERSION = 1


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.fromisoformat(value["__dt__"])
        if "__b64__" in value:
            return base64.b64decode(value["__b64__"])
    return value


async def dump_database(engine: AsyncEngine) -> dict:
    """Dump every table to {"format": 1, "tables": {name: [row, ...]}}."""
    tables: dict[str, list[dict]] = {}
    async with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            rows = (await connection.execute(sa.select(table))).mappings().all()
            tables[table.name] = [
                {key: _encode(value) for key, value in dict(row).items()} for row in rows
            ]
    return {"format": FORMAT_VERSION, "tables": tables}


async def restore_database(engine: AsyncEngine, data: dict, replace: bool = True) -> None:
    """Restore a dump produced by dump_database into the configured database."""
    if data.get("format") != FORMAT_VERSION:
        raise ValueError(f"unsupported backup format: {data.get('format')!r}")
    backup_tables = data.get("tables") or {}

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        if replace:
            for table in reversed(Base.metadata.sorted_tables):
                if table.name in backup_tables:
                    await connection.execute(sa.delete(table))
        for table in Base.metadata.sorted_tables:
            rows = backup_tables.get(table.name)
            if not rows:
                continue
            columns = {column.name for column in table.columns}
            decoded = [
                {key: _decode(value) for key, value in row.items() if key in columns}
                for row in rows
            ]
            await connection.execute(table.insert(), decoded)

    await _repair_postgres_sequences(engine)


async def _repair_postgres_sequences(engine: AsyncEngine) -> None:
    """Advance identity sequences past restored ids so inserts cannot collide."""
    if not engine.url.get_backend_name().startswith("postgresql"):
        return
    for table in Base.metadata.sorted_tables:
        pk_columns = list(table.primary_key.columns)
        if len(pk_columns) != 1:
            continue
        column = pk_columns[0]
        if not isinstance(column.type, sa.Integer) or column.autoincrement is False:
            continue
        statement = sa.text(
            f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "
            f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 1), "
            f"EXISTS (SELECT 1 FROM {table.name}))"
        )
        async with engine.begin() as connection:
            await connection.execute(statement)
