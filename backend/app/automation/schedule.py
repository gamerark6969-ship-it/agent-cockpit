"""Timezone-aware daily slot construction with deterministic DST behavior."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_slot_utc(day: date, hhmm: str, zone_name: str) -> datetime:
    """Resolve a local wall-clock slot to UTC.

    Ambiguous wall times choose the earlier occurrence (fold 0). A nonexistent
    wall time during a spring-forward gap moves forward to the first valid
    local minute, making the result deterministic and never silently dropping
    a configured daily slot.
    """
    hour, minute = map(int, hhmm.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    zone = ZoneInfo(zone_name)
    for offset in range(181):
        candidate = naive + timedelta(minutes=offset)
        aware = candidate.replace(tzinfo=zone, fold=0)
        roundtrip = aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip == candidate:
            return aware.astimezone(timezone.utc)
    raise ValueError(f"could not resolve local slot {hhmm} in {zone_name}")


def scheduled_slots(series, start_utc: datetime, end_utc: datetime) -> list[datetime]:
    """Return unique UTC slots in [start, end], in chronological order."""
    start = start_utc.astimezone(timezone.utc)
    end = end_utc.astimezone(timezone.utc)
    if end < start:
        return []
    zone = ZoneInfo(series.timezone)
    first_day = start.astimezone(zone).date() - timedelta(days=1)
    last_day = end.astimezone(zone).date() + timedelta(days=1)
    day = first_day
    slots: set[datetime] = set()
    while day <= last_day:
        for hhmm in series.publish_times:
            slot = local_slot_utc(day, hhmm, series.timezone)
            if start <= slot <= end:
                slots.add(slot)
        day += timedelta(days=1)
    return sorted(slots)
