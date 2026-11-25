import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes
from app.config import Settings
from app.events import append_event
from app.models import Base, Event, Project, Task


class EventRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add(Project(id="project-id", name="project"))
            session.add_all(
                [
                    Task(id="task-a", project_id="project-id", prompt="a"),
                    Task(id="task-b", project_id="project-id", prompt="b"),
                ]
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def event_sequences(self, task_id):
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(Event)
                    .where(Event.task_id == task_id)
                    .order_by(Event.seq)
                )
            ).scalars().all()
        return [row.seq for row in rows]

    async def append(self, task_id, count, limit):
        with patch("app.events.settings.MAX_EVENTS_PER_TASK", limit):
            async with self.sessions() as session:
                returned = None
                for index in range(count):
                    returned = await append_event(
                        session, task_id, "test", {"index": index}
                    )
        return returned

    def test_invalid_max_events_per_task_configuration(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Settings(MAX_EVENTS_PER_TASK=value)

    async def test_prunes_oldest_events_and_returns_current_event(self):
        returned = await self.append("task-a", count=5, limit=3)

        self.assertEqual(returned.seq, 5)
        self.assertEqual(await self.event_sequences("task-a"), [3, 4, 5])

    async def test_pruning_is_isolated_by_task(self):
        await self.append("task-b", count=2, limit=3)
        await self.append("task-a", count=4, limit=2)

        self.assertEqual(await self.event_sequences("task-a"), [3, 4])
        self.assertEqual(await self.event_sequences("task-b"), [1, 2])

    async def test_keeps_all_events_below_limit(self):
        await self.append("task-a", count=2, limit=3)

        self.assertEqual(await self.event_sequences("task-a"), [1, 2])

    async def test_list_events_returns_retained_history_after_sequence(self):
        await self.append("task-a", count=5, limit=3)

        with patch.object(routes, "SessionLocal", self.sessions):
            retained = await routes.list_events("task-a", after=0, limit=500)
            resumed = await routes.list_events("task-a", after=3, limit=500)

        self.assertEqual([event.seq for event in retained], [3, 4, 5])
        self.assertEqual([event.seq for event in resumed], [4, 5])


if __name__ == "__main__":
    unittest.main()
