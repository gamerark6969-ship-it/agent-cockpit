import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.automation import models as automation_models
from app.automation.config import automation_settings
from app.automation.models import VideoRun, VideoRunEvent, VideoSeries
from app.automation.service import AutomationService
from app.models import utcnow
from app.models import Base
from app.agent import models as provider_models


def _series(**updates):
    values = dict(
        id="series-1",
        name="Tiny tales",
        brief="Original animated shorts about small acts of courage.",
        timezone="UTC",
        publish_times=["09:00"],
        enabled=True,
        publish_mode="review",
        language="en",
        voice="en-US-AriaNeural",
        made_for_kids=False,
        contains_synthetic_media=False,
    )
    values.update(updates)
    return VideoSeries(**values)


class AutomationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add(_series())
            await session.commit()
        self.service = AutomationService(self.sessions)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_schedule_reservation_is_unique_and_only_catches_recent_missed_slots(self):
        async with self.sessions() as session:
            series = await session.get(VideoSeries, "series-1")
            series.publish_times = ["10:00"]
            series.created_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
            await session.commit()
        now = datetime(2026, 2, 5, 10, 45, tzinfo=timezone.utc)
        with (
            patch("app.automation.service.cfg.missed_slot_grace_minutes", 60),
            patch("app.automation.service.cfg.schedule_horizon_hours", 25),
        ):
            first = await self.service.reserve_scheduled_slots(now)
            second = await self.service.reserve_scheduled_slots(now)
        async with self.sessions() as session:
            runs = (await session.execute(select(VideoRun))).scalars().all()
        self.assertEqual(first, 2)  # today's recent missed slot and tomorrow
        self.assertEqual(second, 0)
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(run.scheduled_for.tzinfo is None or run.scheduled_for.tzinfo for run in runs))

    async def test_claim_retries_generation_after_the_missed_slot_window(self):
        async with self.sessions() as session:
            session.add(VideoRun(id="generation-retry", series_id="series-1", status="queued",
                                 stage="generate", attempt=1, retry_count=1,
                                 scheduled_for=utcnow() - timedelta(days=2)))
            await session.commit()
        self.assertEqual(await self.service.claim_one(), "generation-retry")

    async def test_atomic_claim_and_expired_lease_reclaim_increment_fence(self):
        async with self.sessions() as session:
            session.add(VideoRun(id="run-1", series_id="series-1", status="queued", stage="generate"))
            await session.commit()
        first = AutomationService(self.sessions)
        second = AutomationService(self.sessions)
        self.assertEqual(await first.claim_one(), "run-1")
        self.assertIsNone(await second.claim_one())
        async with self.sessions() as session:
            run = await session.get(VideoRun, "run-1")
            initial_fence = run.fence
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        self.assertEqual(await second.claim_one(), "run-1")
        async with self.sessions() as session:
            run = await session.get(VideoRun, "run-1")
            self.assertEqual(run.lease_owner, second.worker_id)
            self.assertEqual(run.fence, initial_fence + 1)
            self.assertEqual(run.attempt, 2)

    async def test_stale_fence_cannot_commit_worker_result_or_event(self):
        async with self.sessions() as session:
            session.add(VideoRun(id="fenced-run", series_id="series-1", status="queued", stage="generate"))
            await session.commit()
        first = AutomationService(self.sessions)
        second = AutomationService(self.sessions)
        await first.claim_one()
        async with self.sessions() as session:
            stale = await session.get(VideoRun, "fenced-run")
            old_fence = stale.fence
            stale.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        await second.claim_one()
        async with self.sessions() as session:
            stale = await session.get(VideoRun, "fenced-run")
            stale.title = "Stale worker output"
            session.add(VideoRunEvent(run_id=stale.id, type="generated", message="Stale event"))
            accepted = await first._fenced_commit(session, stale, old_fence)
        self.assertFalse(accepted)
        async with self.sessions() as session:
            current = await session.get(VideoRun, "fenced-run")
            events = (await session.execute(select(VideoRunEvent).where(VideoRunEvent.run_id == "fenced-run"))).scalars().all()
        self.assertIsNone(current.title)
        self.assertEqual(current.lease_owner, second.worker_id)
        self.assertEqual(events, [])

    async def test_review_approval_retry_and_cancel_transitions(self):
        async with self.sessions() as session:
            session.add_all(
                [
                    VideoRun(id="review-run", series_id="series-1", status="review", stage="upload"),
                    VideoRun(id="failed-run", series_id="series-1", status="failed", stage="render", error="render failure"),
                    VideoRun(id="queued-run", series_id="series-1", status="queued", stage="generate"),
                    VideoRun(id="active-run", series_id="series-1", status="running", stage="render",
                             lease_owner="another-worker", lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1)),
                ]
            )
            await session.commit()
        self.assertTrue(await self.service.approve("review-run"))
        self.assertTrue(await self.service.retry("failed-run"))
        self.assertTrue(await self.service.cancel("queued-run"))
        self.assertTrue(await self.service.cancel("active-run"))
        self.assertFalse(await self.service.approve("queued-run"))
        async with self.sessions() as session:
            approved = await session.get(VideoRun, "review-run")
            retried = await session.get(VideoRun, "failed-run")
            cancelled = await session.get(VideoRun, "queued-run")
            active = await session.get(VideoRun, "active-run")
            events = (await session.execute(select(VideoRunEvent))).scalars().all()
        self.assertEqual((approved.status, approved.stage), ("queued", "upload"))
        self.assertEqual((retried.status, retried.stage, retried.error), ("queued", "render", None))
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(active.status, "running")
        self.assertTrue(active.cancel_requested)
        self.assertGreaterEqual(len(events), 3)

    async def test_review_pipeline_releases_lease_without_uploading(self):
        manifest = {
            "version": 1,
            "title": "The Lantern Garden",
            "description": "A gentle original short.",
            "scenes": [],
            "tags": ["animation"],
        }
        # A compact but valid overall narration; spread across six scenes.
        words = ("One evening a fox found a lantern beneath a tree. It carried the light through the garden, "
                 "helping a shy moth find its family. Together they lit the path for every creature, "
                 "then left the lantern where another traveler could find it. "
                 "At dawn, the fox returned and discovered that the garden had grown brighter overnight. "
                 "The moth thanked its friend, and they welcomed a lost hedgehog with a tiny parade. "
                 "Before sunset, they placed the lantern beside the gate for whoever needed help next.").split()
        manifest["scenes"] = [
            {"narration": " ".join(words[index:index + 19]), "visual": "Warm animated garden scene."}
            for index in range(0, len(words), 19)
        ]
        self.assertGreaterEqual(len(words), 80)
        self.assertLessEqual(len(words), 130)
        while len(manifest["scenes"]) < 5:
            manifest["scenes"].append({"narration": "The lantern glowed beside the old tree.", "visual": "The garden settles."})
        manifest["scenes"] = manifest["scenes"][:8]
        async with self.sessions() as session:
            series = await session.get(VideoSeries, "series-1")
            series.music_data = b"test worker track"
            series.music_name = "music.mp3"
            run = VideoRun(id="00000000-0000-0000-0000-000000000001", series_id="series-1", status="queued", stage="generate")
            session.add(run)
            await session.commit()
        with TemporaryDirectory() as temp:
            with (
                patch.object(automation_settings, "storage_dir", Path(temp)),
                patch("app.automation.service.cfg.lease_seconds", 30),
            ):
                fake_renderer = types.ModuleType("app.video_renderer")

                async def render(_manifest, output_dir, *, voice, music_path):
                    self.assertEqual(music_path.read_bytes(), b"test worker track")
                    video = output_dir / "video.mp4"
                    thumbnail = output_dir / "thumbnail.jpg"
                    video.write_bytes(b"fake video")
                    thumbnail.write_bytes(b"fake thumbnail")
                    return {"path": str(video), "thumbnail_path": str(thumbnail), "duration_seconds": 40.0,
                            "width": 1080, "height": 1920, "checks": {"full_decode":"passed", "audio":"real_narration"}}

                fake_renderer.render_video = render
                with (
                    patch.object(provider_models, "generate_text", AsyncMock(return_value=json.dumps(manifest)), create=True),
                    patch.object(provider_models, "list_generation_models", lambda: ["fake-model"], create=True),
                    patch("app.video_renderer", fake_renderer, create=True),
                    patch.dict(sys.modules, {"app.video_renderer": fake_renderer}),
                    patch("app.automation.service.youtube.upload_video", new=AsyncMock(side_effect=AssertionError("review must wait"))),
                ):
                    claimed = await self.service.claim_one()
                    await self.service._process(claimed)
            async with self.sessions() as session:
                run = await session.get(VideoRun, "00000000-0000-0000-0000-000000000001")
                events = (await session.execute(select(VideoRunEvent).where(VideoRunEvent.run_id == run.id))).scalars().all()
                media_exists = Path(run.media_path).is_file()
        self.assertEqual(run.status, "review", run.error)
        self.assertEqual(run.stage, "upload")
        self.assertIsNone(run.lease_owner)
        self.assertTrue(media_exists)
        self.assertTrue(any(event.type == "review" for event in events))

    async def test_retry_backoff_survives_a_new_worker(self):
        run_id = "backoff-run"
        async with self.sessions() as session:
            run = VideoRun(id=run_id,series_id="series-1",status="running",stage="generate",
                lease_owner=self.service.worker_id,fence=1,lease_expires_at=utcnow()+timedelta(minutes=10))
            session.add(run)
            await session.commit()
            await self.service._fail(session,run,"Transient provider failure",1,retryable=True)
        restarted = AutomationService(self.sessions)
        self.assertIsNone(await restarted.claim_one())
        async with self.sessions() as session:
            saved = await session.get(VideoRun,run_id)
            due = saved.next_attempt_at.replace(tzinfo=timezone.utc)+timedelta(seconds=1)
        with patch("app.automation.service.utcnow",return_value=due):
            self.assertEqual(await restarted.claim_one(),run_id)

    async def test_processing_resume_checks_existing_video_without_another_upload(self):
        async with self.sessions() as session:
            session.add(VideoRun(id="processing-run",series_id="series-1",status="queued",stage="process",youtube_video_id="already-uploaded"))
            await session.commit()
        with patch("app.automation.service.youtube.processing_status",AsyncMock(return_value="ready")), \
             patch("app.automation.service.youtube.upload_video",AsyncMock()) as upload:
            run_id = await self.service.claim_one()
            await self.service._process(run_id)
            upload.assert_not_awaited()
        async with self.sessions() as session:
            completed = await session.get(VideoRun,"processing-run")
            self.assertEqual((completed.status,completed.stage),("published","complete"))

    async def test_cancellation_closes_an_expensive_stage(self):
        import asyncio
        from app.automation.service import StageCancelled
        stopped = asyncio.Event()
        async def slow_stage():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async with self.sessions() as session:
            session.add(VideoRun(id="cancel-stage",series_id="series-1",status="running",stage="generate",
                lease_owner=self.service.worker_id,fence=1,cancel_requested=True))
            await session.commit()
        with self.assertRaises(StageCancelled):
            await self.service._guard_stage("cancel-stage",1,slow_stage())
        self.assertTrue(stopped.is_set())


if __name__ == "__main__":
    unittest.main()
