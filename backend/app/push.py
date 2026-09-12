import asyncio
import json

import sqlalchemy as sa
from pywebpush import WebPushException, webpush

from .config import settings
from .db import SessionLocal
from .models import PushSubscription


def _vapid_ready() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


async def send_push_to_all(payload: dict) -> None:
    """Send a web push to every subscriber. Errors are swallowed; 410 removes the sub."""
    if not _vapid_ready():
        return
    data = json.dumps(payload)
    subject = settings.VAPID_SUBJECT or "mailto:admin@example.com"
    dead: list[str] = []
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    sa.select(
                        PushSubscription.endpoint,
                        PushSubscription.p256dh,
                        PushSubscription.auth,
                    )
                )
            ).fetchall()
        for endpoint, p256dh, auth in rows:
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info={
                        "endpoint": endpoint,
                        "keys": {"p256dh": p256dh, "auth": auth},
                    },
                    data=data,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_public_key=settings.VAPID_PUBLIC_KEY,
                    vapid_claims={"sub": subject},
                )
            except WebPushException as exc:
                status = None
                response = getattr(exc, "response", None)
                if response is not None:
                    status = getattr(response, "status_code", None)
                if status in (404, 410):
                    dead.append(endpoint)
            except Exception:
                pass
        if dead:
            async with SessionLocal() as session:
                for endpoint in dead:
                    await session.execute(
                        sa.delete(PushSubscription).where(
                            PushSubscription.endpoint == endpoint
                        )
                    )
                await session.commit()
    except Exception:
        pass


async def push_approval_request(task_id: str, approval_id: str, description: str) -> None:
    await send_push_to_all(
        {
            "type": "approval_request",
            "task_id": task_id,
            "approval_id": approval_id,
            "description": description,
        }
    )


async def push_task_completed(task_id: str, result_summary: str) -> None:
    await send_push_to_all(
        {
            "type": "task_completed",
            "task_id": task_id,
            "result_summary": result_summary,
        }
    )
