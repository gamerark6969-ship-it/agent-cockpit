import asyncio
import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import connectors as connectors_mod
from .. import sandbox as sbx_mod
from ..db import SessionLocal, get_settings_data, save_settings
from ..events import subscribe, unsubscribe
from ..agent.models import (
    PA_DEFAULT_MODEL as DEFAULT_TASK_MODEL,
    any_configured,
    list_pa_models,
)
from ..models import (
    Approval,
    Artifact,
    Event,
    Message,
    Project,
    PushSubscription,
    Task,
    utcnow,
)
from ..schemas import (
    ApprovalDecision,
    ApprovalOut,
    ConnectorCreate,
    ConnectorOut,
    ConnectorUpdate,
    DiffFile,
    DiffOut,
    EventOut,
    ModelsOut,
    ProjectCreate,
    ProjectOut,
    PushSubscribe,
    PushUnsubscribe,
    ScreenshotOut,
    SettingsIn,
    SettingsOut,
    SteerRequest,
    TaskCreate,
    TaskOut,
)
from ..security import require_token

log = logging.getLogger("api")

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(require_token)])

TERMINAL_STATUSES = {"done", "failed", "stopped"}
ACTIVE_STATUSES = {"queued", "running", "awaiting_approval"}


def get_worker():
    from ..main import worker

    return worker


async def _emit_event(task_id: str, type_: str, payload: dict) -> None:
    from ..events import append_event

    async with SessionLocal() as session:
        await append_event(session, task_id, type_, payload)


# ── health ───────────────────────────────────────────────


@public_router.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ── projects ─────────────────────────────────────────────


def _project_out(p: Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        kind=p.kind or "repo",
        repo_url=p.repo_url,
        default_branch=p.default_branch,
        settings=p.settings or {},
        created_at=p.created_at,
    )


@router.get("/api/projects", response_model=list[ProjectOut])
async def list_projects():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Project).order_by(Project.created_at))).scalars().all()
    return [_project_out(p) for p in rows]


