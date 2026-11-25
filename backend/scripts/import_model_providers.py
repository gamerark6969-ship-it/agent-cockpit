"""Import requested OpenCode providers into encrypted application connectors."""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0,str(ROOT))

from app.agent.models import generate_text
from app.agent.provider_store import PROVIDER_DEFAULTS, validate_provider_config
from app.config import settings
from app.connectors import upsert
from app.db import engine, seed_settings


def resolve_key(value: str) -> str:
    match = re.fullmatch(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}",value)
    if match:
        return os.environ.get(match.group(1),"")
    if value.startswith("{file:"):
        raise ValueError("Provide this provider key through an environment variable for import")
    return value


async def import_providers(args) -> None:
    providers = json.loads(args.source.read_text(encoding="utf-8-sig")).get("provider",{})
    selected = []
    for kind in args.providers.split(","):
        if kind not in PROVIDER_DEFAULTS:
            raise ValueError("Only the requested GPT-6.1 Sol providers can be imported")
        source = providers.get(kind,{})
        options = source.get("options",{})
        config = validate_provider_config(kind,{
            "base_url":options.get("baseURL"), "api_key":resolve_key(options.get("apiKey", "")),
            "api_style":"responses" if source.get("npm") == "@ai-sdk/openai" else "chat",
        })
        if not config["api_key"]:
            print(f"{kind}: no configured key; skipped")
            continue
        selected.append((kind,config))
        print(f"{kind}: {config['base_url']} · gpt-6.1-sol · key present")
    if not args.apply:
        print("Dry run. Use --apply to save encrypted credentials.")
        return
    if not os.environ.get("APP_TOKEN"):
        raise ValueError("Set a persistent APP_TOKEN before importing")
    settings.validate_production_secrets()
    from app.main import run_migrations
    await asyncio.to_thread(run_migrations)
    try:
        for kind,config in selected:
            await upsert(kind,config=config)
        await seed_settings()
        print(f"Saved {len(selected)} encrypted model connectors")
        if args.probe:
            settings.GENERATION_TIMEOUT_S = 60
            try:
                result = await generate_text("Reply with exactly MODEL_CONNECTION_OK and nothing else.",model=args.probe)
                print(f"{args.probe}: {result[:120]}")
            except Exception as exc:
                status = getattr(exc,"status_code",None)
                print(f"{args.probe}: connection check failed ({type(exc).__name__}, HTTP {status})")
                raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path.home()/".config/opencode/opencode.json")
    parser.add_argument("--providers",default="freegg,agentrouter-openai,xingchi2")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--probe",help="Optional explicit provider/model to make a tiny live request")
    args = parser.parse_args()
    asyncio.run(import_providers(args))
