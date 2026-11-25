#!/usr/bin/env python
"""Restore a JSON backup produced by scripts/backup_db.py.

Usage (from backend/):
    python scripts/restore_db.py backup.json
    python scripts/restore_db.py --env .env.prod backup.json

Rows are inserted in foreign-key order; PostgreSQL identity sequences are
advanced past restored ids. With --replace (default) existing rows of the
tables present in the backup are deleted first.
"""

import argparse
import asyncio
import json
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
    parser.add_argument("backup", help="path of the JSON backup file to restore")
    parser.add_argument("--env", help="optional .env file to load before connecting")
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="keep existing rows instead of deleting them first (may violate unique constraints)",
    )
    args = parser.parse_args()

    _load_env(args.env)

    from app.backup import restore_database
    from app.db import engine

    data = json.loads(Path(args.backup).read_text(encoding="utf-8"))

    async def run() -> None:
        await restore_database(engine, data, replace=not args.no_replace)
        tables = data.get("tables", {})
        total = sum(len(rows) for rows in tables.values())
        print(f"restored {total} rows from {args.backup}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
