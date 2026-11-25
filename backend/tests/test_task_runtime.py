import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.config import Settings
from app.models import Task
from app.worker import Worker


class FakeSession:
    def __init__(self, task):
        self.task = task
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, model, object_id):
        return self.task

    async def commit(self):
        self.commits += 1


class TaskRuntimeConfigurationTests(unittest.TestCase):
    def test_default_and_positive_validation(self):
        self.assertEqual(Settings().MAX_TASK_RUNTIME_S, 1800)
        self.assertEqual(Settings(MAX_TASK_RUNTIME_S=1).MAX_TASK_RUNTIME_S, 1)
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Settings(MAX_TASK_RUNTIME_S=value)


class TaskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_persists_failure_event_and_error(self):
        task = Task(id="task-id", project_id="project-id", prompt="test", status="running")
        session = FakeSession(task)
        append_event = AsyncMock()
        worker = Worker()

        with (
            patch("app.worker.SessionLocal", return_value=session),
            patch("app.events.append_event", append_event),
            patch("app.worker.settings.MAX_TASK_RUNTIME_S", 7),
        ):
            await worker._mark_timed_out("task-id")

        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error, "task exceeded the maximum runtime of 7 seconds")
        self.assertEqual(session.commits, 1)
        append_event.assert_awaited_once_with(
            session,
            "task-id",
            "task_failed",
            {
                "error": "task exceeded the maximum runtime of 7 seconds",
                "reason": "runtime_timeout",
            },
        )

    async def test_terminal_task_is_not_reclassified_as_timeout(self):
        task = Task(id="task-id", project_id="project-id", prompt="test", status="stopped")
        session = FakeSession(task)
        append_event = AsyncMock()
        worker = Worker()

        with (
            patch("app.worker.SessionLocal", return_value=session),
            patch("app.events.append_event", append_event),
        ):
            await worker._mark_timed_out("task-id")

        self.assertEqual(task.status, "stopped")
        self.assertEqual(session.commits, 0)
        append_event.assert_not_awaited()

    async def test_successful_task_below_limit_is_not_timed_out(self):
        worker = Worker()
        worker._acquire_lease = AsyncMock(return_value=True)
        worker._release_lease = AsyncMock()
        completed = asyncio.Event()

        async def run_agent_loop(task_id, active_worker):
            completed.set()

        worker._mark_timed_out = AsyncMock()
        with (
            patch("app.agent.pa_loop.run_agent_loop", run_agent_loop),
            patch("app.worker.settings.MAX_TASK_RUNTIME_S", 1),
        ):
            await worker.start()
            worker.enqueue("task-id")
            await asyncio.wait_for(completed.wait(), timeout=1)
            while worker.is_active("task-id"):
                await asyncio.sleep(0)
            await worker.stop()

        worker._mark_timed_out.assert_not_awaited()
        worker._acquire_lease.assert_awaited_once_with("task-id")
        worker._release_lease.assert_awaited_once_with("task-id")

    async def test_timeout_cancels_agent_after_terminalization_for_cleanup(self):
        worker = Worker()
        worker._acquire_lease = AsyncMock(return_value=True)
        worker._release_lease = AsyncMock()
        cleanup_observed = asyncio.Event()
        terminalized = asyncio.Event()

        async def run_agent_loop(task_id, active_worker):
            try:
                await asyncio.Event().wait()
            finally:
                if terminalized.is_set():
                    cleanup_observed.set()

        async def mark_timed_out(task_id):
            terminalized.set()

        worker._mark_timed_out = AsyncMock(side_effect=mark_timed_out)
        with (
            patch("app.agent.pa_loop.run_agent_loop", run_agent_loop),
            patch("app.worker.settings.MAX_TASK_RUNTIME_S", 0.01),
        ):
            await worker.start()
            worker.enqueue("task-id")
            await asyncio.wait_for(cleanup_observed.wait(), timeout=1)
            await worker.stop()

        worker._mark_timed_out.assert_awaited_once_with("task-id")
        worker._acquire_lease.assert_awaited_once_with("task-id")
        worker._release_lease.assert_awaited_once_with("task-id")


if __name__ == "__main__":
    unittest.main()
