"""Public and authenticated HTTP contracts for YouTube automation."""

from pathlib import Path
from tempfile import TemporaryDirectory

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from ..crypto import decrypt_json
from ..db import SessionLocal
from ..security import require_token
from . import charts, youtube
from .models import ChartTrack
from sqlalchemy.exc import IntegrityError
from fastapi import Path as PathParam
from .dispatch import dispatch_worker
from .config import automation_settings as cfg
from .models import VideoRun, VideoRunEvent, VideoSeries, YouTubeCredential
from .schemas import RunOut, SeriesInput, SeriesPatch
from .schedule import lock_series_schedule, scheduled_slots
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
        "music_name": series.music_name,
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
        client, source = await youtube.client_settings(session)
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
        "youtube": {"configured": bool(client and cfg.callback_url), "redirect_uri": cfg.callback_url, "client_source": source, "connected": connected, **channel},
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
        series = await lock_series_schedule(session, series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="Series not found")
        schedule_changed = any(field in changes and changes[field] != getattr(series, field)
                               for field in ("timezone", "publish_times", "enabled"))
        for field, value in changes.items():
            setattr(series, field, value)
        if schedule_changed:
            series.schedule_effective_at = datetime.now(timezone.utc)
            pending = (
                VideoRun.series_id == series_id, VideoRun.scheduled_for.is_not(None),
                VideoRun.stage == "generate", VideoRun.status == "queued", VideoRun.attempt == 0,
            )
            if series.enabled:
                # Keep unchanged slots, including a due slot waiting for its
                # first worker. Only the newly configured times start now.
                rows = (await session.execute(select(VideoRun.id, VideoRun.scheduled_for).where(*pending))).all()
                obsolete = []
                for run_id, timestamp in rows:
                    slot = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
                    if not scheduled_slots(series, slot, slot):
                        obsolete.append(run_id)
                await session.execute(delete(VideoRun).where(*pending, VideoRun.id.in_(obsolete)))
            else:
                await session.execute(delete(VideoRun).where(*pending))
        series.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(series)
        return _series_out(series)


@router.put("/series/{series_id}/music")
async def upload_music(series_id: str, request: Request, name: str = Query(min_length=1, max_length=160)):
    return await _set_music(series_id, name, await _read_music(request, 12))


async def _read_music(request: Request, limit_mib: int) -> bytes:
    from ..video_renderer import validate_music

    if request.headers.get("content-type", "").split(";")[0].lower() != "audio/mpeg":
        raise HTTPException(415, "Upload an MP3 with content type audio/mpeg")
    track = bytearray()
    async for chunk in request.stream():
        if len(track) + len(chunk) > limit_mib * 1024 * 1024:
            raise HTTPException(413, f"Music exceeds {limit_mib} MiB")
        track.extend(chunk)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "track.mp3"
        path.write_bytes(track)
        try:
            await validate_music(path)
        except (RuntimeError, TimeoutError, ValueError):
            raise HTTPException(422, "Upload a decodable MP3 track")
    return bytes(track)


@router.delete("/series/{series_id}/music")
async def clear_music(series_id: str):
    return await _set_music(series_id, None, None)


async def _set_music(series_id: str, name: str | None, data: bytes | None):
    async with SessionLocal() as session:
        series = await session.get(VideoSeries, series_id)
        if series is None:
            raise HTTPException(404, "Series not found")
        series.music_name = name
        series.music_data = data
        series.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(series)
        return _series_out(series)


async def _chart_state(session, series_id):
    series = await session.get(VideoSeries, series_id)
    if series is None:
        raise HTTPException(404, "Series not found")
    rows = (await session.scalars(select(ChartTrack).where(ChartTrack.series_id == series_id).order_by(ChartTrack.slot))).all()
    return {"playlist_id": series.chart_playlist, "candidates": [
        {"slot": row.slot, "video_id": row.video_id, "name": row.name} for row in rows]}


@router.get("/series/{series_id}/chart")
async def get_chart(series_id: str):
    async with SessionLocal() as session:
        return await _chart_state(session, series_id)


@router.put("/series/{series_id}/chart")
async def set_chart(series_id: str, playlist: str = Query(max_length=1024)):
    try:
        playlist_id = charts.parse_playlist(playlist)
    except ValueError:
        raise HTTPException(422, "Enter a YouTube playlist URL or playlist ID")
    async with SessionLocal() as session:
        series = await session.get(VideoSeries, series_id)
        if series is None:
            raise HTTPException(404, "Series not found")
        series.chart_playlist = playlist_id
        await session.commit()
        return await _chart_state(session, series_id)


@router.put("/series/{series_id}/chart/{slot}")
async def put_candidate(series_id: str, request: Request, slot: int = PathParam(ge=1, le=3),
                        video_id: str = Query(pattern=r"^[A-Za-z0-9_-]{11}$"), name: str = Query(min_length=1, max_length=160)):
    audio = await _read_music(request, 4)
    async with SessionLocal() as session:
        if await session.get(VideoSeries, series_id) is None:
            raise HTTPException(404, "Series not found")
        await session.merge(ChartTrack(series_id=series_id, slot=slot, video_id=video_id, name=name, audio=audio))
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(409, "This video or slot is already in use. Refresh the candidates and retry.")
        return await _chart_state(session, series_id)


@router.delete("/series/{series_id}/chart/{slot}")
async def remove_candidate(series_id: str, slot: int = PathParam(ge=1, le=3)):
    async with SessionLocal() as session:
        track = await session.get(ChartTrack, (series_id, slot))
        if track:
            await session.delete(track)
            await session.commit()
        return await _chart_state(session, series_id)


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


@router.put("/youtube/client")
async def configure_youtube(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
            raise HTTPException(413, "Google client file exceeds 64 KiB.")
        raw.extend(chunk)
    async with SessionLocal() as session:
        await youtube.save_web_client(session, bytes(raw))
    return {"configured": True}


@router.get("/youtube/connect")
async def connect_youtube():
    async with SessionLocal() as session:
        return {"authorization_url": await youtube.authorization_url(session)}


@public_router.get("/api/automation/youtube/callback")
async def youtube_callback(code: str | None = None, state: str = "", error: str | None = None):
    try:
        if error or not code or not state:
            raise HTTPException(400, "YouTube authorization was not completed")
        async with SessionLocal() as session:
            await youtube.oauth_callback(session, code=code, state=state)
    except HTTPException:
        if cfg.studio_url:
            outcome = "cancelled" if error == "access_denied" else "error"
            return RedirectResponse(f"{cfg.studio_url}?youtube={outcome}", status_code=303)
        raise
    if cfg.studio_url:
        return RedirectResponse(f"{cfg.studio_url}?youtube=connected", status_code=303)
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
