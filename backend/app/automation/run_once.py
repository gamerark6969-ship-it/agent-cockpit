"""Bounded scheduled worker: python -m app.automation.run_once --max-jobs 3."""

import argparse
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from ..config import settings
from ..db import engine
from .config import automation_settings as cfg
from .service import AutomationService
from .models import VideoRun


async def run_once(max_jobs: int = 3) -> int:
    service = AutomationService()
    await service.reserve_scheduled_slots()
    completed = 0
    failures = 0
    try:
        for _ in range(max_jobs):
            run_id = await service.claim_one()
            if run_id is None:
                break
            await service._process(run_id)
            async with service.session_factory() as session:
                result = await session.get(VideoRun, run_id)
                failures += int(result is not None and result.status == "failed")
            completed += 1
        if failures:
            raise RuntimeError(f"{failures} video run(s) failed; inspect Studio for details")
        return completed
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--check-due", action="store_true", help="Only reserve slots and report true/false")
    parser.add_argument("--include-agent",action="store_true",help="Also process one queued coding/chat task")
    parser.add_argument("--check-agent",action="store_true",help="Report whether a coding workspace is needed")
    args = parser.parse_args()
    settings.validate_production_secrets()
    if settings.ENVIRONMENT in ("production", "prod"):
        if settings.DATABASE_URL.startswith("sqlite") or not cfg.s3_configured:
            raise SystemExit("Scheduled cloud runs require shared Postgres and private S3-compatible storage")
    # This process is not running an event loop while Alembic creates its loop.
    from ..main import run_migrations
    run_migrations()

    async def execute():
        try:
            from ..agent.provider_store import refresh_provider_routes
            await refresh_provider_routes()
            from ..cloud_tasks import next_cloud_task, run_cloud_task
            if args.check_agent:
                print("true" if await next_cloud_task() else "false")
                return
            if args.check_due:
                service = AutomationService()
                await service.reserve_scheduled_slots()
                print("true" if await service.has_due_work() or await next_cloud_task() else "false")
                return
            failed = None
            try:
                count = await run_once(args.max_jobs)
                print(f"Production worker processed {count} run(s)")
            except RuntimeError as exc:
                failed = exc
            if args.include_agent:
                processed = await run_cloud_task()
                print(f"Coding/chat worker processed {int(processed)} task(s)")
            if failed:
                raise failed
        finally:
            await engine.dispose()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
