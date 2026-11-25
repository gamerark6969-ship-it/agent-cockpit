#!/usr/bin/env python
"""Dump the configured database to a JSON backup file.

Usage (from backend/):
    python scripts/backup_db.py backup.json          # uses DATABASE_URL/.env
    python scripts/backup_db.py --env .env.prod backup.json

The output is a portable, versioned JSON document containing every table, so it
can be restored into a fresh database (or used to seed another environment).
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env(env_file: str | None) -> None:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parents[1]
    candidates = [Path(env_file)] if env_file else [here / ".env", here.parent / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="path of the JSON backup file to write")
    parser.add_argument("--env", help="optional .env file to load before connecting")
    args = parser.parse_args()

    _load_env(args.env)

    from app.backup import dump_database
    from app.db import engine

    async def run() -> None:
        data = await dump_database(engine)
        Path(args.output).write_text(
            __import__("json").dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tables = data.get("tables", {})
        counts = {name: len(rows) for name, rows in tables.items()}
        total = sum(counts.values())
        print(f"wrote {args.output}: {total} rows across {len(counts)} tables")
        for name in sorted(counts):
            print(f"  {name}: {counts[name]}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
