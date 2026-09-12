import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select

from .. import sandbox as sbx_mod
from .. import compute
from .. import push
from ..config import settings
from ..db import SessionLocal, get_settings_data
from ..events import append_event
from ..llm import (
    LLMError,
    LLMNotConfigured,
    any_configured,
    chat as llm_chat,
    chat_stream as llm_chat_stream,
    client_for,
)
from ..models import Approval, Message, Task, Project, new_id, utcnow
from ..permissions import evaluate
from .prompts import COMPACTION_PROMPT, GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT
from .tools import ToolContext, all_tool_definitions, dispatch

log = logging.getLogger("agent")

TERMINAL = ("done", "failed", "stopped")
MAX_CONTEXT_CHARS = 150_000
COMPACT_KEEP = 8


# ── persistence helpers ──────────────────────────────────


async def _save_message(task_id: str, msg: Dict[str, Any]) -> None:
    async with SessionLocal() as session:
        max_seq = (
            await session.execute(select(func.max(Message.seq)).where(Message.task_id == task_id))
        ).scalar_one()
        session.add(
            Message(
                task_id=task_id,
                seq=(max_seq or 0) + 1,
                role=msg["role"],
                content=msg.get("content") or "",
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )
        )
        await session.commit()


async def _load_messages(task_id: str) -> List[Dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Message).where(Message.task_id == task_id).order_by(Message.seq)
            )
        ).scalars().all()
    out = []
    for m in rows:
        msg: Dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.tool_calls:
            msg["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            msg["tool_call_id"] = m.tool_call_id
            msg["role"] = "tool"
            msg["name"] = "tool"
        out.append(msg)
    return out


def _is_internal_user_text(text: str) -> bool:
    return text.startswith("[") or text.startswith("If you have fully answered") or text.startswith(
        "Continue with the task"
    )


async def _load_thread_history(project_id: str, exclude_task: str, max_turns: int = 40) -> List[Dict[str, Any]]:
    """Prior completed turns in a chat thread as plain user/assistant text for context."""
    async with SessionLocal() as session:
        tasks = (
            await session.execute(
                select(Task)
                .where(Task.project_id == project_id, Task.id != exclude_task, Task.status == "done")
                .order_by(Task.created_at)
            )
        ).scalars().all()
        history: List[Dict[str, Any]] = []
        for t in tasks:
            rows = (
                await session.execute(
                    select(Message).where(Message.task_id == t.id).order_by(Message.seq)
                )
            ).scalars().all()
            user_texts = [
                (m.content or "").strip()
                for m in rows
                if m.role == "user" and (m.content or "").strip() and not _is_internal_user_text((m.content or "").strip())
            ]
            assistant_texts = [
                (m.content or "").strip() for m in rows if m.role == "assistant" and (m.content or "").strip()
            ]
            if user_texts:
                history.append({"role": "user", "content": user_texts[0][:4000]})
            final = (t.result_summary or "").strip() or (assistant_texts[-1] if assistant_texts else "")
            if final:
                history.append({"role": "assistant", "content": final[:4000]})
    return history[-max_turns:]


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


def _wire_msg(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Message as sent to the LLM API (strip our internal keys)."""
    out = {"role": msg["role"]}
    if msg.get("content"):
        out["content"] = msg["content"]
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
        out.setdefault("content", None)
        if out["content"] is None:
            out.pop("content")
    if msg["role"] == "tool" or msg.get("tool_call_id"):
        out["tool_call_id"] = msg["tool_call_id"]
        out["content"] = msg.get("content") or ""
    return out


# ── streaming LLM turn ───────────────────────────────────

DELTA_FLUSH_CHARS = 90
DELTA_FLUSH_SECONDS = 0.25

# When the primary model is rate-limited (429) or unavailable, fail over in order.
FALLBACK_MODELS = ("deepseek-v4.1-flash", "gemini-3.8-flash", "gemini-flash-latest")


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text


async def _llm_stream(task_id: str, messages: List[Dict[str, Any]], tool_defs, model: str) -> tuple:
    """Call the LLM with streaming, emitting throttled ``agent_delta`` events.

    On quota errors (429) it fails over to another model, persists the switch
    on the task and returns the model that actually answered, so later
    iterations stick with the working model.
    """
    pending: List[str] = []
    pending_len = 0
    last_flush = time.monotonic()

    async def flush() -> None:
        nonlocal pending, pending_len, last_flush
        if not pending:
            return
        text = "".join(pending)
        pending = []
        pending_len = 0
        last_flush = time.monotonic()
        await _emit(task_id, "agent_delta", {"content": text})

    async def on_delta(piece: str) -> None:
        nonlocal pending_len
        pending.append(piece)
        pending_len += len(piece)
        if pending_len >= DELTA_FLUSH_CHARS or (time.monotonic() - last_flush) >= DELTA_FLUSH_SECONDS:
            await flush()

    chain = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_exc: Exception | None = None
    for i, candidate in enumerate(chain):
        if not client_for(candidate).configured:
            continue
        try:
            assistant = await llm_chat_stream(
                [_wire_msg(m) for m in messages], tools=tool_defs, model=candidate, on_delta=on_delta
            )
            await flush()
            return assistant, candidate
        except LLMNotConfigured:
            raise
        except LLMError as exc:
            last_exc = exc
            if not _is_rate_limited(exc) or i >= len(chain) - 1:
                raise
            nxt = next((m for m in chain[i + 1:] if client_for(m).configured), None)
            if nxt is None:
                raise
            await _emit(task_id, "model_switch", {"from": candidate, "to": nxt})
            if candidate == model:
                # sticky: remember the working model for the rest of the task
                await _update_task(task_id, model=nxt)
    raise last_exc or LLMError("LLM request failed")


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


# ── compaction ───────────────────────────────────────────


async def _maybe_compact(task_id: str, messages: List[Dict[str, Any]], model: str) -> List[Dict[str, Any]]:
    total = sum(len(m.get("content") or "") for m in messages)
    if total <= MAX_CONTEXT_CHARS or len(messages) <= COMPACT_KEEP + 2:
        return messages
    cut = len(messages) - COMPACT_KEEP
    while cut < len(messages) and messages[cut].get("role") == "tool":
        cut += 1
    if cut <= 1:
        return messages
    system_msg = messages[0] if messages[0]["role"] == "system" else None
    old = [m for m in messages[:cut] if m is not system_msg]
    kept = messages[cut:]
    if not old:
        return messages
    transcript = []
    for m in old:
        role = m["role"]
        content = (m.get("content") or "").strip()
        if m.get("tool_calls"):
            content += " " + json.dumps(m["tool_calls"], default=str)
        transcript.append(f"[{role}] {content}")
    try:
        summary_msg = await llm_chat(
            [
                {"role": "system", "content": COMPACTION_PROMPT},
                {"role": "user", "content": "\n\n".join(transcript)[:100_000]},
            ],
            model=model,
        )
        summary = (summary_msg.get("content") or "").strip()
    except Exception as exc:
        log.warning("compaction failed, keeping full context: %s", exc)
        return messages
    if not summary:
        return messages
    compacted = {"role": "user", "content": f"[CONVERSATION SUMMARY — earlier turns were compacted]\n{summary}"}
    new_messages = ([system_msg] if system_msg else []) + [compacted] + kept
    # persist: replace all messages
    async with SessionLocal() as session:
        await session.execute(delete(Message).where(Message.task_id == task_id))
        for i, m in enumerate(new_messages, start=1):
            session.add(
                Message(
                    task_id=task_id,
                    seq=i,
                    role=m["role"],
                    content=m.get("content") or "",
                    tool_calls=m.get("tool_calls"),
                    tool_call_id=m.get("tool_call_id"),
                )
            )
        await session.commit()
    await _emit(
        task_id,
        "error",
        {"message": f"context compacted: {len(messages)} messages -> {len(new_messages)} messages"},
    )
    return new_messages


# ── sandbox setup ────────────────────────────────────────


async def _setup_sandbox(task: Task, project: Project, resume: bool) -> Tuple[Optional[Any], Optional[str]]:
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


# ── dangling tool calls (resume after crash) ─────────────


def _dangling_tool_calls(messages: List[Dict[str, Any]]) -> List[dict]:
    """Tool calls from the last assistant message that have no tool result yet."""
    answered = set()
    last_calls: List[dict] = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            last_calls = m["tool_calls"]
        elif m.get("tool_call_id"):
            answered.add(m["tool_call_id"])
            last_calls = []
    return [tc for tc in last_calls if tc.get("id") not in answered]


async def _pending_approval_for(task_id: str, tool_call_id: str) -> Optional[Approval]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Approval).where(Approval.task_id == task_id, Approval.status == "pending")
            )
        ).scalars().all()
    for a in rows:
        if (a.payload or {}).get("tool_call_id") == tool_call_id:
            return a
    return None


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


# ── the loop ─────────────────────────────────────────────


async def run_agent_loop(task_id: str, worker) -> None:
    task = await _get_task(task_id)
    if task is None:
        return
    if task.status in TERMINAL:
        return

    stop_event = worker.get_stop_event(task_id)
    if stop_event.is_set():
        await _finish_stopped(task_id, "stopped via API before start")
        return

    async with SessionLocal() as session:
        project = await session.get(Project, task.project_id)
        settings_data = await get_settings_data(session)
    if project is None:
        await _finish_failed(task_id, "project not found")
        return

    resume = task.status in ("running", "awaiting_approval")
    model = task.model or str(settings_data.get("default_model") or "gemini-3.8-flash")
    max_iterations = int(settings_data.get("max_iterations") or 50)
    token_budget = int(settings_data.get("token_budget") or 2000000)
    command_timeout_s = int(settings_data.get("command_timeout_s") or 600)
    approval_timeout_s = int(settings_data.get("approval_timeout_s") or 1800)

    is_chat = (project.kind or "repo") == "chat"
    tool_defs = all_tool_definitions()
    ctx: Optional[ToolContext] = None

    # fresh start bookkeeping
    if not resume:
        await _emit(task_id, "task_started", {"model": model, "kind": project.kind or "repo"})
        await _update_task(task_id, status="running", model=model)

    # sandbox: repo tasks always get one; chat tasks create one lazily on first sandbox use
    sbx = None
    reset_note = None
    branch = None
    sandbox_id = ""
    if is_chat:
        reuse_id = task.sandbox_id if (resume and task.sandbox_id) else None
        if not reuse_id:
            reuse_id = await _latest_thread_sandbox_id(project.id, task_id)
        if reuse_id:
            try:
                sbx = await sbx_mod.connect(reuse_id)
            except sbx_mod.SandboxError:
                sbx = None
    else:
        try:
            sbx, reset_note = await _setup_sandbox(task, project, resume)
        except sbx_mod.SandboxUnavailable as exc:
            await _finish_failed(task_id, str(exc))
            return
        except sbx_mod.SandboxError as exc:
            await _finish_failed(task_id, f"sandbox error: {exc}")
            return

    try:
        if sbx is not None:
            sandbox_id = getattr(sbx, "sandbox_id", None) or ""
            await sbx_mod.keep_alive(sbx)
            await _update_task(task_id, sandbox_id=sandbox_id)
        if is_chat:
            short_id = ""
        else:
            short_id = task_id.replace("-", "")[:8]
            await _ensure_branch(sbx, short_id)
            branch = f"agent/{short_id}"

        # conversation
        messages = await _load_messages(task_id)
        if not messages:
            if is_chat:
                history = await _load_thread_history(project.id, task_id)
                messages = [{"role": "system", "content": GENERAL_SYSTEM_PROMPT}, *history]
                messages.append({"role": "user", "content": task.prompt})
                await _save_message(task_id, messages[0])
                await _save_message(task_id, messages[-1])
            else:
                user_prompt = (
                    f"Repository: {project.repo_url} (default branch: {project.default_branch}), "
                    f"cloned at {sbx_mod.REPO_DIR} in your sandbox. Your working branch: {branch}.\n\n"
                    f"Task:\n{task.prompt}"
                )
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
                await _save_message(task_id, messages[0])
                await _save_message(task_id, messages[1])
        elif reset_note and sbx is not None:
            note = {"role": "user", "content": f"[SYSTEM NOTE] {reset_note}"}
            messages.append(note)
            await _save_message(task_id, note)
            await _ensure_branch(sbx, short_id)

        ctx = ToolContext(
            task_id=task_id,
            sandbox=sbx,
            settings_data=settings_data,
            command_timeout_s=command_timeout_s,
            default_branch=project.default_branch,
            kind=project.kind or "repo",
            repo_dir=(sbx_mod.WORK_DIR if is_chat else sbx_mod.REPO_DIR),
            extras={},
        )

        # resume: complete dangling tool calls (approval waits / interrupted dispatches)
        dangling = _dangling_tool_calls(messages)
        if dangling:
            await _handle_dangling(task_id, worker, ctx, messages, dangling, stop_event, approval_timeout_s)
            if stop_event.is_set():
                await _finish_stopped(task_id, "stopped via API")
                return

        if not any_configured():
            await _finish_failed(task_id, "no LLM provider configured (AGENTROUTER_API_KEY / BAI_API_KEY)")
            return

        # main iterations
        current = await _get_task(task_id)
        iterations = current.iterations if current else 0
        tokens_used = current.tokens_used if current else 0
        finished = False
        result_summary: Optional[str] = None

        while not finished:
            if stop_event.is_set():
                await _finish_stopped(task_id, "stopped via API")
                return
            if iterations >= max_iterations:
                await _finish_failed(task_id, f"iteration limit reached ({max_iterations}) without finishing")
                return
            if tokens_used >= token_budget:
                await _finish_failed(task_id, f"token budget exhausted ({tokens_used}/{token_budget})")
                return

            # extend sandbox lifetime before doing more work (sandbox may be lazily created in a tool)
            if ctx is not None and ctx.sandbox is not None:
                await sbx_mod.keep_alive(ctx.sandbox)
                sandbox_id = getattr(ctx.sandbox, "sandbox_id", None) or sandbox_id

            # steer injection
            for note_text in worker.drain_steer(task_id):
                note = {"role": "user", "content": f"[USER STEERING MESSAGE] {note_text}"}
                messages.append(note)
                await _save_message(task_id, note)

            # LLM call (streamed so the user sees the response as it is written)
            try:
                assistant, model = await _llm_stream(task_id, messages, tool_defs, model)
            except LLMNotConfigured:
                await _finish_failed(task_id, "AGENTROUTER_BASE_URL/AGENTROUTER_API_KEY not configured")
                return
            except LLMError as exc:
                await _emit(task_id, "error", {"message": f"LLM error: {exc}"})
                await _finish_failed(task_id, f"LLM error: {exc}")
                return

            usage = assistant.pop("_usage", {})
            tokens_used += int(usage.get("total_tokens") or 0)
            iterations += 1

            content = assistant.get("content") or ""
            tool_calls = assistant.get("tool_calls") or []

            # persist assistant message
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            await _save_message(task_id, assistant_msg)

            if content.strip():
                await _emit(task_id, "agent_message", {"content": content})

            if not tool_calls:
                if is_chat and content.strip():
                    # The model answered directly — finish now instead of
                    # burning another round-trip on a "call finish" nudge.
                    finished = True
                    result_summary = content.strip()
                    break
                if is_chat:
                    nudge_text = (
                        "If you have fully answered the user, call the finish tool now with your answer "
                        "in result_summary. Otherwise, continue working with tools."
                    )
                else:
                    nudge_text = (
                        "Continue with the task. When it is fully complete (changes committed, pushed, "
                        "PR opened), call the finish tool with a result summary."
                    )
                nudge = {"role": "user", "content": nudge_text}
                messages.append(nudge)
                await _save_message(task_id, nudge)
                await _checkpoint(task_id, iterations, tokens_used, sandbox_id, sbx)
                messages = await _maybe_compact(task_id, messages, model)
                continue

            # execute tool calls
            for tc in tool_calls:
                if stop_event.is_set():
                    break
                outcome = await _execute_tool_call(
                    task_id, worker, ctx, messages, tc, stop_event, approval_timeout_s
                )
                if outcome == "finished":
                    finished = True
                    result_summary = ctx.extras.get("result_summary")
                    break
                if outcome == "stopped":
                    break

            await _checkpoint(task_id, iterations, tokens_used, sandbox_id, sbx)
            messages = await _maybe_compact(task_id, messages, model)

            if stop_event.is_set():
                await _finish_stopped(task_id, "stopped via API")
                return

        if finished:
            pr_url = ctx.extras.get("pr_url")
            pr_number = ctx.extras.get("pr_number")
            final_branch = ctx.extras.get("branch") or branch
            await _finish_done(task_id, result_summary or "", pr_url, pr_number, final_branch, iterations, tokens_used)
    except asyncio.CancelledError:
        raise
    except sbx_mod.SandboxError as exc:
        await _finish_failed(task_id, f"sandbox error: {exc}")
    except Exception as exc:
        log.exception("unhandled error in agent loop for %s", task_id)
        await _finish_failed(task_id, f"internal error: {type(exc).__name__}: {exc}")
    finally:
        active_sbx = ctx.sandbox if ctx is not None else sbx
        await _release_sandbox_after_terminal(task_id, active_sbx, is_chat)


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


# ── tool call execution ──────────────────────────────────


async def _execute_tool_call(
    task_id: str, worker, ctx: ToolContext, messages: List[Dict[str, Any]],
    tc: dict, stop_event: asyncio.Event, approval_timeout_s: int,
) -> str:
    """Returns 'continue' | 'finished' | 'stopped'."""
    tool = tc.get("function", {}).get("name", "")
    raw_args = tc.get("function", {}).get("arguments", "{}")
    try:
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            args = {"_value": args}
    except Exception:
        args = None

    tool_call_id = tc.get("id") or new_id()

    if args is None:
        await _emit(task_id, "tool_call", {"tool": tool, "args": {"_raw": raw_args[:500]}})
        summary = f"invalid tool arguments (not valid JSON): {raw_args[:300]}"
        await _append_tool_result(task_id, messages, tool_call_id, summary)
        await _emit(task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary[:8000], "truncated": False})
        return "continue"

    safe_args = _sanitize_args(args)
    await _emit(task_id, "tool_call", {"tool": tool, "args": safe_args})

    level = evaluate(tool, args, ctx.settings_data)

    if level == "deny":
        summary = f"Permission denied by policy: tool '{tool}' is not allowed."
        await _append_tool_result(task_id, messages, tool_call_id, summary)
        await _emit(task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary, "truncated": False})
        return "continue"

    if level == "ask":
        approval_id = await _create_approval(task_id, tool, args, tool_call_id)
        await _update_task(task_id, status="awaiting_approval")
        approval = await _get_approval(approval_id)
        await _emit(
            task_id,
            "approval_request",
            {
                "approval_id": approval_id,
                "kind": "tool",
                "description": approval.description if approval else "",
                "payload": safe_args,
            },
        )
        asyncio.create_task(push.push_approval_request(task_id, approval_id, approval.description if approval else ""))

        decision = await _wait_for_approval(worker, approval_id, approval_timeout_s, stop_event)

        if decision == "stopped":
            await _expire_pending_approvals(task_id)
            return "stopped"
        if decision == "timeout":
            await _set_approval_status(approval_id, "expired")
            await _emit(task_id, "approval_decision", {"approval_id": approval_id, "decision": "timeout"})
            summary = (
                f"Approval request timed out after {approval_timeout_s}s and was treated as DENIED "
                f"for tool '{tool}'. Adapt: find another way that does not require this action."
            )
            await _update_task(task_id, status="running")
            await _append_tool_result(task_id, messages, tool_call_id, summary)
            await _emit(task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary, "truncated": False})
            return "continue"

        decision_str = "approve" if decision == "approve" else "deny"
        await _emit(task_id, "approval_decision", {"approval_id": approval_id, "decision": decision_str})
        if decision == "deny":
            summary = f"The user DENIED this action ({_approval_description(tool, args)}). Do not retry it; adapt."
            await _update_task(task_id, status="running")
            await _append_tool_result(task_id, messages, tool_call_id, summary)
            await _emit(task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary, "truncated": False})
            return "continue"

        await _update_task(task_id, status="running")
        # fall through to dispatch

    ok, summary, data = await dispatch(ctx, tool, args)
    if data:
        if data.get("pr_url"):
            ctx.extras["pr_url"] = data["pr_url"]
        if data.get("pr_number"):
            ctx.extras["pr_number"] = data["pr_number"]
        if data.get("result_summary"):
            ctx.extras["result_summary"] = data["result_summary"]

    truncated = len(summary) > 8000
    await _emit(
        task_id,
        "tool_result",
        {"tool": tool, "ok": bool(ok), "summary": summary[:8000], "truncated": truncated},
    )
    await _append_tool_result(task_id, messages, tool_call_id, summary)

    if tool == "finish":
        ctx.extras["result_summary"] = str(args.get("result_summary", "") or summary)
        return "finished"
    return "continue"


def _sanitize_args(args: dict) -> dict:
    from ..sandbox import mask_secrets

    try:
        return json.loads(mask_secrets(json.dumps(args, default=str)))
    except Exception:
        return {"_error": "unserializable args"}


async def _append_tool_result(task_id: str, messages: List[Dict[str, Any]], tool_call_id: str, summary: str) -> None:
    msg = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": "tool",
        "content": (summary or "")[:8000],
    }
    messages.append(msg)
    await _save_message(task_id, msg)


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


# ── dangling tool call handling on resume ────────────────


async def _handle_dangling(
    task_id: str, worker, ctx: ToolContext, messages: List[Dict[str, Any]],
    dangling: List[dict], stop_event: asyncio.Event, approval_timeout_s: int,
) -> None:
    for tc in dangling:
        tool = tc.get("function", {}).get("name", "")
        tool_call_id = tc.get("id") or new_id()
        try:
            args = json.loads(tc.get("function", {}).get("arguments") or "{}")
        except Exception:
            args = {}
        approval = await _pending_approval_for(task_id, tool_call_id)
        if approval is not None:
            # resume the interrupted approval wait
            await _emit(task_id, "approval_request", {
                "approval_id": approval.id,
                "kind": approval.kind,
                "description": approval.description,
                "payload": _sanitize_args((approval.payload or {}).get("args", {})),
            })
            decision = await _wait_for_approval(worker, approval.id, approval_timeout_s, stop_event)
            if decision == "stopped":
                await _expire_pending_approvals(task_id)
                return
            if decision == "timeout":
                await _set_approval_status(approval.id, "expired")
                await _emit(task_id, "approval_decision", {"approval_id": approval.id, "decision": "timeout"})
                summary = "Approval timed out (treated as denied)."
            else:
                decision_str = "approve" if decision == "approve" else "deny"
                await _emit(task_id, "approval_decision", {"approval_id": approval.id, "decision": decision_str})
                if decision == "deny":
                    summary = "The user DENIED this action. Do not retry; adapt."
                else:
                    ok, summary, _data = await dispatch(ctx, tool, args)
                    await _emit(
                        task_id,
                        "tool_result",
                        {"tool": tool, "ok": bool(ok), "summary": summary[:8000], "truncated": len(summary) > 8000},
                    )
                    await _append_tool_result(task_id, messages, tool_call_id, summary)
                    continue
            await _update_task(task_id, status="running")
            await _append_tool_result(task_id, messages, tool_call_id, summary)
            await _emit(
                task_id,
                "tool_result",
                {"tool": tool, "ok": False, "summary": summary[:8000], "truncated": False},
            )
        else:
            summary = (
                "[SYSTEM NOTE] This tool call was interrupted by a backend restart. It produced no result. "
                "Re-do the work if needed."
            )
            await _append_tool_result(task_id, messages, tool_call_id, summary)
            await _emit(
                task_id,
                "tool_result",
                {"tool": tool, "ok": False, "summary": summary, "truncated": False},
            )


# ── checkpoint & terminal states ─────────────────────────


async def _checkpoint(task_id: str, iterations: int, tokens_used: int, sandbox_id: str, sbx) -> None:
    if sbx is None:
        await _update_task(task_id, iterations=iterations, tokens_used=tokens_used, sandbox_id=sandbox_id)
        await _emit(task_id, "checkpoint", {"iteration": iterations, "sandbox_alive": None})
        return
    alive = True
    try:
        result = await sbx.commands.run("true", timeout=15)
        alive = result.exit_code == 0
    except Exception:
        alive = False
    await _update_task(task_id, iterations=iterations, tokens_used=tokens_used, sandbox_id=sandbox_id)
    await _emit(task_id, "checkpoint", {"iteration": iterations, "sandbox_alive": alive})


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