@router.post("/api/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate):
    repo_url = (body.repo_url or "").strip() or None
    kind = body.kind
    if kind == "repo":
        if not repo_url or not repo_url.startswith("https://"):
            raise HTTPException(status_code=422, detail="repo_url must be an https:// URL")
    else:
        repo_url = None
    if body.name:
        name = body.name
    elif repo_url:
        name = repo_url.rstrip("/").split("/")[-1] or "project"
    else:
        name = "New chat"
    async with SessionLocal() as session:
        project = Project(
            name=name,
            kind=kind,
            repo_url=repo_url,
            default_branch=body.default_branch or "main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
    return _project_out(project)


@router.get("/api/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str):
    async with SessionLocal() as session:
        project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _project_out(project)


@router.delete("/api/projects/{project_id}", status_code=204)
async def delete_project(project_id: str):
    worker = get_worker()
    async with SessionLocal() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        tasks = (
            await session.execute(select(Task).where(Task.project_id == project_id))
        ).scalars().all()
        for task in tasks:
            if task.status in ACTIVE_STATUSES:
                worker.request_stop(task.id)
                if not worker.is_active(task.id):
                    task.status = "stopped"
                    task.updated_at = utcnow()
                    await session.flush()
                    await _emit_event(task.id, "task_stopped", {"reason": "project deleted"})
        await session.delete(project)
        await session.commit()
    return Response(status_code=204)


# ── tasks ────────────────────────────────────────────────


def _task_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        project_id=t.project_id,
        prompt=t.prompt,
        status=t.status,
        model=t.model,
        iterations=t.iterations,
        tokens_used=t.tokens_used,
        error=t.error,
        result_summary=t.result_summary,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.post("/api/projects/{project_id}/tasks", response_model=TaskOut, status_code=201)
async def create_task(project_id: str, body: TaskCreate):
    worker = get_worker()
    async with SessionLocal() as session:
        settings_data = await get_settings_data(session)
    async with SessionLocal() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        task = Task(
            project_id=project_id,
            prompt=body.prompt,
            status="queued",
            model=body.model or str(settings_data.get("default_model") or DEFAULT_TASK_MODEL),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
    worker.enqueue(task.id)
    return _task_out(task)


@router.get("/api/projects/{project_id}/tasks", response_model=list[TaskOut])
async def list_tasks(project_id: str):
    async with SessionLocal() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        rows = (
            await session.execute(
                select(Task).where(Task.project_id == project_id).order_by(Task.created_at)
            )
        ).scalars().all()
    return [_task_out(t) for t in rows]


@router.get("/api/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: str):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_out(task)


@router.post("/api/tasks/{task_id}/stop", status_code=202)
async def stop_task(task_id: str):
    worker = get_worker()
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail=f"task already {task.status}")
        if task.status == "queued" and not worker.is_active(task_id):
            task.status = "stopped"
            task.updated_at = utcnow()
            await session.commit()
    if task.status == "stopped":
        await _emit_event(task_id, "task_stopped", {"reason": "stopped via API"})
        return Response(status_code=202)
    worker.request_stop(task_id)
    return Response(status_code=202)


@router.post("/api/tasks/{task_id}/steer", status_code=202)
async def steer_task(task_id: str, body: SteerRequest):
    worker = get_worker()
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status != "running":
            raise HTTPException(status_code=409, detail=f"cannot steer a task in status '{task.status}' (must be running)")
    if not worker.is_active(task_id):
        raise HTTPException(status_code=409, detail="task is not active in the worker")
    worker.steer(task_id, body.message)
    return Response(status_code=202)


@router.get("/api/tasks/{task_id}/events", response_model=list[EventOut])
async def list_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        rows = (
            await session.execute(
                select(Event)
                .where(Event.task_id == task_id, Event.seq > after)
                .order_by(Event.seq)
                .limit(limit)
            )
        ).scalars().all()
    return [
        EventOut(seq=e.seq, task_id=e.task_id, type=e.type, payload=e.payload or {}, created_at=e.created_at)
        for e in rows
    ]


@router.get("/api/tasks/{task_id}/stream")
async def stream_events(task_id: str, after: int = Query(default=0, ge=0)):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")

    async def gen():
        last = after
        flag = await subscribe(task_id)
        try:
            while True:
                flag.clear()
                # drain history/new events first
                async with SessionLocal() as session:
                    rows = (
                        await session.execute(
                            select(Event)
                            .where(Event.task_id == task_id, Event.seq > last)
                            .order_by(Event.seq)
                        )
                    ).scalars().all()
                    task_row = await session.get(Task, task_id)
                for e in rows:
                    data = {
                        "seq": e.seq,
                        "task_id": e.task_id,
                        "type": e.type,
                        "payload": e.payload or {},
                        "created_at": e.created_at.isoformat(),
                    }
                    last = e.seq
                    yield f"event: event\nid: {e.seq}\ndata: {json.dumps(data, default=str)}\n\n"
                if task_row is not None and task_row.status in TERMINAL_STATUSES and not rows:
                    yield "event: end\ndata: {}\n\n"
                    return
                # wait for broadcast or ping timeout
                try:
                    await asyncio.wait_for(flag.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            await unsubscribe(task_id, flag)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/tasks/{task_id}/diff", response_model=DiffOut)
async def get_diff(task_id: str):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        project = await session.get(Project, task.project_id)
        base = project.default_branch if project else "main"
        sandbox_id = task.sandbox_id
    if not sandbox_id or not sbx_mod.settings.E2B_API_KEY:
        return DiffOut(summary="", files=[])
    try:
        sbx = await sbx_mod.connect(sandbox_id)
    except sbx_mod.SandboxError:
        return DiffOut(summary="", files=[])
    stat = await sbx_mod.run_command(
        sbx, f"cd {sbx_mod.REPO_DIR} && git diff --stat origin/{base}...HEAD 2>/dev/null || git diff --stat {base}...HEAD 2>/dev/null", timeout=60
    )
    porcelain = await sbx_mod.run_command(
        sbx, f"cd {sbx_mod.REPO_DIR} && git status --porcelain", timeout=60
    )
    files: list[DiffFile] = []
    import re as _re

    for line in (stat.get("output") or "").splitlines():
        m = _re.match(r"\s*(.+?)\s+\|\s+(\d+)\s+([+-]+)", line)
        if m:
            path = m.group(1).strip()
            changes = m.group(3)
            files.append(
                DiffFile(
                    path=path,
                    status="modified",
                    additions=changes.count("+"),
                    deletions=changes.count("-"),
                )
            )
    if porcelain.get("exit_code") == 0:
        for line in (porcelain.get("output") or "").splitlines():
            if not line.strip():
                continue
            code = line[:2].strip()
            path = line[3:].strip().strip('"')
            norm = path.split(" -> ")[-1]
            if any(f.path == norm for f in files):
                continue
            status = {"M": "modified", "A": "added", "D": "deleted", "??": "untracked"}.get(code, "changed")
            files.append(DiffFile(path=norm, status=status, additions=0, deletions=0))
    summary = ""
    for line in reversed((stat.get("output") or "").splitlines()):
        if "files changed" in line or "file changed" in line:
            summary = line.strip()
            break
    return DiffOut(summary=summary, files=files)


@router.get("/api/tasks/{task_id}/screenshots", response_model=list[ScreenshotOut])
async def list_screenshots(task_id: str):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        rows = (
            await session.execute(
                select(Artifact)
                .where(Artifact.task_id == task_id, Artifact.kind == "screenshot")
                .order_by(Artifact.created_at)
            )
        ).scalars().all()
    return [ScreenshotOut(id=a.id, filename=a.filename, created_at=a.created_at) for a in rows]


@router.get("/api/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    async with SessionLocal() as session:
        artifact = await session.get(Artifact, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        data = artifact.data
        mime = artifact.mime or "application/octet-stream"
    return Response(content=data, media_type=mime)


# ── approvals ────────────────────────────────────────────


def _approval_out(a: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        task_id=a.task_id,
        kind=a.kind,
        description=a.description,
        payload=a.payload or {},
        created_at=a.created_at,
    )


@router.get("/api/approvals", response_model=list[ApprovalOut])
async def list_approvals(pending: bool = Query(default=False)):
    async with SessionLocal() as session:
        stmt = select(Approval).order_by(Approval.created_at.desc())
        if pending:
            stmt = stmt.where(Approval.status == "pending")
        rows = (await session.execute(stmt)).scalars().all()
    return [_approval_out(a) for a in rows]


@router.post("/api/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(approval_id: str, body: ApprovalDecision):
    worker = get_worker()
    async with SessionLocal() as session:
        approval = await session.get(Approval, approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        if approval.status != "pending":
            raise HTTPException(status_code=409, detail=f"approval already {approval.status}")
        task = await session.get(Task, approval.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail=f"task is {task.status}; decision not applicable")
        approval.status = "approved" if body.decision == "approve" else "denied"
        approval.decided_at = utcnow()
        if body.decision == "approve" and task.status == "awaiting_approval":
            task.status = "running"
            task.updated_at = utcnow()
        await session.commit()
        await session.refresh(approval)
        task_id = approval.task_id
    worker.resolve_approval(approval_id, body.decision == "approve")
    if not worker.is_active(task_id) and body.decision == "approve":
        worker.enqueue(task_id)
    return _approval_out(approval)


# ── push ─────────────────────────────────────────────────


@router.post("/api/push/subscribe", status_code=201)
async def push_subscribe(body: PushSubscribe):
    async with SessionLocal() as session:
        existing = await session.get(PushSubscription, body.endpoint)
        if existing is None:
            session.add(
                PushSubscription(
                    endpoint=body.endpoint,
                    p256dh=body.keys.p256dh,
                    auth=body.keys.auth,
                )
            )
            await session.commit()
    return Response(status_code=201)


@router.delete("/api/push/subscribe", status_code=204)
async def push_unsubscribe(body: PushUnsubscribe):
    async with SessionLocal() as session:
        existing = await session.get(PushSubscription, body.endpoint)
        if existing is not None:
            await session.delete(existing)
            await session.commit()
    return Response(status_code=204)


@router.get("/api/push/vapid-public")
async def vapid_public():
    from ..config import settings as _settings

    return {"public_key": _settings.VAPID_PUBLIC_KEY or ""}


# ── settings ─────────────────────────────────────────────


@router.get("/api/settings", response_model=SettingsOut)
async def get_settings():
    async with SessionLocal() as session:
        data = await get_settings_data(session)
    return SettingsOut(**data)


@router.put("/api/settings", response_model=SettingsOut)
async def put_settings(body: SettingsIn):
    data = body.model_dump()
    await save_settings(data)
    return SettingsOut(**data)


# ── models proxy ─────────────────────────────────────────


@router.get("/api/models", response_model=ModelsOut)
async def list_models():
    if not any_configured():
        return ModelsOut(models=[])
    try:
        models = list_pa_models()
    except Exception as exc:
        log.warning("models proxy failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"failed to fetch models: {exc}")
    return ModelsOut(models=models)


# ── connectors ───────────────────────────────────────────


@router.get("/api/connectors", response_model=list[ConnectorOut])
async def list_connectors():
    rows = await connectors_mod.list_connectors()
    return [ConnectorOut(**connectors_mod.to_out(c)) for c in rows]


@router.post("/api/connectors", response_model=ConnectorOut, status_code=201)
async def create_connector(body: ConnectorCreate):
    if body.kind not in connectors_mod.KNOWN_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown connector kind '{body.kind}'")
    connector = await connectors_mod.upsert(
        body.kind, name=body.name, config=body.config, enabled=body.enabled
    )
    return ConnectorOut(**connectors_mod.to_out(connector))


@router.put("/api/connectors/{connector_id}", response_model=ConnectorOut)
async def update_connector(connector_id: str, body: ConnectorUpdate):
    connector = await connectors_mod.update(
        connector_id, name=body.name, config=body.config, enabled=body.enabled
    )
    if connector is None:
        raise HTTPException(status_code=404, detail="connector not found")
    return ConnectorOut(**connectors_mod.to_out(connector))


@router.delete("/api/connectors/{connector_id}", status_code=204)
async def delete_connector(connector_id: str):
    if not await connectors_mod.delete(connector_id):
        raise HTTPException(status_code=404, detail="connector not found")
    return Response(status_code=204)
