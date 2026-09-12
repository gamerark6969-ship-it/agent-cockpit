import asyncio
import logging
from typing import Dict, List, Optional, Set

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Task, utcnow

log = logging.getLogger("worker")

TERMINAL_STATUSES = {"done", "failed", "stopped"}
ACTIVE_STATUSES = {"queued", "running", "awaiting_approval"}


class Worker:
    """Background worker: processes up to WORKER_CONCURRENCY tasks at once from a queue."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._queued: Set[str] = set()
        self._running: Set[str] = set()
        self._spawned: Set[asyncio.Task] = set()
        self.stop_events: Dict[str, asyncio.Event] = {}
        self.steer_inboxes: Dict[str, List[str]] = {}
        self.approval_events: Dict[str, asyncio.Event] = {}
        self.approval_results: Dict[str, bool] = {}
        self._runner: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────

    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._shutdown.clear()
            self._runner = asyncio.create_task(self._run(), name="agent-worker")

    async def stop(self) -> None:
        self._shutdown.set()
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):
                pass
            self._runner = None
        for t in list(self._spawned):
            t.cancel()
        if self._spawned:
            await asyncio.gather(*self._spawned, return_exceptions=True)
        self._spawned.clear()
        self._running.clear()

    # ── queue / registries ───────────────────────────────

    def enqueue(self, task_id: str) -> None:
        if task_id in self._queued or task_id in self._running:
            return
        self._queued.add(task_id)
        self.queue.put_nowait(task_id)

    async def enqueue_db(self, task_id: str) -> None:
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                return
        self.enqueue(task_id)

    @property
    def current(self) -> Optional[str]:
        return next(iter(self._running), None)

    def is_active(self, task_id: str) -> bool:
        return task_id in self._running or task_id in self._queued

    def get_stop_event(self, task_id: str) -> asyncio.Event:
        return self.stop_events.setdefault(task_id, asyncio.Event())

    def request_stop(self, task_id: str) -> None:
        self.get_stop_event(task_id).set()

    def steer(self, task_id: str, message: str) -> None:
        self.steer_inboxes.setdefault(task_id, []).append(message)

    def drain_steer(self, task_id: str) -> List[str]:
        msgs = self.steer_inboxes.get(task_id, [])
        if msgs:
            self.steer_inboxes[task_id] = []
        return msgs

    def get_approval_event(self, approval_id: str) -> asyncio.Event:
        return self.approval_events.setdefault(approval_id, asyncio.Event())

    def resolve_approval(self, approval_id: str, approved: bool) -> None:
        self.approval_results[approval_id] = approved
        ev = self.approval_events.get(approval_id)
        if ev is not None:
            ev.set()

    # ── resume on startup ────────────────────────────────

    async def resume_interrupted_tasks(self) -> None:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Task.id).where(
                        Task.status.in_(["running", "awaiting_approval"])
                    )
                )
            ).fetchall()
        for (task_id,) in rows:
            log.info("re-enqueueing interrupted task %s", task_id)
            self.enqueue(task_id)

    # ── main loop ────────────────────────────────────────

    async def _run(self) -> None:
        from .agent.loop import run_agent_loop

        concurrency = max(1, int(settings.WORKER_CONCURRENCY))
        semaphore = asyncio.Semaphore(concurrency)

        async def runner(task_id: str) -> None:
            try:
                async with semaphore:
                    if self._shutdown.is_set():
                        return
                    await run_agent_loop(task_id, self)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("agent loop crashed for task %s", task_id)
                await self._mark_crashed(task_id)
            finally:
                self._running.discard(task_id)
                self.stop_events.pop(task_id, None)
                self.steer_inboxes.pop(task_id, None)

        while not self._shutdown.is_set():
            try:
                task_id = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self._queued.discard(task_id)
            if task_id in self._running:
                continue
            self._running.add(task_id)
            spawned = asyncio.create_task(runner(task_id), name=f"agent-task-{task_id}")
            self._spawned.add(spawned)
            spawned.add_done_callback(self._spawned.discard)

    async def _mark_crashed(self, task_id: str) -> None:
        from .events import append_event

        try:
            async with SessionLocal() as session:
                task = await session.get(Task, task_id)
                if task is None or task.status in TERMINAL_STATUSES:
                    return
                await append_event(
                    session,
                    task_id,
                    "task_failed",
                    {"error": "internal worker error; see server logs"},
                )
                task.status = "failed"
                task.error = "internal worker error"
                task.updated_at = utcnow()
                await session.commit()
        except Exception:
            log.exception("failed to mark task %s as crashed", task_id)
