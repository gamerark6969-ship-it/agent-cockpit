from datetime import datetime, timezone
import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, StringConstraints
from typing import Annotated
from sqlalchemy import delete, select, update

from .db import SessionLocal
from .models import Reminder
from .push import send_push_to_all
from .security import require_token

router = APIRouter(prefix="/api/reminders", dependencies=[Depends(require_token)])


class ReminderInput(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    due_at: AwareDatetime


def serialize(row):
    return {"id": row.id, "title": row.title, "due_at": datetime.fromtimestamp(row.due, timezone.utc), "done": row.done}


@router.get("")
async def list_reminders():
    async with SessionLocal() as session:
        rows = await session.scalars(select(Reminder).order_by(Reminder.done, Reminder.due, Reminder.id))
        return [serialize(row) for row in rows]


@router.post("", status_code=201)
async def create_reminder(body: ReminderInput):
    due = int(body.due_at.timestamp())
    row = Reminder(title=body.title, due=due, next_attempt=due)
    async with SessionLocal() as session:
        session.add(row)
        await session.commit()
        return serialize(row)


@router.post("/{reminder_id}/complete", status_code=204)
async def complete_reminder(reminder_id: str):
    async with SessionLocal() as session:
        result = await session.execute(update(Reminder).where(Reminder.id == reminder_id).values(done=True, next_attempt=None))
        if not result.rowcount:
            raise HTTPException(404, "Reminder not found")
        await session.commit()


@router.delete("/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: str):
    async with SessionLocal() as session:
        result = await session.execute(delete(Reminder).where(Reminder.id == reminder_id))
        if not result.rowcount:
            raise HTTPException(404, "Reminder not found")
        await session.commit()


async def deliver_due():
    now = int(time.time())
    async with SessionLocal() as session:
        ids = list(await session.scalars(select(Reminder.id).where(Reminder.next_attempt <= now)
                                        .order_by(Reminder.next_attempt, Reminder.id).limit(100)))
    for reminder_id in ids:
        lease_until = int(time.time()) + 600
        async with SessionLocal() as session:
            title = await session.scalar(update(Reminder).where(Reminder.id == reminder_id, Reminder.next_attempt <= now)
                                         .values(next_attempt=lease_until).returning(Reminder.title))
            await session.commit()
        if title is None:
            continue
        if await send_push_to_all({"type": "reminder", "reminder_id": reminder_id, "description": title}):
            async with SessionLocal() as session:
                await session.execute(update(Reminder).where(Reminder.id == reminder_id, Reminder.next_attempt == lease_until)
                                      .values(next_attempt=None))
                await session.commit()


async def reminder_loop():
    while True:
        try:
            await deliver_due()
        except Exception:
            logging.getLogger(__name__).exception("Reminder delivery failed")
        await asyncio.sleep(30)
