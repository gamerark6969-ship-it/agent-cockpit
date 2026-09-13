"""Pydantic AI–powered agent loop.

Enabled with ``AGENT_FRAMEWORK=pydantic_ai`` (the default). It reuses all the
surrounding machinery from the legacy loop — ``tools.py`` (dispatch + approval
events), permissions, persistence, sandbox setup — and swaps out the one thing
that kept breaking: the hand-rolled LLM turn / provider-failover code.

Why this is more robust than the hand-rolled loop:
  * ``FallbackModel`` owns provider failover (transient 429/503/400 -> next model).
  * The native Gemini provider keeps ``thought_signature`` intact on its own.
  * Tool schemas come from ``tools.py`` and become typed pydantic arg models.
  * The full conversation is persisted in pydantic-ai's own message format
    (``Task.pa_history``), so a crashed/restarted task resumes its tool trace.

The legacy loop (``loop.py``) stays importable; flip the flag to roll back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, CancellationToken, RunContext
from pydantic_ai.exceptions import ModelAPIError, RunCancelled, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    UserPromptPart,
)
from pydantic_ai.output import ToolOutput

from .. import push
from .. import sandbox as sbx_mod
from ..db import SessionLocal, get_settings_data
from ..models import Project, new_id
from ..permissions import evaluate
from .loop import (
    _approval_description,
    _create_approval,
    _emit,
    _ensure_branch,
    _expire_pending_approvals,
    _finish_done,
    _finish_failed,
    _finish_stopped,
    _get_task,
    _latest_thread_sandbox_id,
    _release_sandbox_after_terminal,
    _sanitize_args,
    _set_approval_status,
    _setup_sandbox,
    _update_task,
    _wait_for_approval,
)
from .models import PA_DEFAULT_MODEL, build_model_chain
from .prompts import GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT
from .tools import ToolContext, all_tool_definitions, dispatch

log = logging.getLogger("agent")

TERMINAL = ("done", "failed", "stopped")
DELTA_FLUSH_CHARS = 90
DELTA_FLUSH_SECONDS = 0.25
MAX_TOOL_SUMMARY = 8000


# ── pydantic-ai plumbing ─────────────────────────────────


class _StopRun(Exception):
    """Raised inside a tool when the user stops the task mid-approval."""


@dataclass
class RunDeps:
    task_id: str
    worker: Any
    ctx: ToolContext
    is_chat: bool
    approval_timeout_s: int
    stop_event: asyncio.Event


class FinishResult(BaseModel):
    result_summary: str = Field(
        description="Concise final answer, or summary of what was accomplished."
    )


_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_args_model(name: str, schema: Dict[str, Any]) -> type[BaseModel]:
    """Turn an OpenAI-style JSON schema into a typed pydantic model."""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: Dict[str, Any] = {}
    for pname, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        py_t = _TYPE_MAP.get(str(spec.get("type") or "string"), str)
        desc = spec.get("description")
        if pname in required:
            fields[pname] = (py_t, Field(..., description=desc))
        else:
            fields[pname] = (Optional[py_t], Field(None, description=desc))
    return create_model(f"{name}_args", **fields)


async def _execute_tool(rc: RunContext[RunDeps], args: BaseModel) -> str:
    """The single entry point every generated tool funnels into."""
    deps = rc.deps
    tool = rc.tool_name
    tool_call_id = rc.tool_call_id or new_id()
    args_dict = {k: v for k, v in args.model_dump().items() if v is not None}

    await _emit(deps.task_id, "tool_call", {"tool": tool, "args": _sanitize_args(args_dict)})

    level = evaluate(tool, args_dict, deps.ctx.settings_data)
    if level == "deny":
        summary = f"Permission denied by policy: tool '{tool}' is not allowed."
        await _emit(deps.task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary, "truncated": False})
        return summary

    if level == "ask":
        approval_id = await _create_approval(deps.task_id, tool, args_dict, tool_call_id)
        await _update_task(deps.task_id, status="awaiting_approval")
        description = _approval_description(tool, args_dict)
        await _emit(
            deps.task_id,
            "approval_request",
            {
                "approval_id": approval_id,
                "kind": "tool",
                "description": description,
                "payload": _sanitize_args(args_dict),
            },
        )
        asyncio.create_task(push.push_approval_request(deps.task_id, approval_id, description))

        decision = await _wait_for_approval(
            deps.worker, approval_id, deps.approval_timeout_s, deps.stop_event
        )
        if decision == "stopped":
            await _expire_pending_approvals(deps.task_id)
            raise _StopRun()
        if decision == "timeout":
            await _set_approval_status(approval_id, "expired")
            await _emit(deps.task_id, "approval_decision", {"approval_id": approval_id, "decision": "timeout"})
            await _update_task(deps.task_id, status="running")
            summary = (
                f"Approval request timed out after {deps.approval_timeout_s}s and was treated as DENIED "
                f"for tool '{tool}'. Adapt: find another way that does not require this action."
            )
            await _emit(deps.task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary, "truncated": False})
            return summary

        decision_str = "approve" if decision == "approve" else "deny"
        await _emit(deps.task_id, "approval_decision", {"approval_id": approval_id, "decision": decision_str})
        if decision == "deny":
            summary = f"The user DENIED this action ({description}). Do not retry it; adapt."
            await _update_task(deps.task_id, status="running")
            await _emit(deps.task_id, "tool_result", {"tool": tool, "ok": False, "summary": summary, "truncated": False})
            return summary
        await _update_task(deps.task_id, status="running")

    ok, summary, data = await dispatch(deps.ctx, tool, args_dict)
    if data:
        if data.get("pr_url"):
            deps.ctx.extras["pr_url"] = data["pr_url"]
        if data.get("pr_number"):
            deps.ctx.extras["pr_number"] = data["pr_number"]
        if data.get("result_summary"):
            deps.ctx.extras["result_summary"] = data["result_summary"]

    summary = summary or ""
    await _emit(
        deps.task_id,
        "tool_result",
        {"tool": tool, "ok": bool(ok), "summary": summary[:MAX_TOOL_SUMMARY], "truncated": len(summary) > MAX_TOOL_SUMMARY},
    )
    return summary[:MAX_TOOL_SUMMARY] or "(no output)"


def _make_tool(defn: Dict[str, Any]):
    """Compile one OpenAI tool definition into a pydantic-ai tool function."""
    name = defn["function"]["name"]
    schema = defn["function"].get("parameters") or {"type": "object", "properties": {}}
    description = defn["function"].get("description") or ""
    args_model = _build_args_model(name, schema)
    ns: Dict[str, Any] = {"_ARGS": args_model, "_exec": _execute_tool, "RunContext": RunContext}
    src = (
        "async def _tool(ctx: RunContext, args: _ARGS) -> str:\n"
        "    return await _exec(ctx, args)\n"
    )
    exec(src, ns)  # noqa: S102 - fixed, generated source
    fn = ns["_tool"]
    fn.__name__ = name
    fn.__doc__ = description
    return fn


class _DeltaStream:
    """Batches streamed text chunks into throttled ``agent_delta`` events."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._buf: List[str] = []
        self._n = 0
        self._last = time.monotonic()

    async def push(self, piece: str) -> None:
        if not piece:
            return
        self._buf.append(piece)
        self._n += len(piece)
        if self._n >= DELTA_FLUSH_CHARS or (time.monotonic() - self._last) >= DELTA_FLUSH_SECONDS:
            await self.flush()

    async def flush(self) -> None:
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf = []
        self._n = 0
        self._last = time.monotonic()
        await _emit(self.task_id, "agent_delta", {"content": text})


