"""Durable, lease-claimed generation/render/review/YouTube worker."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import and_, inspect as sa_inspect, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import utcnow
from .config import automation_settings as cfg
from .models import VideoRun, VideoRunEvent, VideoSeries
from .schedule import scheduled_slots
from .schemas import VideoManifest
from .storage import media_url, run_directory, archive_media, restore_media
from . import charts, youtube

log = logging.getLogger("automation.worker")
_TERMINAL = {"published", "uploaded", "failed", "cancelled"}


class StageCancelled(RuntimeError):
    pass


class StageLeaseLost(RuntimeError):
    pass


class AutomationService:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        self.worker_id = uuid.uuid4().hex
        self._loop_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._active_run: str | None = None
        self._last_error: str | None = None
        self._last_poll_at: datetime | None = None

    async def start(self) -> None:
        if not cfg.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(self._poll_loop(), name="youtube-automation")

    async def stop(self) -> None:
        self._stopping.set()
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # The durable lease expires quickly; shutdown never marks a possibly
        # in-flight YouTube request as failed or blindly repeats an upload.
        self._active_run = None

    def worker_status(self) -> dict:
        alive = bool(self._loop_task and not self._loop_task.done())
        return {
            "enabled": bool(cfg.enabled),
            "running": alive,
            "worker_id": self.worker_id if alive else None,
            "active_run_id": self._active_run,
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "last_error": self._last_error,
        }

    async def _poll_loop(self) -> None:
        while not self._stopping.is_set():
            self._last_poll_at = utcnow()
            try:
                await self.reserve_scheduled_slots()
                run = await self.claim_one()
                if run:
                    self._active_run = run
                    await self._process(run)
                    self._active_run = None
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep poll errors intentionally generic: provider/network exceptions
                # can contain credentials or request URLs.
                self._last_error = "Automation worker encountered a temporary error"
                log.warning("automation worker poll failed")
            await asyncio.sleep(cfg.poll_interval_s)

    async def reserve_scheduled_slots(self, now: datetime | None = None) -> int:
        now = (now or utcnow()).astimezone(timezone.utc)
        start = now - timedelta(minutes=cfg.missed_slot_grace_minutes)
        end = now + timedelta(hours=cfg.schedule_horizon_hours)
        created = 0
        async with self.session_factory() as session:
            series_rows = (await session.execute(select(VideoSeries).where(VideoSeries.enabled.is_(True)))).scalars().all()
            series_rows = [
                SimpleNamespace(id=row.id, timezone=row.timezone, publish_times=list(row.publish_times or []))
                for row in series_rows
            ]
            for series in series_rows:
                for slot in scheduled_slots(series, start, end):
                    # Unique DB constraint is the cross-process reservation. Each
                    # insert commits separately so a competing insert cannot undo
                    # neighboring slots in this pass.
                    session.add(VideoRun(series_id=series.id, scheduled_for=slot, status="queued", stage="generate"))
                    try:
                        await session.commit()
                        created += 1
                    except IntegrityError:
                        await session.rollback()
        return created

    async def claim_one(self) -> str | None:
        now = utcnow()
        expired = or_(VideoRun.lease_expires_at.is_(None), VideoRun.lease_expires_at < now)
        async with self.session_factory() as session:
            await session.execute(update(VideoRun).where(
                VideoRun.status == "queued", VideoRun.stage == "generate", VideoRun.scheduled_for.is_not(None),
                VideoRun.scheduled_for < now - timedelta(minutes=cfg.missed_slot_grace_minutes),
            ).values(status="cancelled", error="Missed production slot", updated_at=now))
            await session.commit()
            due_queued = and_(
                VideoRun.status == "queued",
                or_(VideoRun.scheduled_for.is_(None), VideoRun.scheduled_for <= now),
                or_(VideoRun.next_attempt_at.is_(None), VideoRun.next_attempt_at <= now),
            )
            reclaimable = and_(VideoRun.status.in_(("running", "uploading")), expired)
            candidate = await session.scalar(
                select(VideoRun.id)
                .where(
                    or_(due_queued, reclaimable)
                )
                .order_by(VideoRun.created_at, VideoRun.id)
                .limit(1)
            )
            if candidate is None:
                return None
            result = await session.execute(
                update(VideoRun)
                .execution_options(synchronize_session=False)
                .where(
                    VideoRun.id == candidate,
                    or_(due_queued, reclaimable),
                )
                .values(
                    status="uploading" if (await session.get(VideoRun, candidate)).stage == "upload" else "running",
                    lease_owner=self.worker_id,
                    lease_expires_at=now + timedelta(seconds=cfg.lease_seconds),
                    fence=VideoRun.fence + 1,
                    attempt=VideoRun.attempt + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            return candidate

    async def has_due_work(self) -> bool:
        now = utcnow()
        async with self.session_factory() as session:
            candidate = await session.scalar(select(VideoRun.id).where(or_(
                and_(VideoRun.status == "queued", or_(VideoRun.scheduled_for.is_(None), VideoRun.scheduled_for <= now),
                     or_(VideoRun.next_attempt_at.is_(None), VideoRun.next_attempt_at <= now)),
                and_(VideoRun.status.in_(("running", "uploading")), or_(VideoRun.lease_expires_at.is_(None), VideoRun.lease_expires_at < now)),
            )).limit(1))
            return candidate is not None

    async def _renew_lease(self, run_id: str, fence: int, lost: asyncio.Event) -> None:
        interval = max(5, cfg.lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            async with self.session_factory() as session:
                result = await session.execute(
                    update(VideoRun)
                    .where(VideoRun.id == run_id, VideoRun.lease_owner == self.worker_id, VideoRun.fence == fence)
                    .values(lease_expires_at=utcnow() + timedelta(seconds=cfg.lease_seconds))
                )
                await session.commit()
                if result.rowcount != 1:
                    lost.set()
                    return

    async def _guard_stage(self, run_id: str, fence: int, coroutine):
        """Cancel expensive work promptly when a phone cancels it or ownership changes."""
        task = asyncio.create_task(coroutine)
        try:
            while not task.done():
                done, _ = await asyncio.wait({task},timeout=2)
                if done:
                    break
                async with self.session_factory() as session:
                    current = await session.get(VideoRun,run_id)
                    if current is None or current.lease_owner != self.worker_id or current.fence != fence:
                        raise StageLeaseLost()
                    if current.cancel_requested:
                        raise StageCancelled()
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def _process(self, run_id: str) -> None:
        async with self.session_factory() as session:
            run = await session.get(VideoRun, run_id)
            if run is None or run.lease_owner != self.worker_id:
                return
            fence = run.fence
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._renew_lease(run_id, fence, lost))
        processing_checks = 0
        try:
            while not lost.is_set():
                async with self.session_factory() as session:
                    run = await session.get(VideoRun, run_id)
                    if run is None or run.lease_owner != self.worker_id or run.fence != fence:
                        return
                    if run.cancel_requested:
                        await self._finish(session, run, "cancelled", "Run cancelled", fence)
                        return
                    series = await session.get(VideoSeries, run.series_id)
                    if series is None:
                        await self._fail(session, run, "Series no longer exists", fence)
                        return
                    if not series.enabled and run.scheduled_for is not None:
                        await self._finish(session, run, "cancelled", "Series schedule is paused", fence)
                        return
                    series_data = SimpleNamespace(
                        id=series.id,
                        name=series.name,
                        brief=series.brief,
                        model=series.model,
                        timezone=series.timezone,
                        publish_times=list(series.publish_times or []),
                        enabled=series.enabled,
                        publish_mode=series.publish_mode,
                        privacy=series.privacy,
                        language=series.language,
                        voice=series.voice,
                        made_for_kids=series.made_for_kids,
                        contains_synthetic_media=series.contains_synthetic_media,
                    )
                    stage = run.stage
                    if stage == "generate":
                        # Do not retain a database transaction while waiting on a provider.
                        await session.commit()
                        try:
                            manifest = await self._guard_stage(run_id,fence,self._generate(series_data, run_id))
                        except StageCancelled:
                            await self._finish(session, run, "cancelled", "Run cancelled", fence)
                            return
                        except StageLeaseLost:
                            return
                        except Exception:
                            await self._fail(session, run, "Video script generation failed; checking the provider on the next attempt", fence, retryable=True)
                            return
                        if lost.is_set():
                            return
                        await session.refresh(run)
                        if run.cancel_requested:
                            await self._finish(session, run, "cancelled", "Run cancelled", fence)
                            return
                        run.manifest = manifest
                        run.title = manifest["title"]
                        run.description = manifest["description"]
                        run.stage = "render"
                        await self._event(session, run.id, "generated", "Original video script generated")
                        if not await self._fenced_commit(session, run, fence):
                            return
                        continue
                    if stage == "render":
                        # Renderer work can take several minutes; lease renewal and
                        # API cancellation must be able to use independent sessions.
                        await session.commit()
                        try:
                            result = await self._guard_stage(run_id,fence,self._render(series_data, run_id, run.manifest or {}))
                        except StageCancelled:
                            await self._finish(session, run, "cancelled", "Run cancelled", fence)
                            return
                        except StageLeaseLost:
                            return
                        except Exception:
                            await self._fail(session, run, "Video rendering failed; retry this run", fence)
                            return
                        if lost.is_set():
                            return
                        await session.refresh(run)
                        if run.cancel_requested:
                            await self._finish(session, run, "cancelled", "Run cancelled", fence)
                            return
                        if result.get("music_selection"):
                            await self._event(session, run.id, "music", result["music_selection"])
                        run.render_data = result
                        run.media_path = result["path"]
                        run.thumbnail_path = result["thumbnail_path"]
                        if series.publish_mode == "review":
                            run.status = "review"
                            run.stage = "upload"
                            run.lease_owner = None
                            run.lease_expires_at = None
                            run.updated_at = utcnow()
                            await self._event(session, run.id, "review", "Video is ready for approval")
                            if not await self._fenced_commit(session, run, fence):
                                return
                            from ..push import send_push_to_all
                            await send_push_to_all({"type":"video_review", "run_id":run_id, "description":"Your draft is ready in Studio."})
                            return
                        run.status = "uploading"
                        run.stage = "upload"
                        await self._event(session, run.id, "rendered", "Video rendered; upload is starting")
                        if not await self._fenced_commit(session, run, fence):
                            return
                        continue
                    if stage == "upload":
                        await session.commit()
                        try:
                            media = await restore_media(run_id, run.render_data) if cfg.s3_configured else self._owned_media_path(run, run.media_path)
                        except Exception:
                            await self._fail(session,run,"Preview media is unavailable; check persistent storage before retrying",fence)
                            return

                        async def cancel_requested() -> bool:
                            async with self.session_factory() as current:
                                latest = await current.get(VideoRun, run_id)
                                return (
                                    latest is None
                                    or latest.lease_owner != self.worker_id
                                    or latest.fence != fence
                                    or latest.cancel_requested
                                )

                        try:
                            video_id = await youtube.upload_video(
                                session,
                                run,
                                series_data,
                                media,
                                lease_owner=self.worker_id,
                                fence=fence,
                                cancel_requested=cancel_requested,
                            )
                        except youtube.UploadCancelled:
                            await self._finish(session, run, "cancelled", "Run cancelled", fence)
                            return
                        except Exception:
                            await self._fail(session, run, "YouTube upload failed or was interrupted; retry will reconcile it", fence, retryable=True)
                            return
                        if lost.is_set():
                            return
                        if video_id:
                            run.youtube_video_id = video_id
                            run.youtube_url = f"https://www.youtube.com/watch?v={video_id}"
                            run.stage = "process"
                            run.upload_session_enc = None
                            run.upload_session_expires_at = None
                            run.updated_at = utcnow()
                            await self._event(session, run.id, "uploaded", "Video uploaded; checking YouTube processing")
                            if not await self._fenced_commit(session, run, fence):
                                return
                            continue
                        await self._fail(session, run, "YouTube upload could not be confirmed; retry will reconcile it", fence)
                        return
                    if stage == "process":
                        await session.commit()
                        try:
                            state = await youtube.processing_status(session, run.youtube_video_id)
                        except Exception:
                            await self._fail(session, run, "YouTube processing check failed; retry without re-uploading", fence)
                            return
                        if state == "ready":
                            run.stage = "complete"
                            status = "published" if series_data.privacy == "public" else "uploaded"
                            await self._finish(session, run, status, "Video processed on YouTube", fence)
                            return
                        processing_checks += 1
                        if processing_checks >= 12:
                            run.status = "queued"
                            run.next_attempt_at = utcnow() + timedelta(minutes=5)
                            run.lease_owner = None
                            run.lease_expires_at = None
                            await self._fenced_commit(session, run, fence)
                            return
                        await asyncio.sleep(5)
                        continue
                    await self._fail(session, run, "Run has an invalid saved stage", fence)
                    return
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _generate(self, series: VideoSeries, run_id: str) -> dict:
        try:
            from ..agent import models as provider_models

            generate_text = provider_models.generate_text
            model_list = getattr(provider_models, "list_generation_models", None) or getattr(
                provider_models, "list_pa_models", None
            )
        except (ImportError, AttributeError):
            raise RuntimeError("No content generation provider is available")
        models = model_list() if model_list else []
        if not models:
            raise RuntimeError("No content generation provider is configured")
        async with self.session_factory() as session:
            previous = (
                await session.execute(
                    select(VideoRun.title, VideoRun.manifest)
                    .where(VideoRun.series_id == series.id, VideoRun.id != run_id, VideoRun.title.is_not(None))
                    .order_by(VideoRun.created_at.desc())
                    .limit(25)
                )
            ).all()
        recent_items = []
        for title, prior_manifest in previous:
            scenes = (prior_manifest or {}).get("scenes") or []
            story = str(scenes[0].get("narration", ""))[:240] if scenes else ""
            recent_items.append(f"- {title}: {story}" if story else f"- {title}")
        recent = "\n".join(recent_items) or "- (none)"
        system = (
            "You write original scripts for short animated videos. Output only one JSON object matching the requested schema. "
            "Never copy or closely retell existing works. Each video has 5 to 8 scenes and the combined narration has 80 to 130 words."
        )
        base = (
            f"Create a distinct, complete animated short for this series.\nSeries brief: {series.brief}\n"
            f"Language: {series.language}. Avoid repeating these recent titles/story concepts:\n{recent}\n"
            'Return strict JSON: {"version":1,"title":"...","description":"...","scenes":[{"narration":"...","visual":"...","caption":"...","palette":"..."}],"tags":["..."]}. '
            "For each scene also include setting (town, forest, space, ocean, room), action (walk, run, discover, talk, celebrate, escape, wait), "
            "emotion (happy, worried, surprised, determined), and prop (clock, key, door, star, bridge, none). These are the supported illustrated animation directions. "
            "Use different camera beats and at least two settings or actions when the story calls for them. Keep a consistent pair of original block characters. "
            "Use 5-8 scenes; total narration 80-130 words. Keep narration suitable for 30-60 seconds of natural speech. Include a fresh plot, a clear ending, and visual direction that can be rendered as animation."
        )
        last_error = None
        for attempt in range(3):
            prompt = base if attempt == 0 else base + f"\nPrevious response did not validate ({last_error}). Return corrected strict JSON only."
            raw = await generate_text(prompt, model=series.model, system_prompt=system)
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                parsed = json.loads(cleaned)
                return VideoManifest.model_validate(parsed).model_dump()
            except (json.JSONDecodeError, ValueError, TypeError):
                last_error = "JSON/schema validation error"
        raise RuntimeError("Model output invalid")

    async def _render(self, series: VideoSeries, run_id: str, manifest: dict) -> dict:
        try:
            from .. import video_renderer
        except ImportError:
            raise RuntimeError("The video renderer is not available")
        output_dir = run_directory(run_id)
        async with self.session_factory() as session:
            music, music_message = await charts.select_music(session, series.id)
        if music:
            with TemporaryDirectory(dir=output_dir) as directory:
                music_path = Path(directory) / "track.mp3"
                await asyncio.to_thread(music_path.write_bytes, music)
                result = await video_renderer.render_video(manifest, output_dir, voice=series.voice, music_path=music_path)
        else:
            result = await video_renderer.render_video(manifest, output_dir, voice=series.voice)
        if not isinstance(result, dict):
            raise RuntimeError("Renderer returned invalid result")
        required = {"path", "thumbnail_path", "duration_seconds", "width", "height", "checks"}
        if not required.issubset(result):
            raise RuntimeError("Renderer returned incomplete result")
        path = self._owned_media_path_by_id(run_id, str(result["path"]))
        thumb = self._owned_media_path_by_id(run_id, str(result["thumbnail_path"]))
        if not path.is_file() or not thumb.is_file():
            raise RuntimeError("Renderer output is missing")
        if not 30 <= float(result["duration_seconds"]) <= 60:
            raise RuntimeError("Rendered duration is outside the expected range")
        if result["checks"].get("full_decode") != "passed" or result["checks"].get("audio") != "real_narration":
            raise RuntimeError("Rendered video did not pass media and narration checks")
        if not 0 < int(result["width"]) < int(result["height"]) or path.stat().st_size > 45 * 1024 * 1024:
            raise RuntimeError("Rendered short must be portrait and below the free storage file-size limit")
        result["music_selection"] = music_message
        archived = await archive_media(run_id, result)
        return {**result, **archived, "path": str(path), "thumbnail_path": str(thumb)}

    def _owned_media_path_by_id(self, run_id: str, value: str) -> Path:
        root = run_directory(run_id).resolve()
        path = Path(value).expanduser().resolve()
        if path.parent != root or path.name in ("", ".", ".."):
            raise ValueError("media path does not belong to run")
        return path

    def _owned_media_path(self, run: VideoRun, value: str | None) -> Path:
        if not value:
            raise ValueError("media is not ready")
        return self._owned_media_path_by_id(run.id, value)

    async def _event(self, session, run_id: str, kind: str, message: str) -> None:
        session.add(VideoRunEvent(run_id=run_id, type=kind, message=message))

    async def _fenced_commit(self, session, run: VideoRun, fence: int) -> bool:
        """Write worker changes and events only while the lease generation still owns the row."""
        state = sa_inspect(run)
        values = {
            attribute.key: getattr(run, attribute.key)
            for attribute in state.attrs
            if attribute.history.has_changes() and attribute.key in VideoRun.__table__.columns
        }
        if not values:
            values = {"updated_at": utcnow()}
        with session.no_autoflush:
            result = await session.execute(
                update(VideoRun)
                .execution_options(synchronize_session=False)
                .where(
                    VideoRun.id == run.id,
                    VideoRun.lease_owner == self.worker_id,
                    VideoRun.fence == fence,
                    VideoRun.lease_expires_at > utcnow(),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                await session.rollback()
                return False
            # Avoid a second, unfenced ORM flush after the conditional SQL update.
            session.expire(run)
            await session.commit()
        return True

    async def _fail(self, session, run: VideoRun, message: str, fence: int, *, retryable: bool = False) -> None:
        run_id = run.id
        retrying = retryable and run.retry_count < cfg.retry_limit
        run.status = "queued" if retrying else "failed"
        if retrying:
            run.retry_count += 1
            run.next_attempt_at = utcnow() + timedelta(seconds=cfg.retry_delay_s * 2 ** (run.retry_count - 1))
        run.error = message
        run.lease_owner = None
        run.lease_expires_at = None
        run.updated_at = utcnow()
        await self._event(session, run.id, "retry_scheduled" if retrying else "failed", message)
        committed = await self._fenced_commit(session, run, fence)
        if committed and not retrying:
            from ..push import send_push_to_all
            await send_push_to_all({"type":"video_failed", "run_id":run_id, "description":message})

    async def _finish(self, session, run: VideoRun, status: str, message: str, fence: int | None = None) -> None:
        run_id = run.id
        run.status = status
        run.lease_owner = None
        run.lease_expires_at = None
        run.cancel_requested = False
        run.updated_at = utcnow()
        await self._event(session, run.id, status, message)
        if fence is None:
            await session.commit()
            committed = True
        else:
            committed = await self._fenced_commit(session, run, fence)
        if committed and status == "published":
            from ..push import send_push_to_all
            await send_push_to_all({"type":"video_published", "run_id":run_id, "description":message})

    async def approve(self, run_id: str) -> bool:
        async with self.session_factory() as session:
            run = await session.get(VideoRun, run_id)
            if not run or run.status != "review":
                return False
            run.status, run.stage, run.error = "queued", "upload", None
            run.updated_at = utcnow()
            await self._event(session, run_id, "approved", "Video approved for upload")
            await session.commit()
            return True

    async def retry(self, run_id: str) -> bool:
        async with self.session_factory() as session:
            run = await session.get(VideoRun, run_id)
            if not run or run.status != "failed":
                return False
            run.status = "queued"
            run.error = None
            run.cancel_requested = False
            run.next_attempt_at = None
            run.retry_count = 0
            run.lease_owner = None
            run.lease_expires_at = None
            run.updated_at = utcnow()
            await self._event(session, run_id, "retry", f"Retrying from saved stage: {run.stage}")
            await session.commit()
            return True

    async def cancel(self, run_id: str) -> bool:
        async with self.session_factory() as session:
            run = await session.get(VideoRun, run_id)
            if not run or run.status in _TERMINAL:
                return False
            if run.status in ("queued", "review"):
                await self._finish(session, run, "cancelled", "Run cancelled")
            else:
                run.cancel_requested = True
                run.updated_at = utcnow()
                await self._event(session, run_id, "cancel_requested", "Cancellation requested")
                await session.commit()
            return True

    @staticmethod
    def media_url(run_id: str) -> str:
        return media_url(run_id)
