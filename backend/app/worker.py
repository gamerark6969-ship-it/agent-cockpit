import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Dict, List, Optional, Set

from sqlalchemy import or_, select, update

from .config import settings
from .db import SessionLocal
from .models import Task, TaskControl, utcnow

log = logging.getLogger("worker")

TERMINAL_STATUSES = {"done", "failed", "stopped"}
ACTIVE_STATUSES = {"queued", "running", "awaiting_approval"}


class Worker:
    """Background worker: processes up to WORKER_CONCURRENCY tasks at once from a queue."""

    def __init__(self):
        self.worker_id = str(uuid.uuid4())
        self.queue: asyncio.Queue = asyncio.Queue()
        self._queued: Set[str] = set()
        self._running: Set[str] = set()
        self._spawned: Set[asyncio.Task] = set()
        self.stop_events: Dict[str, asyncio.Event] = {}
        self.steer_inboxes: Dict[str, List[str]] = {}
        self.approval_events: Dict[str, asyncio.Event] = {}
        self.approval_results: Dict[str, bool] = {}
        self._runner: Optional[asyncio.Task] = None
        self._cleanup_runner: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────

    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._shutdown.clear()
            self._runner = asyncio.create_task(self._run(), name="agent-worker")
        if self._cleanup_runner is None or self._cleanup_runner.done():
            self._cleanup_runner = asyncio.create_task(
                self._cleanup_idle_sandboxes_loop(),
                name="sandbox-cleanup",
            )

    async def stop(self) -> None:
        self._shutdown.set()
        if self._cleanup_runner is not None:
            self._cleanup_runner.cancel()
            try:
                await self._cleanup_runner
            except (asyncio.CancelledError, Exception):
                pass
            self._cleanup_runner = None
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

    async def _cleanup_idle_sandboxes_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self.cleanup_idle_sandboxes()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("idle sandbox cleanup failed")
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=settings.SANDBOX_CLEANUP_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                pass

    async def cleanup_idle_sandboxes(self) -> int:
        from . import sandbox as sbx_mod

        cutoff = utcnow() - timedelta(seconds=settings.SANDBOX_IDLE_TIMEOUT_S)
        async with SessionLocal() as session:
            tasks = (
                await session.execute(
                    select(Task).where(
                        Task.sandbox_id.is_not(None),
                        Task.updated_at < cutoff,
                        Task.status.not_in(ACTIVE_STATUSES),
                    )
                )
            ).scalars().all()

            cleaned = 0
            for task in tasks:
                sandbox_id = task.sandbox_id
                if not sandbox_id or self.is_active(task.id):
                    continue
                if await sbx_mod.kill(sandbox_id):
                    task.sandbox_id = None
                    task.updated_at = utcnow()
                    cleaned += 1
            if cleaned:
                await session.commit()
            return cleaned

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
                        Task.status.in_(["queued", "running", "awaiting_approval"])
                    )
                )
            ).fetchall()
        for (task_id,) in rows:
            log.info("re-enqueueing interrupted task %s", task_id)
            self.enqueue(task_id)

    async def read_steer_controls(self,task_id: str) -> list[tuple[str,str]]:
        async with SessionLocal() as session:
            rows = (await session.execute(select(TaskControl).where(TaskControl.task_id == task_id,
                TaskControl.kind == "steer",TaskControl.processed_at.is_(None)).order_by(TaskControl.created_at).limit(50))).scalars().all()
        return [(row.id,row.message) for row in rows]

    async def checkpoint_history(self,task_id: str,history: list,controls: list[str]) -> bool:
        async with SessionLocal() as session:
            result = await session.execute(update(Task).where(Task.id == task_id,Task.lease_owner == self.worker_id).values(pa_history=history))
            if result.rowcount != 1:
                await session.rollback()
                return False
            if controls:
                await session.execute(update(TaskControl).where(TaskControl.id.in_(controls),TaskControl.processed_at.is_(None)).values(processed_at=utcnow()))
            await session.commit()
            return True

    async def stop_requested_in_db(self,task_id: str) -> bool:
        async with SessionLocal() as session:
            task = await session.get(Task,task_id)
            if task is None or task.status == "stopped":
                return True
            requested = await session.scalar(select(TaskControl.id).where(TaskControl.task_id == task_id,
                TaskControl.kind == "stop").limit(1))
            return requested is not None

    # ── main loop ────────────────────────────────────────

    async def _run(self) -> None:
        from .agent.pa_loop import run_agent_loop

        concurrency = max(1, int(settings.WORKER_CONCURRENCY))
        semaphore = asyncio.Semaphore(concurrency)

        async def runner(task_id: str) -> None:
            agent_task = None
            try:
                async with semaphore:
                    if self._shutdown.is_set():
                        return
                    if not await self._acquire_lease(task_id):
                        return
                    agent_task = asyncio.create_task(
                        run_agent_loop(task_id, self),
                        name=f"agent-loop-{task_id}",
                    )
                    done, _ = await asyncio.wait(
                        {agent_task}, timeout=settings.MAX_TASK_RUNTIME_S
                    )
                    if agent_task in done:
                        await agent_task
                    else:
                        await self._mark_timed_out(task_id)
                        agent_task.cancel()
                        await asyncio.gather(agent_task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("agent loop crashed for task %s", task_id)
                await self._mark_crashed(task_id)
            finally:
                if agent_task is not None and not agent_task.done():
                    agent_task.cancel()
                    await asyncio.gather(agent_task,return_exceptions=True)
                await self._release_lease(task_id)
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

    async def _acquire_lease(self, task_id: str) -> bool:
        now = utcnow()
        expires_at = now + timedelta(seconds=max(30, settings.MAX_TASK_RUNTIME_S + 30))
        async with SessionLocal() as session:
            result = await session.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.status.in_(ACTIVE_STATUSES),
                    or_(
                        Task.lease_owner.is_(None),
                        Task.lease_expires_at.is_(None),
                        Task.lease_expires_at <= now,
                        Task.lease_owner == self.worker_id,
                    ),
                )
                .values(lease_owner=self.worker_id, lease_expires_at=expires_at)
            )
            await session.commit()
            return result.rowcount == 1

    async def _release_lease(self, task_id: str) -> None:
        async with SessionLocal() as session:
            await session.execute(
                update(Task)
                .where(Task.id == task_id, Task.lease_owner == self.worker_id)
                .values(lease_owner=None, lease_expires_at=None)
            )
            await session.commit()

    async def _mark_timed_out(self, task_id: str) -> None:
        from .events import append_event

        timeout_s = settings.MAX_TASK_RUNTIME_S
        error = f"task exceeded the maximum runtime of {timeout_s} seconds"
        try:
            async with SessionLocal() as session:
                task = await session.get(Task, task_id)
                if task is None or task.status in TERMINAL_STATUSES:
                    return
                await append_event(
                    session,
                    task_id,
                    "task_failed",
                    {"error": error, "reason": "runtime_timeout"},
                )
                task.status = "failed"
                task.error = error
                task.updated_at = utcnow()
                await session.commit()
        except Exception:
            log.exception("failed to mark task %s as timed out", task_id)

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
