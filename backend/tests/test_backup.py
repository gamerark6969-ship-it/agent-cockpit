import unittest

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.backup import dump_database, restore_database
from app.models import (
    Approval,
    Artifact,
    Base,
    Event,
    Message,
    Project,
    PushSubscription,
    Setting,
    Task,
    utcnow,
)


def _seeded_rows():
    now = utcnow()
    return {
        Project: [
            Project(id="project-id", name="project", created_at=now),
        ],
        Task: [
            Task(
                id="task-id",
                project_id="project-id",
                prompt="p",
                status="done",
                iterations=3,
                tokens_used=42,
                created_at=now,
                updated_at=now,
            ),
        ],
        Event: [
            Event(id=1, task_id="task-id", seq=1, type="test", payload={"k": "v"}, created_at=now),
            Event(id=2, task_id="task-id", seq=2, type="test", payload={}, created_at=now),
        ],
        Message: [
            Message(id=1, task_id="task-id", seq=1, role="user", content="hello", created_at=now),
        ],
        Approval: [
            Approval(id="approval-id", task_id="task-id", kind="tool", description="d", created_at=now),
        ],
        Artifact: [
            Artifact(
                id="artifact-id",
                task_id="task-id",
                kind="screenshot",
                filename="shot.png",
                mime="image/png",
                size=4,
                data=b"\x89PNG",
                created_at=now,
            ),
        ],
        PushSubscription: [
            PushSubscription(endpoint="https://push", p256dh="p", auth="a", created_at=now),
        ],
        Setting: [Setting(id=1, data={"max_iterations": 120})],
    }


class BackupRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            for model, rows in _seeded_rows().items():
                session.add_all(rows)
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def table_counts(self):
        counts = {}
        async with self.engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                counts[table.name] = await connection.scalar(select(func.count()).select_from(table))
        return counts

    async def test_round_trip_preserves_every_table(self):
        data = await dump_database(self.engine)

        fresh_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            await restore_database(fresh_engine, data)
            counts = {}
            async with fresh_engine.connect() as connection:
                for table in Base.metadata.sorted_tables:
                    counts[table.name] = await connection.scalar(select(func.count()).select_from(table))
            expected = await self.table_counts()
            self.assertEqual(counts, expected)
            self.assertGreater(counts["artifacts"], 0)
            self.assertGreater(counts["events"], 0)
        finally:
            await fresh_engine.dispose()

    async def test_binary_and_datetime_values_survive_round_trip(self):
        data = await dump_database(self.engine)
        artifact_row = next(
            row for row in data["tables"]["artifacts"] if row["id"] == "artifact-id"
        )
        self.assertIn("__b64__", artifact_row["data"])
        self.assertIn("__dt__", artifact_row["created_at"])

        fresh_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            await restore_database(fresh_engine, data)
            async with fresh_engine.connect() as connection:
                row = (
                    (await connection.execute(select(Artifact).where(Artifact.id == "artifact-id")))
                    .mappings()
                    .one()
                )
            self.assertEqual(row["data"], b"\x89PNG")
            self.assertEqual(row["created_at"].isoformat(), artifact_row["created_at"]["__dt__"])
        finally:
            await fresh_engine.dispose()

    async def test_replace_clears_existing_rows_of_backed_up_tables(self):
        data = await dump_database(self.engine)

        fresh_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with fresh_engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                connection.execute  # schema ready
            async with fresh_engine.begin() as connection:
                from sqlalchemy import text

                await connection.execute(
                    text(
                        "INSERT INTO projects (id, name, kind, default_branch, settings, created_at) "
                        "VALUES ('stale-project', 'stale', 'repo', 'main', '{}', CURRENT_TIMESTAMP)"
                    )
                )
            await restore_database(fresh_engine, data, replace=True)
            async with fresh_engine.connect() as connection:
                names = [row[0] for row in (await connection.execute(select(Project.name))).all()]
            self.assertEqual(names, ["project"])
        finally:
            await fresh_engine.dispose()

    async def test_rejects_unknown_backup_format(self):
        with self.assertRaises(ValueError):
            await restore_database(self.engine, {"format": 999, "tables": {}})


if __name__ == "__main__":
    unittest.main()
