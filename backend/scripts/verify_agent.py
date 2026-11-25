"""Verify a tiny real FreeGG chat task, including persisted checkpoints and events."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0,str(ROOT))


async def verify():
    from app.agent.pa_loop import run_agent_loop
    from app.agent.provider_store import refresh_provider_routes
    from app.db import SessionLocal, engine
    from app.main import run_migrations
    from app.models import Project, Task
    from app.worker import Worker
    await asyncio.to_thread(run_migrations)
    await refresh_provider_routes()
    async with SessionLocal() as session:
        project = Project(name="Model connection check",kind="chat")
        session.add(project)
        await session.flush()
        task = Task(project_id=project.id,prompt="Reply with exactly TASK_CONNECTION_OK and nothing else. Do not use any tools.",model="freegg/gpt-6.1-sol")
        session.add(task)
        await session.commit()
        task_id = task.id
    worker = Worker()
    if not await worker._acquire_lease(task_id):
        raise RuntimeError("Could not acquire the test task")
    worker._running.add(task_id)
    try:
        await asyncio.wait_for(run_agent_loop(task_id,worker),timeout=90)
        async with SessionLocal() as session:
            result = await session.get(Task,task_id)
            print("Chat task:",result.status,"route:",result.model,"history:",bool(result.pa_history))
            if result.status != "done" or not result.pa_history or result.model != "freegg/gpt-6.1-sol":
                raise RuntimeError("The chat task did not finish with a durable provider-aware checkpoint")
            print("Result:",result.result_summary)
    finally:
        await worker._release_lease(task_id)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
