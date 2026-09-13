"""Shared agent runtime: persistence, approvals, sandbox setup, terminal states.

Moved verbatim out of the retired hand-rolled ``loop.py`` so the pydantic-ai
loop (``pa_loop.py``) owns the only agent implementation. No LLM code lives
here — just the surrounding machinery (tasks, events, approvals, sandboxes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from sqlalchemy import select

from .. import compute
from .. import push
from .. import sandbox as sbx_mod
from ..config import settings
from ..db import SessionLocal
from ..events import append_event
from ..models import Approval, Project, Task, new_id, utcnow

log = logging.getLogger("agent")


async def _update_task(task_id: str, **fields) -> None:
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return
        for k, v in fields.items():
            setattr(task, k, v)
        task.updated_at = utcnow()
        await session.commit()


async def _get_task(task_id: str) -> Optional[Task]:
    async with SessionLocal() as session:
        return await session.get(Task, task_id)


async def _emit(task_id: str, type_: str, payload: dict) -> None:
    async with SessionLocal() as session:
        await append_event(session, task_id, type_, payload)


# ── approvals ────────────────────────────────────────────


async def _wait_for_approval(worker, approval_id: str, timeout_s: int, stop_event: asyncio.Event) -> str:
    """Returns 'approve' | 'deny' | 'timeout' | 'stopped'."""
    ev = worker.get_approval_event(approval_id)
    deadline = time.monotonic() + max(30, timeout_s)
    while True:
        if stop_event.is_set():
            return "stopped"
        async with SessionLocal() as session:
            approval = await session.get(Approval, approval_id)
            status = approval.status if approval else "denied"
        if status == "approved":
            return "approve"
        if status == "denied":
            return "deny"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=min(2.0, remaining))
        except asyncio.TimeoutError:
            pass


async def _create_approval(task_id: str, tool: str, args: dict, tool_call_id: str) -> str:
    approval_id = new_id()
    description = _approval_description(tool, args)
    async with SessionLocal() as session:
        session.add(
            Approval(
                id=approval_id,
                task_id=task_id,
                kind="tool",
                description=description,
                payload={"tool": tool, "args": args, "tool_call_id": tool_call_id},
            )
        )
        await session.commit()
    return approval_id


def _approval_description(tool: str, args: dict) -> str:
    if tool == "bash":
        return f"bash: {args.get('command', '')[:200]}"
    if tool == "create_pr":
        return f"create PR: {args.get('title', '')[:200]}"
    return f"{tool}: {json.dumps(args, default=str)[:200]}"


async def _get_approval(approval_id: str) -> Optional[Approval]:
    async with SessionLocal() as session:
        return await session.get(Approval, approval_id)


async def _set_approval_status(approval_id: str, status: str) -> None:
    async with SessionLocal() as session:
        approval = await session.get(Approval, approval_id)
        if approval and approval.status == "pending":
            approval.status = status
            approval.decided_at = utcnow()
            await session.commit()


async def _expire_pending_approvals(task_id: str) -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Approval).where(Approval.task_id == task_id, Approval.status == "pending")
            )
        ).scalars().all()
        for a in rows:
            a.status = "expired"
            a.decided_at = utcnow()
        await session.commit()


# ── sandbox ──────────────────────────────────────────────


async def _setup_sandbox(task: Task, project: Project, resume: bool):
    """Returns (sandbox, reset_note). Sandbox is None on SandboxUnavailable (task already failed)."""
    reset_note = None
    if resume and task.sandbox_id:
        try:
            sbx = await sbx_mod.connect(task.sandbox_id)
            return sbx, None
        except sbx_mod.SandboxError:
            reset_note = (
                "The backend restarted and the previous sandbox is gone. A NEW sandbox was created "
                "with a fresh clone of the repository: prior file changes were LOST. Check git log / "
                "your summary above, and re-do the needed work (edits, commits)."
            )
    sbx = await compute.create_repo_sandbox(task.id, project.repo_url, project.default_branch)
    return sbx, reset_note


async def _latest_thread_sandbox_id(project_id: str, exclude_task_id: str) -> Optional[str]:
    """For chat threads: reuse the most recent turn's sandbox so files persist across turns."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(Task.sandbox_id)
                .where(
                    Task.project_id == project_id,
                    Task.id != exclude_task_id,
                    Task.sandbox_id.is_not(None),
                    Task.sandbox_id != "",
                )
                .order_by(Task.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return row or None


async def _ensure_branch(sbx, short_id: str) -> None:
    branch = f"agent/{short_id}"
    cmd = (
        f"cd {sbx_mod.REPO_DIR} && "
        f"(git rev-parse --verify {branch} >/dev/null 2>&1 && git checkout -q {branch}) || "
        f"git checkout -q -b {branch}"
    )
    await sbx_mod.run_command(sbx, cmd, timeout=60)


# Once a task reaches a terminal state the sandbox is no longer needed for work.
# Shorten its lifetime to a grace window so the diff stays viewable for a bit,
# then E2B reclaims it automatically (prevents sandbox leaks / quota exhaustion).
TERMINAL_SANDBOX_GRACE_S = 900


async def _release_sandbox_after_terminal(task_id: str, sbx, is_chat: bool = False) -> None:
    if sbx is None:
        return
    try:
        current = await _get_task(task_id)
        if current and current.status in ("done", "failed", "stopped"):
            grace = settings.SANDBOX_TIMEOUT_S if is_chat else TERMINAL_SANDBOX_GRACE_S
            await sbx_mod.keep_alive(sbx, seconds=grace)
    except Exception:
        log.debug("sandbox release after terminal failed for %s", task_id, exc_info=True)


def _sanitize_args(args: dict) -> dict:
    from ..sandbox import mask_secrets

    try:
        return json.loads(mask_secrets(json.dumps(args, default=str)))
    except Exception:
        return {"_error": "unserializable args"}


# ── terminal states ──────────────────────────────────────


async def _finish_done(task_id, result_summary, pr_url, pr_number, branch, iterations, tokens_used) -> None:
    await _emit(
        task_id,
        "task_completed",
        {
            "result_summary": result_summary or "",
            "pr_url": pr_url,
            "pr_number": pr_number,
            "branch": branch,
            "iterations": iterations,
            "tokens_used": tokens_used,
        },
    )
    await _update_task(
        task_id,
        status="done",
        result_summary=result_summary or "",
        iterations=iterations,
        tokens_used=tokens_used,
        error=None,
    )
    asyncio.create_task(push.push_task_completed(task_id, result_summary or ""))


async def _finish_failed(task_id: str, error: str) -> None:
    await _emit(task_id, "task_failed", {"error": error})
    await _update_task(task_id, status="failed", error=error)


async def _finish_stopped(task_id: str, reason: str) -> None:
    await _expire_pending_approvals(task_id)
    await _emit(task_id, "task_stopped", {"reason": reason})
    await _update_task(task_id, status="stopped", error=None)