async def _prior_turns(project_id: str, exclude_task_id: str, max_turns: int = 40) -> List[ModelMessage]:
    """Prior completed turns in a chat thread, as plain user/assistant text."""
    from sqlalchemy import select

    from ..models import Task

    async with SessionLocal() as session:
        tasks = (
            await session.execute(
                select(Task)
                .where(Task.project_id == project_id, Task.id != exclude_task_id, Task.status == "done")
                .order_by(Task.created_at)
            )
        ).scalars().all()
    history: List[ModelMessage] = []
    for t in tasks:
        prompt = (t.prompt or "").strip()
        if prompt:
            history.append(ModelRequest(parts=[UserPromptPart(content=prompt[:4000])]))
        final = (t.result_summary or "").strip()
        if final:
            history.append(ModelResponse(parts=[TextPart(content=final[:4000])]))
    return history[-max_turns:]


def _response_text(resp: ModelResponse) -> str:
    return "\n".join(p.content for p in resp.parts if isinstance(p, TextPart) and p.content.strip())


def _norm_model(name: str) -> str:
    for prefix in ("models/", "b.ai/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


async def _sync_answering_model(task_id: str, resp: ModelResponse, current: str) -> str:
    actual = getattr(resp, "model_name", None)
    if actual and _norm_model(actual) != _norm_model(current):
        await _emit(task_id, "model_switch", {"from": current, "to": actual})
        await _update_task(task_id, model=actual)
        return actual
    return current


# ── the loop ─────────────────────────────────────────────


async def run_agent_loop(task_id: str, worker) -> None:
    task = await _get_task(task_id)
    if task is None or task.status in TERMINAL:
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

    is_chat = (project.kind or "repo") == "chat"
    resume = task.status in ("running", "awaiting_approval")
    model_name = str(task.model or settings_data.get("default_model") or PA_DEFAULT_MODEL)
    max_iterations = int(settings_data.get("max_iterations") or 50)
    token_budget = int(settings_data.get("token_budget") or 2_000_000)
    command_timeout_s = int(settings_data.get("command_timeout_s") or 600)
    approval_timeout_s = int(settings_data.get("approval_timeout_s") or 1800)

    model = build_model_chain(model_name)
    if model is None:
        await _finish_failed(task_id, f"no LLM provider configured for model '{model_name}'")
        return

    if not resume:
        await _emit(task_id, "task_started", {"model": model_name, "kind": project.kind or "repo"})
        await _update_task(task_id, status="running", model=model_name)

    # ── sandbox: repo tasks always get one; chat tasks reuse/create lazily ──
    sbx = None
    reset_note = None
    branch = None
    sandbox_id = task.sandbox_id or ""
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

    ctx: Optional[ToolContext] = None
    deltas = _DeltaStream(task_id)
    try:
        if sbx is not None:
            sandbox_id = getattr(sbx, "sandbox_id", None) or sandbox_id
            await sbx_mod.keep_alive(sbx)
            await _update_task(task_id, sandbox_id=sandbox_id)

        short_id = ""
        if not is_chat:
            short_id = task_id.replace("-", "")[:8]
            await _ensure_branch(sbx, short_id)
            branch = f"agent/{short_id}"

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

        # ── conversation: restore pa_history, else start from thread context ──
        history: List[ModelMessage] = []
        if resume and task.pa_history:
            try:
                history = ModelMessagesTypeAdapter.validate_json(json.dumps(task.pa_history))
            except Exception:
                log.warning("could not restore pa_history for %s; starting fresh", task_id, exc_info=True)
                history = []
        if not history and is_chat:
            history = await _prior_turns(project.id, task_id)

        if is_chat:
            prompt = task.prompt
        else:
            prompt = (
                f"Repository: {project.repo_url} (default branch: {project.default_branch}), "
                f"cloned at {sbx_mod.REPO_DIR} in your sandbox. Your working branch: {branch}.\n\n"
                f"Task:\n{task.prompt}"
            )
        if resume and reset_note and sbx is not None:
            prompt = f"[SYSTEM NOTE] {reset_note}\n\n{prompt}"
            await _ensure_branch(sbx, short_id)

        # ── build the agent ──
        tool_defs = [d for d in all_tool_definitions() if d["function"]["name"] != "finish"]
        agent = Agent(
            model,
            deps_type=RunDeps,
            system_prompt=GENERAL_SYSTEM_PROMPT if is_chat else SYSTEM_PROMPT,
            output_type=ToolOutput(
                FinishResult,
                name="finish",
                description="Call when the task is fully complete; put the final answer/summary in result_summary.",
            ),
            tools=[_make_tool(d) for d in tool_defs],
            end_strategy="graceful",
        )

        deps = RunDeps(
            task_id=task_id,
            worker=worker,
            ctx=ctx,
            is_chat=is_chat,
            approval_timeout_s=approval_timeout_s,
            stop_event=stop_event,
        )

        # bridge the worker stop_event to pydantic-ai's cancellation token
        token = CancellationToken()

        async def _watch_stop() -> None:
            await stop_event.wait()
            token.cancel()

        watcher = asyncio.create_task(_watch_stop())

        iterations = task.iterations if resume else 0
        tokens_used = task.tokens_used if resume else 0
        current_model = model_name
        abort_reason: Optional[str] = None
        result_summary: Optional[str] = None
        final_messages: List[ModelMessage] = list(history)

        try:
            async with agent.iter(prompt, message_history=history, deps=deps, cancellation_token=token) as run:
                async for node in run:
                    if stop_event.is_set():
                        abort_reason = "stopped"
                        break

                    if Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as stream:
                            async for ev in stream:
                                if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                                    await deltas.push(ev.part.content)
                                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                                    await deltas.push(ev.delta.content_delta or "")
                        await deltas.flush()

                    elif Agent.is_call_tools_node(node):
                        resp = node.model_response
                        iterations += 1
                        usage = getattr(resp, "usage", None)
                        if usage is not None:
                            tokens_used += int(getattr(usage, "input_tokens", 0) or 0) + int(
                                getattr(usage, "output_tokens", 0) or 0
                            )
                        current_model = await _sync_answering_model(task_id, resp, current_model)
                        text = _response_text(resp)
                        if text:
                            await _emit(task_id, "agent_message", {"content": text})
                        if ctx.sandbox is not None:
                            await sbx_mod.keep_alive(ctx.sandbox)
                        await _update_task(task_id, iterations=iterations, tokens_used=tokens_used)

                        if iterations >= max_iterations:
                            abort_reason = f"iteration limit reached ({max_iterations}) without finishing"
                            break
                        if tokens_used >= token_budget:
                            abort_reason = f"token budget exhausted ({tokens_used}/{token_budget})"
                            break

                if abort_reason is None:
                    output = run.result.output
                    result_summary = output.result_summary if isinstance(output, FinishResult) else str(output or "")
                    final_messages = list(run.result.all_messages())
        except RunCancelled:
            abort_reason = "stopped"
        except _StopRun:
            abort_reason = "stopped"
        except ModelAPIError as exc:
            await _emit(task_id, "error", {"message": f"LLM error: {exc}"})
            abort_reason = f"LLM error: {exc}"
        except UnexpectedModelBehavior as exc:
            abort_reason = f"model behavior error: {exc}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("unhandled error in pydantic-ai loop for %s", task_id)
            abort_reason = f"internal error: {type(exc).__name__}: {exc}"
        finally:
            watcher.cancel()
            try:
                await deltas.flush()
            except Exception:
                log.debug("final delta flush failed for %s", task_id, exc_info=True)

        # persist the conversation so a restart can resume it
        if final_messages:
            try:
                serialized = json.loads(ModelMessagesTypeAdapter.dump_json(final_messages))
                await _update_task(task_id, pa_history=serialized)
            except Exception:
                log.warning("failed to persist pa_history for %s", task_id, exc_info=True)

        if abort_reason == "stopped":
            await _finish_stopped(task_id, "stopped via API")
            return
        if abort_reason is not None:
            await _finish_failed(task_id, abort_reason)
            return
        if stop_event.is_set():
            await _finish_stopped(task_id, "stopped via API")
            return

        await _finish_done(
            task_id,
            result_summary or "",
            ctx.extras.get("pr_url"),
            ctx.extras.get("pr_number"),
            ctx.extras.get("branch") or branch,
            iterations,
            tokens_used,
        )
    except asyncio.CancelledError:
        raise
    except sbx_mod.SandboxError as exc:
        await _finish_failed(task_id, f"sandbox error: {exc}")
    except Exception as exc:
        log.exception("unhandled error in pydantic-ai loop for %s", task_id)
        await _finish_failed(task_id, f"internal error: {type(exc).__name__}: {exc}")
    finally:
        active_sbx = ctx.sandbox if ctx is not None else sbx
        await _release_sandbox_after_terminal(task_id, active_sbx, is_chat)
