import unittest
from unittest.mock import create_autospec, patch

from pywebpush import webpush
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, PushSubscription
from app.push import send_push_to_all


class PushDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_uses_the_real_webpush_function_contract(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessions = async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions() as session:
                session.add(PushSubscription(endpoint="https://push.example.test/notification",p256dh="public",auth="auth"))
                await session.commit()
            sender = create_autospec(webpush,return_value=None)
            with patch("app.push.SessionLocal",sessions), patch("app.push.webpush",sender), \
                 patch("app.push.settings.VAPID_PUBLIC_KEY","configured"), patch("app.push.settings.VAPID_PRIVATE_KEY","configured"):
                await send_push_to_all({"type":"video_review","run_id":"run"})
            sender.assert_called_once()
            self.assertEqual(sender.call_args.kwargs["timeout"],10)
        finally:
            await engine.dispose()
