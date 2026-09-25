"""Calendar boundaries, local midnight and missing/future dates."""
from datetime import date, datetime, timedelta, timezone
import unittest
from viewer.date_groups import date_group, message_date_label

UTC = timezone.utc


def stamp(day):
    return datetime.combine(day, datetime.min.time(), UTC).timestamp()


class DateGroupTests(unittest.TestCase):
    def test_requested_week_and_month_groups_have_disjoint_boundaries(self):
        today = date(2026, 9, 30)  # Wednesday, week starts Monday 28th.
        samples = {
            date(2026, 10, 1): 'Future',
            today: 'Today',
            date(2026, 9, 29): 'Yesterday',
            date(2026, 9, 28): 'This Week',
            date(2026, 9, 27): 'Last Week',
            date(2026, 9, 21): 'Last Week',
            date(2026, 9, 20): 'Two Weeks Ago',
            date(2026, 9, 14): 'Two Weeks Ago',
            date(2026, 9, 13): 'Three Weeks Ago',
            date(2026, 9, 7): 'Three Weeks Ago',
            date(2026, 9, 6): 'Earlier This Month',
            date(2026, 9, 1): 'Earlier This Month',
            date(2026, 8, 31): 'Last Month',
            date(2026, 8, 1): 'Last Month',
            date(2026, 7, 31): 'Older',
        }
        for day, expected in samples.items():
            with self.subTest(day=day):
                self.assertEqual(date_group(stamp(day), today=today, tz=UTC), expected)
        self.assertEqual(date_group(None, today=today), 'Unknown Date')
        self.assertEqual(date_group(float('inf'), today=today), 'Unknown Date')

    def test_monday_and_sunday_week_starts_yesterday_wins_across_week_boundary(self):
        today = date(2026, 9, 23)  # Wednesday.
        sunday = stamp(date(2026, 9, 20))
        self.assertEqual(date_group(sunday, today=today, first_weekday=0, tz=UTC), 'Last Week')
        self.assertEqual(date_group(sunday, today=today, first_weekday=6, tz=UTC), 'This Week')
        self.assertEqual(date_group(sunday, today=date(2026, 9, 21), tz=UTC), 'Yesterday')

    def test_year_boundary_and_leap_day_use_calendar_months(self):
        self.assertEqual(date_group(stamp(date(2025, 12, 1)), today=date(2026, 1, 2), tz=UTC), 'Last Month')
        self.assertEqual(date_group(stamp(date(2024, 2, 29)), today=date(2024, 3, 1), tz=UTC), 'Yesterday')
        self.assertEqual(date_group(stamp(date(2024, 2, 1)), today=date(2024, 3, 1), tz=UTC), 'Last Month')

    def test_local_midnight_and_date_labels_agree(self):
        offset = timezone(timedelta(hours=1))
        timestamp = datetime(2026, 9, 24, 23, 30, tzinfo=UTC).timestamp()
        self.assertEqual(date_group(timestamp, today=date(2026, 9, 25), tz=offset), 'Today')
        self.assertEqual(message_date_label(timestamp, today=date(2026, 9, 25), tz=offset), '00:30')
        self.assertEqual(date_group(timestamp, today=date(2026, 9, 25), tz=UTC), 'Yesterday')
        self.assertEqual(message_date_label(None), 'No date')
        self.assertEqual(message_date_label(stamp(date(2025, 2, 2)), today=date(2026, 9, 25), tz=UTC), '02 Feb 2025')
