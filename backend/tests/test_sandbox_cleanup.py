import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Project, Task, utcnow
from app.worker import Worker


class SandboxCleanupConfigurationTests(unittest.TestCase):
    def test_defaults_and_positive_validation(self):
        settings = Settings()
        self.assertEqual(settings.SANDBOX_IDLE_TIMEOUT_S, 900)
        self.assertEqual(settings.SANDBOX_CLEANUP_INTERVAL_S, 60)
        for name in ("SANDBOX_IDLE_TIMEOUT_S", "SANDBOX_CLEANUP_INTERVAL_S"):
            for value in (0, -1):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValidationError):
                        Settings(**{name: value})


class SandboxCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add(Project(id="project-id", name="project"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def add_task(self, task_id, status, sandbox_id, age_seconds):
        async with self.sessions() as session:
            session.add(
                Task(
                    id=task_id,
                    project_id="project-id",
                    prompt="test",
                    status=status,
                    sandbox_id=sandbox_id,
                    updated_at=utcnow() - timedelta(seconds=age_seconds),
                )
            )
            await session.commit()

    async def get_task(self, task_id):
        async with self.sessions() as session:
            return await session.get(Task, task_id)

    async def test_cleans_stale_inactive_sandbox(self):
        await self.add_task("stale", "done", "sandbox-stale", 1000)
        worker = Worker()
        kill = AsyncMock(return_value=True)
        with (
            patch("app.worker.SessionLocal", self.sessions),
            patch("app.sandbox.kill", kill),
            patch("app.worker.settings.SANDBOX_IDLE_TIMEOUT_S", 900),
        ):
            cleaned = await worker.cleanup_idle_sandboxes()

        self.assertEqual(cleaned, 1)
        kill.assert_awaited_once_with("sandbox-stale")
        self.assertIsNone((await self.get_task("stale")).sandbox_id)

    async def test_does_not_clean_active_or_recent_sandboxes(self):
        await self.add_task("active", "running", "sandbox-active", 1000)
        await self.add_task("recent", "done", "sandbox-recent", 10)
        worker = Worker()
        kill = AsyncMock(return_value=True)
        with (
            patch("app.worker.SessionLocal", self.sessions),
            patch("app.sandbox.kill", kill),
            patch("app.worker.settings.SANDBOX_IDLE_TIMEOUT_S", 900),
        ):
            cleaned = await worker.cleanup_idle_sandboxes()

        self.assertEqual(cleaned, 0)
        kill.assert_not_awaited()

    async def test_keeps_identifier_when_termination_fails(self):
        await self.add_task("stale", "failed", "sandbox-stale", 1000)
        worker = Worker()
        with (
            patch("app.worker.SessionLocal", self.sessions),
            patch("app.sandbox.kill", AsyncMock(return_value=False)),
            patch("app.worker.settings.SANDBOX_IDLE_TIMEOUT_S", 900),
        ):
            cleaned = await worker.cleanup_idle_sandboxes()

        self.assertEqual(cleaned, 0)
        self.assertEqual((await self.get_task("stale")).sandbox_id, "sandbox-stale")


if __name__ == "__main__":
    unittest.main()
