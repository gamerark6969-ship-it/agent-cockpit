"""Public and authenticated HTTP contracts for YouTube automation."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..crypto import decrypt_json
from ..db import SessionLocal
from ..security import require_token
from . import youtube
from .dispatch import dispatch_worker
from .config import automation_settings as cfg
from .models import VideoRun, VideoRunEvent, VideoSeries, YouTubeCredential
from .schemas import RunOut, SeriesInput, SeriesPatch
from .service import AutomationService
from .storage import media_file_response, media_url, verify_media_signature

router = APIRouter(prefix="/api/automation", dependencies=[Depends(require_token)])
public_router = APIRouter()
service = AutomationService()


def _series_out(series: VideoSeries) -> dict:
    return {
        "id": series.id,
        "name": series.name,
        "brief": series.brief,
        "model": series.model,
        "timezone": series.timezone,
        "publish_times": series.publish_times,
        "enabled": series.enabled,
        "publish_mode": series.publish_mode,
        "privacy": series.privacy,
        "language": series.language,
        "voice": series.voice,
        "made_for_kids": series.made_for_kids,
        "contains_synthetic_media": series.contains_synthetic_media,
        "created_at": series.created_at,
        "updated_at": series.updated_at,
    }


async def _run_out(run: VideoRun, events: bool = False) -> dict:
    result = RunOut(
        id=run.id,
        series_id=run.series_id,
        status=run.status,
        stage=run.stage,
        title=run.title,
        description=run.description,
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        youtube_video_id=run.youtube_video_id,
        youtube_url=run.youtube_url,
        media_url=media_url(run.id, run.render_data) if run.media_path else None,
        manifest=run.manifest,
    )
    data = result.model_dump(mode="json")
    if events:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(VideoRunEvent).where(VideoRunEvent.run_id == run.id).order_by(VideoRunEvent.created_at, VideoRunEvent.id)
                )
            ).scalars().all()
        data["events"] = [
            {"type": row.type, "message": row.message, "created_at": row.created_at.isoformat()}
            for row in rows
        ]
    return data


async def _renderer_status() -> dict:
    try:
        from ..video_renderer import renderer_status

        result = renderer_status()
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, dict):
            return {"available": bool(result.get("available")), "detail": result.get("detail")}
    except Exception:
        pass
    return {"available": False, "detail": "Renderer is unavailable"}


async def _models() -> list[str]:
    try:
        from ..agent.models import list_generation_models

        values = list_generation_models()
        return sorted({str(value) for value in values})
    except Exception:
        try:
            from ..agent.models import list_pa_models

            return sorted({str(value) for value in list_pa_models()})
        except Exception:
            return []


@router.get("/status")
async def status():
    async with SessionLocal() as session:
        credential = await session.get(YouTubeCredential, 1)
        connected = bool(credential and decrypt_json(credential.tokens_enc).get("refresh_token"))
        channel = {
            "channel_id": credential.channel_id,
            "channel_title": credential.channel_title,
        } if credential else {}
    renderer = await _renderer_status()
    root = cfg.storage_dir.expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        storage = {
            "available": True,
            "backend": "s3" if cfg.s3_configured else "filesystem",
            "path": str(root.resolve()),
            "persistent": cfg.storage_persistent or cfg.s3_configured,
        }
    except OSError:
        storage = {"available": False, "backend": "filesystem", "persistent": cfg.storage_persistent}
    return {
        "youtube": {"configured": cfg.youtube_configured, "connected": connected, **channel},
        "renderer": renderer,
        "models": await _models(),
        "storage": storage,
        "worker": service.worker_status(),
    }


@router.get("/series")
async def list_series():
    async with SessionLocal() as session:
        values = (await session.execute(select(VideoSeries).order_by(VideoSeries.created_at, VideoSeries.id))).scalars().all()
        return [_series_out(series) for series in values]


@router.post("/series", status_code=201)
async def create_series(payload: SeriesInput):
    values = payload.model_dump()
    async with SessionLocal() as session:
        series = VideoSeries(**values)
        session.add(series)
        await session.commit()
        await session.refresh(series)
        return _series_out(series)


@router.patch("/series/{series_id}")
async def patch_series(series_id: str, payload: SeriesPatch):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields supplied")
    async with SessionLocal() as session:
        series = await session.get(VideoSeries, series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="Series not found")
        for field, value in changes.items():
            setattr(series, field, value)
        series.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(series)
        return _series_out(series)


@router.post("/series/{series_id}/run", status_code=201)
async def create_run(series_id: str):
    async with SessionLocal() as session:
        series = await session.get(VideoSeries, series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="Series not found")
        run = VideoRun(series_id=series_id, status="queued", stage="generate")
        session.add(run)
        await session.commit()
        await session.refresh(run)
        response = await _run_out(run, events=True)
    response["worker_dispatched"] = await dispatch_worker()
    return response


@router.get("/runs")
async def list_runs(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    async with SessionLocal() as session:
        values = (
            await session.execute(select(VideoRun).order_by(VideoRun.created_at.desc(), VideoRun.id).offset(offset).limit(limit))
        ).scalars().all()
        return [await _run_out(run) for run in values]


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    async with SessionLocal() as session:
        run = await session.get(VideoRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return await _run_out(run, events=True)


async def _run_action(run_id: str, action: str):
    if action == "approve":
        accepted = await service.approve(run_id)
    elif action == "retry":
        accepted = await service.retry(run_id)
    else:
        accepted = await service.cancel(run_id)
    if not accepted:
        async with SessionLocal() as session:
            if await session.get(VideoRun, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
        verb = {"approve": "approved", "retry": "retried", "cancel": "cancelled"}[action]
        raise HTTPException(status_code=409, detail=f"Run cannot be {verb} in its current state")
    async with SessionLocal() as session:
        run = await session.get(VideoRun, run_id)
        response = await _run_out(run, events=True)
    if action in ("approve", "retry"):
        response["worker_dispatched"] = await dispatch_worker()
    return response


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: str):
    return await _run_action(run_id, "approve")


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str):
    return await _run_action(run_id, "retry")


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    return await _run_action(run_id, "cancel")


@router.get("/youtube/connect")
async def connect_youtube():
    async with SessionLocal() as session:
        return {"authorization_url": await youtube.authorization_url(session)}


@public_router.get("/api/automation/youtube/callback")
async def youtube_callback(code: str | None = None, state: str = "", error: str | None = None):
    if error:
        # Never reflect OAuth's provider-supplied error text into the response.
        raise HTTPException(status_code=400, detail="YouTube authorization was not completed")
    if not code or not state:
        raise HTTPException(status_code=400, detail="YouTube authorization was not completed")
    async with SessionLocal() as session:
        await youtube.oauth_callback(session, code=code, state=state)
    if cfg.frontend_url:
        return RedirectResponse(cfg.frontend_url.rstrip("/") + "/studio?youtube=connected", status_code=303)
    return {"connected": True}


@router.post("/youtube/disconnect")
async def disconnect_youtube():
    async with SessionLocal() as session:
        await youtube.disconnect(session)
    return {"connected": False}


@public_router.get("/api/automation/media/{run_id}")
async def public_media(run_id: str, expires: int, sig: str, request: Request):
    if not verify_media_signature(run_id, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired media link")
    async with SessionLocal() as session:
        run = await session.get(VideoRun, run_id)
        if run is None or not run.media_path:
            raise HTTPException(status_code=404, detail="Media not found")
        path = service._owned_media_path(run, run.media_path)
    return await media_file_response(path, request)
