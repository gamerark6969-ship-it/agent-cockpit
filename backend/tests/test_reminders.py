import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import reminders
from app.config import settings


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(reminders.Reminder.__table__.create)
        self.addAsyncCleanup(self.engine.dispose)
        self.enterContext(patch.object(reminders, "SessionLocal", self.sessions))
        app = FastAPI()
        app.include_router(reminders.router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)

    async def test_authenticated_lifecycle_and_delivery(self):
        client = self.client
        url = "/api/reminders"
        self.assertEqual((await client.get(url)).status_code, 401)
        client.headers["Authorization"] = f"Bearer {settings.APP_TOKEN}"
        self.assertEqual((await client.post(url, json={"title": "x", "due_at": "2026-01-01T10:00"})).status_code, 422)
        now = datetime.now(timezone.utc)
        async def create(title, due):
            response = await client.post(url, json={"title": title, "due_at": due.isoformat()})
            self.assertEqual(response.status_code, 201)
            return response.json()["id"]
        due_id = await create("Call", now - timedelta(minutes=1))
        await create("Later", now + timedelta(days=2))
        done_id = await create("Completed", now)
        self.assertEqual((await client.post(f"{url}/{done_id}/complete")).status_code, 204)
        push = AsyncMock(side_effect=[False, True])
        with patch.object(reminders, "send_push_to_all", push):
            await reminders.deliver_due()
            await reminders.deliver_due()
            self.assertEqual(push.await_count, 1)
            with patch("app.reminders.time.time", return_value=now.timestamp()+601):
                await reminders.deliver_due()
                await reminders.deliver_due()
            self.assertEqual(push.await_count, 2)
            self.assertEqual(push.await_args.args[0]["reminder_id"], due_id)
        self.assertEqual((await client.delete(f"{url}/{due_id}")).status_code, 204)
        rows = (await client.get(url)).json()
        self.assertEqual([r["title"] for r in rows], ["Later", "Completed"])
        self.assertTrue(rows[1]["done"])
