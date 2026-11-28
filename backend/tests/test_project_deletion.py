import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select

from app.api import routes
from app.config import settings
from app.db import create_app_engine
from app.models import Approval, Base, Event, Task
from app.worker import Worker


class ProjectDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_chat_and_repo_cascades_pending_tasks_and_ends_open_stream(self):
        with TemporaryDirectory() as temp:
            engine = create_app_engine("sqlite+aiosqlite:///" + (Path(temp) / "projects.db").as_posix())
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                api = FastAPI()
                api.include_router(routes.router)
                worker = Worker()
                kill = AsyncMock(return_value=True)
                with (
                    patch.object(routes, "SessionLocal", sessions),
                    patch.object(routes, "get_worker", return_value=worker),
                    patch.object(routes.sbx_mod, "kill", kill),
                ):
                    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://localhost",
                                           headers={"Authorization": f"Bearer {settings.APP_TOKEN}"}) as client:
                        for kind in ("chat", "repo"):
                            body = {"kind": kind, "repo_url": "https://example.test/repo"}
                            created = await client.post("/api/projects", json=body)
                            self.assertEqual(created.status_code, 201)
                            path = "/api/projects/" + created.json()["id"]
                            async with sessions() as session:
                                task = Task(project_id=created.json()["id"], prompt="hello", sandbox_id="test-workspace")
                                session.add(task)
                                await session.flush()
                                session.add(Approval(task_id=task.id, description="pending"))
                                session.add(Event(task_id=task.id, seq=1, type="queued"))
                                await session.commit()
                            stream = await routes.stream_events(task.id, after=0)
                            response = await client.delete(path)
                            self.assertEqual(response.status_code, 204, response.text)
                            self.assertIn("event: end", await anext(stream.body_iterator))
                            await stream.body_iterator.aclose()
                            self.assertEqual((await client.get(path)).status_code, 404)
                            self.assertEqual((await client.get("/api/tasks/" + task.id)).status_code, 404)
                            async with sessions() as session:
                                self.assertIsNone(await session.scalar(select(Event.id)))
                                self.assertIsNone(await session.scalar(select(Approval.id)))
                        self.assertEqual(kill.await_count, 2)
            finally:
                await engine.dispose()
