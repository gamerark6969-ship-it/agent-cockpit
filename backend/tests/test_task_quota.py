import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api import routes
from app.config import Settings
from app.models import Project
from app.schemas import TaskCreate


class FakeSession:
    def __init__(self, active_count):
        self.active_count = active_count
        self.added = []
        self.count_statement = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, model, object_id):
        return Project(id=object_id, name="project")

    async def scalar(self, statement):
        self.count_statement = statement
        return self.active_count

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        for value in self.added:
            if value.id is None:
                value.id = "task-id"
            if value.iterations is None:
                value.iterations = 0
            if value.tokens_used is None:
                value.tokens_used = 0
            if value.created_at is None:
                value.created_at = datetime.now(timezone.utc)
            if value.updated_at is None:
                value.updated_at = datetime.now(timezone.utc)

    async def refresh(self, value):
        return None


class TaskQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, active_count, limit):
        session = FakeSession(active_count)
        worker = Mock()
        with (
            patch.object(routes, "SessionLocal", return_value=session),
            patch.object(routes, "get_settings_data", AsyncMock(return_value={})),
            patch.object(routes, "get_worker", return_value=worker),
            patch.object(routes.settings, "MAX_ACTIVE_TASKS", limit),
        ):
            result = await routes.create_task(
                "project-id", TaskCreate(prompt="test task")
            )
        return session, worker, result

    async def test_allows_creation_below_limit(self):
        session, worker, result = await self.create(active_count=1, limit=2)

        self.assertEqual(result.status, "queued")
        self.assertEqual(len(session.added), 1)
        worker.enqueue.assert_called_once_with("task-id")

    async def test_rejects_creation_at_limit(self):
        session = FakeSession(active_count=2)
        worker = Mock()
        with (
            patch.object(routes, "SessionLocal", return_value=session),
            patch.object(routes, "get_settings_data", AsyncMock(return_value={})),
            patch.object(routes, "get_worker", return_value=worker),
            patch.object(routes.settings, "MAX_ACTIVE_TASKS", 2),
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes.create_task(
                    "project-id", TaskCreate(prompt="test task")
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("maximum active-task limit reached", raised.exception.detail)
        self.assertEqual(session.added, [])
        worker.enqueue.assert_not_called()

    async def test_terminal_tasks_do_not_count(self):
        session, _, result = await self.create(active_count=0, limit=1)
        sql = str(
            session.count_statement.compile(compile_kwargs={"literal_binds": True})
        )

        self.assertEqual(result.status, "queued")
        for status in routes.ACTIVE_STATUSES:
            self.assertIn(status, sql)
        for status in routes.TERMINAL_STATUSES:
            self.assertNotIn(status, sql)

    def test_invalid_max_active_tasks_configuration(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Settings(MAX_ACTIVE_TASKS=value)


if __name__ == "__main__":
    unittest.main()
