"""Bounded coding/chat jobs on the same free scheduled runner."""

import asyncio

from sqlalchemy import or_, select

from .config import settings
from .db import SessionLocal
from .models import Task, utcnow
from .worker import ACTIVE_STATUSES, Worker


async def next_cloud_task() -> str | None:
    async with SessionLocal() as session:
        return await session.scalar(select(Task.id).where(Task.status.in_(ACTIVE_STATUSES),or_(
            Task.lease_owner.is_(None),Task.lease_expires_at.is_(None),Task.lease_expires_at < utcnow()
        )).order_by(Task.created_at).limit(1))


async def run_cloud_task() -> bool:
    task_id = await next_cloud_task()
    if task_id is None:
        return False
    worker = Worker()
    if not await worker._acquire_lease(task_id):
        return False
    worker._running.add(task_id)
    try:
        from .agent.pa_loop import run_agent_loop
        job = asyncio.create_task(run_agent_loop(task_id,worker))
        try:
            done,_ = await asyncio.wait({job},timeout=settings.MAX_TASK_RUNTIME_S)
            if job in done:
                await job
            else:
                await worker._mark_timed_out(task_id)
        finally:
            if not job.done():
                job.cancel()
            await asyncio.gather(job,return_exceptions=True)
    finally:
        await worker._release_lease(task_id)
        worker._running.discard(task_id)
    return True
