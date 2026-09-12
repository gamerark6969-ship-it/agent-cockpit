import asyncio
from typing import Dict, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event, Task, new_id, utcnow

# In-memory broadcast: task_id -> set of asyncio.Event flags set when new events land.
_subscribers: Dict[str, Set[asyncio.Event]] = {}
_lock = asyncio.Lock()


def _notify(task_id: str) -> None:
    for ev in _subscribers.get(task_id, ()):  # snapshot-safe: iterating a live set is fine for additions
        ev.set()


async def subscribe(task_id: str) -> asyncio.Event:
    flag = asyncio.Event()
    async with _lock:
        _subscribers.setdefault(task_id, set()).add(flag)
    return flag


async def unsubscribe(task_id: str, flag: asyncio.Event) -> None:
    async with _lock:
        subs = _subscribers.get(task_id)
        if subs is not None:
            subs.discard(flag)
            if not subs:
                _subscribers.pop(task_id, None)


class SequenceConflict(Exception):
    pass


async def append_event(
    session: AsyncSession,
    task_id: str,
    type: str,
    payload: dict | None = None,
) -> Event:
    """Append an event with a per-task strictly increasing seq. Retries on conflicts."""
    payload = payload or {}
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            row = (
                await session.execute(
                    select(Task).where(Task.id == task_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"task {task_id} not found")
            max_seq = (
                await session.execute(
                    select(func.max(Event.seq)).where(Event.task_id == task_id)
                )
            ).scalar_one()
            event = Event(
                id=None,
                task_id=task_id,
                seq=(max_seq or 0) + 1,
                type=type,
                payload=payload,
                created_at=utcnow(),
            )
            session.add(event)
            await session.flush()
            await session.commit()
            _notify(task_id)
            return event
        except ValueError:
            raise
        except SequenceConflict:
            last_err = SequenceConflict("seq conflict")
            await session.rollback()
        except Exception as exc:  # IntegrityError / concurrent write / db lock
            last_err = exc
            await session.rollback()
            await asyncio.sleep(0.05 * (attempt + 1))
    raise last_err if last_err else RuntimeError("append_event failed")
