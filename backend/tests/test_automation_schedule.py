import unittest
from datetime import date, datetime, timedelta, timezone

from app.automation.schedule import local_slot_utc, scheduled_slots
from app.automation.schemas import SeriesInput


class ScheduleTests(unittest.TestCase):
    def test_fall_back_ambiguous_time_chooses_first_occurrence(self):
        slot = local_slot_utc(date(2026, 11, 1), "01:30", "America/New_York")
        self.assertEqual(slot, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))

    def test_spring_forward_gap_advances_to_first_valid_local_minute(self):
        slot = local_slot_utc(date(2026, 3, 8), "02:30", "America/New_York")
        self.assertEqual(slot, datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc))

    def test_slots_are_unique_chronological_and_bounded(self):
        series = SeriesInput(
            name="Daily",
            brief="A small animated history and mystery series.",
            timezone="UTC",
            publish_times=["09:00", "21:00"],
        )
        start = datetime(2026, 5, 1, 8, 59, tzinfo=timezone.utc)
        slots = scheduled_slots(series, start, start + timedelta(hours=24))
        self.assertEqual(slots, [datetime(2026, 5, 1, 9, tzinfo=timezone.utc), datetime(2026, 5, 1, 21, tzinfo=timezone.utc)])
        self.assertEqual(len(slots), len(set(slots)))

    def test_schedule_validation_requires_valid_zone_and_unique_times(self):
        common = {"name": "x", "brief": "A long enough creative series brief"}
        with self.assertRaises(ValueError):
            SeriesInput(**common, timezone="Mars/Olympus")
        with self.assertRaises(ValueError):
            SeriesInput(**common, publish_times=["09:00", "09:00"])
        with self.assertRaises(ValueError):
            SeriesInput(**common, publish_times=["25:00"])


if __name__ == "__main__":
    unittest.main()
