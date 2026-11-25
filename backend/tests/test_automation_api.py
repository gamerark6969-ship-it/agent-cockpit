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
