import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.automation import api
from app.automation.config import automation_settings
from app.automation.models import VideoRun, VideoSeries
from app.models import Base


class AutomationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.app = FastAPI()
        self.app.include_router(api.public_router)
        self.app.include_router(api.router)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.session_patch = patch.object(api, "SessionLocal", self.sessions)
        self.session_patch.start()
        self.service_sessions_patch = patch.object(api.service, "session_factory", self.sessions)
        self.service_sessions_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        self.service_sessions_patch.stop()
        await self.client.aclose()
        await self.engine.dispose()

    async def test_series_run_status_and_authenticated_contract(self):
        with TemporaryDirectory() as temp, patch.object(automation_settings, "storage_dir", Path(temp)):
            response = await self.client.get("/api/automation/status", headers={"Authorization": "Bearer " + __import__("app.config", fromlist=["settings"]).settings.APP_TOKEN})
            self.assertEqual(response.status_code, 200)
            status = response.json()
            self.assertEqual(set(status), {"youtube", "renderer", "models", "storage", "worker"})
            self.assertFalse(status["youtube"]["connected"])
            self.assertIn("available", status["renderer"])

            missing_auth = await self.client.get("/api/automation/series")
            self.assertEqual(missing_auth.status_code, 401)

            token = __import__("app.config", fromlist=["settings"]).settings.APP_TOKEN
            headers = {"Authorization": f"Bearer {token}"}
            created = await self.client.post(
                "/api/automation/series",
                headers=headers,
                json={
                    "name": "Little discoveries",
                    "brief": "Short animated stories about overlooked acts of kindness.",
                    "timezone": "UTC",
                    "publish_times": ["09:00", "17:30"],
                    "enabled": True,
                    "publish_mode": "review",
                    "language": "en",
                    "voice": "en-US-AriaNeural",
                    "made_for_kids": False,
                    "contains_synthetic_media": False,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            series = created.json()
            self.assertEqual(series["publish_times"], ["09:00", "17:30"])
            self.assertEqual((await self.client.get("/api/automation/series", headers=headers)).json()[0]["id"], series["id"])

            invalid = await self.client.patch(
                f"/api/automation/series/{series['id']}", headers=headers, json={"brief": None}
            )
            self.assertEqual(invalid.status_code, 422)
            patched = await self.client.patch(
                f"/api/automation/series/{series['id']}", headers=headers, json={"model": "test-model"}
            )
            self.assertEqual(patched.status_code, 200)
            self.assertEqual(patched.json()["model"], "test-model")

            started = await self.client.post(f"/api/automation/series/{series['id']}/run", headers=headers)
            self.assertEqual(started.status_code, 201)
            run = started.json()
            self.assertEqual((run["status"], run["stage"], run["series_id"]), ("queued", "generate", series["id"]))
            self.assertEqual((await self.client.get("/api/automation/runs", headers=headers)).json()[0]["id"], run["id"])
            details = await self.client.get(f"/api/automation/runs/{run['id']}", headers=headers)
            self.assertEqual(details.status_code, 200)
            self.assertIn("events", details.json())

    async def test_series_music_replaces_and_clears_durable_track(self):
        from app.config import settings
        from app.video_renderer import _command, _ffmpeg

        headers = {"Authorization": f"Bearer {settings.APP_TOKEN}", "Content-Type": "audio/mpeg"}
        async with self.sessions() as session:
            session.add(VideoSeries(id="music", name="Music", brief="test"))
            await session.commit()
        url = "/api/automation/series/music/music"
        with TemporaryDirectory() as directory:
            track = Path(directory) / "tone.mp3"
            await _command(_ffmpeg(), "-f", "lavfi", "-i", "sine=duration=0.4", str(track))
            content = track.read_bytes()
        for name in ("one.mp3", "two.mp3"):
            result = await self.client.put(url, params={"name": name}, headers=headers, content=content)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["music_name"], name)
        invalid = await self.client.put(url, params={"name": "broken.mp3"}, headers=headers, content=b"invalid")
        self.assertEqual(invalid.status_code, 422)
        from sqlalchemy import select
        async with self.sessions() as session:
            self.assertEqual(await session.scalar(select(VideoSeries.music_data)), content)
        cleared = await self.client.delete(url, headers=headers)
        self.assertIsNone(cleared.json()["music_name"])
        async with self.sessions() as session:
            self.assertIsNone(await session.scalar(select(VideoSeries.music_data)))

    async def test_chart_reads_one_page_in_playlist_position_order(self):
        from app.automation import charts
        from httpx import AsyncClient, MockTransport, Response
        from unittest.mock import AsyncMock

        calls = []
        def fixture(request):
            calls.append(request)
            self.assertEqual(request.url.path, "/youtube/v3/playlistItems")
            self.assertEqual(request.url.params["maxResults"], "50")
            self.assertEqual(request.url.params["playlistId"], "PL1234567890")
            self.assertEqual(request.headers["authorization"], "Bearer fixture")
            return Response(200, json={"nextPageToken":"unused", "items":[
                {"snippet":{"position":5,"resourceId":{"videoId":"bbbbbbbbbbb"}}},
                {"snippet":{"position":0,"resourceId":{"videoId":"aaaaaaaaaaa"}}}]})

        client = AsyncClient(transport=MockTransport(fixture))
        with patch.object(charts.youtube, "_access_token", AsyncMock(return_value="fixture")), patch.object(charts.httpx, "AsyncClient", return_value=client):
            ranking = await charts.read_chart(None, "PL1234567890")
        self.assertEqual(ranking, [(1,"aaaaaaaaaaa"), (6,"bbbbbbbbbbb")])
        self.assertEqual(len(calls), 1)

    async def test_chart_candidate_selection_replacement_and_fallback(self):
        from app.automation import charts
        from app.config import settings
        from unittest.mock import AsyncMock

        headers = {"Authorization": f"Bearer {settings.APP_TOKEN}", "Content-Type": "audio/mpeg"}
        async with self.sessions() as session:
            session.add(VideoSeries(id="chart", name="Chart", brief="test", music_name="base.mp3", music_data=b"base"))
            await session.commit()
        base = "/api/automation/series/chart/chart"
        top = "12345678901"
        second = "abcdefghijk"
        enabled = await self.client.put(base, params={"playlist":"https://music.youtube.com/playlist?list=PL1234567890"}, headers=headers)
        self.assertEqual(enabled.json()["playlist_id"], "PL1234567890")
        with patch("app.video_renderer.validate_music", new_callable=AsyncMock):
            for slot, video, content in [(1, second, b"second"), (2, top, b"old"), (2, top, b"new")]:
                saved = await self.client.put(f"{base}/{slot}", params={"video_id":video, "name":f"{slot}.mp3"},
                                              content=content, headers=headers)
                self.assertEqual(saved.status_code, 200, saved.text)
            duplicate = await self.client.put(f"{base}/3", params={"video_id":top, "name":"copy"}, content=b"copy", headers=headers)
            self.assertEqual(duplicate.status_code, 409)
            full = await self.client.put(f"{base}/4", params={"video_id":top, "name":"copy"}, content=b"copy", headers=headers)
            self.assertEqual(full.status_code, 422)
        async with self.sessions() as session:
            with patch.object(charts, "read_chart", AsyncMock(return_value=[(2,top),(8,second)])):
                data, message = await charts.select_music(session, "chart")
                self.assertEqual(data, b"new")
                self.assertIn("#2", message)
            with patch.object(charts, "read_chart", AsyncMock(return_value=[])):
                data, message = await charts.select_music(session, "chart")
                self.assertEqual(data, b"base")
                self.assertIn("No licensed match", message)
            data, message = await charts.select_music(session, "chart")
            self.assertEqual(data, b"base")
            self.assertIn("Chart unavailable", message)
        disabled = await self.client.put(base, params={"playlist":""}, headers=headers)
        self.assertIsNone(disabled.json()["playlist_id"])
        self.assertEqual(len(disabled.json()["candidates"]), 2)
        removed = await self.client.delete(base+"/2", headers=headers)
        self.assertEqual([r["slot"] for r in removed.json()["candidates"]], [1])
        await self.client.delete("/api/automation/series/chart/music", headers=headers)
        async with self.sessions() as session:
            with patch.object(charts, "read_chart", AsyncMock(side_effect=AssertionError("Disabled chart read"))):
                self.assertEqual(await charts.select_music(session, "chart"), (None, "Using generated score."))

    async def test_review_upload_actions_and_unknown_ids(self):
        async with self.sessions() as session:
            series = VideoSeries(
                id="api-series", name="Series", brief="A distinct enough short animation series.",
                timezone="UTC", publish_times=["09:00"], enabled=True, publish_mode="review",
                language="en", voice="en-US-AriaNeural", made_for_kids=False, contains_synthetic_media=False,
            )
            session.add(series)
            session.add(VideoRun(id="api-review", series_id="api-series", status="review", stage="upload"))
            await session.commit()
        token = __import__("app.config", fromlist=["settings"]).settings.APP_TOKEN
        headers = {"Authorization": f"Bearer {token}"}
        approved = await self.client.post("/api/automation/runs/api-review/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual((approved.json()["status"], approved.json()["stage"]), ("queued", "upload"))
        missing = await self.client.post("/api/automation/runs/missing/retry", headers=headers)
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
