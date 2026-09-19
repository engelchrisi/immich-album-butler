import unittest
from datetime import datetime, timedelta, timezone

from immich_album_butler.schedule import ScheduleError, parse

UTC = timezone.utc


def at(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class ParsingTests(unittest.TestCase):
    def test_every_documented_form_parses(self):
        for text, kind in [("manual", "manual"), ("daily 03:30", "daily"),
                           ("weekly sun 04:00", "weekly"),
                           ("monthly 1 05:00", "monthly"),
                           ("every 6h", "interval"), ("every 30m", "interval")]:
            with self.subTest(text=text):
                self.assertEqual(parse(text).kind, kind)

    def test_forms_are_forgiving_about_case_and_spacing(self):
        self.assertEqual(parse("  Daily   03:30 ").kind, "daily")
        self.assertEqual(parse("WEEKLY Sunday 04:00").weekday, 6)
        self.assertEqual(parse("every 6 hours").interval, timedelta(hours=6))

    def test_the_text_is_kept_so_it_round_trips_into_the_config(self):
        self.assertEqual(str(parse("weekly sun 04:00")), "weekly sun 04:00")

    def test_bad_values_say_what_is_wrong(self):
        for text in ["", "hourly", "daily 25:00", "daily 03:70", "weekly xyz 04:00",
                     "monthly 0 05:00", "monthly 32 05:00", "every 0h", "every 1m"]:
            with self.subTest(text=text):
                with self.assertRaises(ScheduleError):
                    parse(text)

    def test_a_rejected_schedule_names_the_alternatives(self):
        with self.assertRaises(ScheduleError) as caught:
            parse("hourly")
        self.assertIn("daily HH:MM", str(caught.exception))


class NextRunTests(unittest.TestCase):
    def test_daily_rolls_to_tomorrow_once_the_time_has_passed(self):
        schedule = parse("daily 03:30")
        self.assertEqual(schedule.next_after(at(2019, 7, 1, 1)), at(2019, 7, 1, 3, 30))
        self.assertEqual(schedule.next_after(at(2019, 7, 1, 4)), at(2019, 7, 2, 3, 30))

    def test_daily_at_exactly_the_scheduled_minute_waits_a_day(self):
        self.assertEqual(parse("daily 03:30").next_after(at(2019, 7, 1, 3, 30)),
                         at(2019, 7, 2, 3, 30))

    def test_weekly_finds_the_next_named_day(self):
        schedule = parse("weekly sun 04:00")           # 2019-07-01 was a Monday
        self.assertEqual(schedule.next_after(at(2019, 7, 1)), at(2019, 7, 7, 4))
        self.assertEqual(schedule.next_after(at(2019, 7, 7, 5)), at(2019, 7, 14, 4))

    def test_monthly_moves_to_the_next_month(self):
        schedule = parse("monthly 15 05:00")
        self.assertEqual(schedule.next_after(at(2019, 7, 1)), at(2019, 7, 15, 5))
        self.assertEqual(schedule.next_after(at(2019, 7, 20)), at(2019, 8, 15, 5))

    def test_monthly_31_clamps_to_a_short_month(self):
        schedule = parse("monthly 31 05:00")
        self.assertEqual(schedule.next_after(at(2019, 2, 1)), at(2019, 2, 28, 5))

    def test_monthly_rolls_over_the_year(self):
        self.assertEqual(parse("monthly 5 05:00").next_after(at(2019, 12, 20)),
                         at(2020, 1, 5, 5))

    def test_interval_counts_from_the_moment_given(self):
        self.assertEqual(parse("every 6h").next_after(at(2019, 7, 1, 1)),
                         at(2019, 7, 1, 7))

    def test_manual_never_comes_due(self):
        self.assertIsNone(parse("manual").next_after(at(2019, 7, 1)))


class DueTests(unittest.TestCase):
    def test_an_album_that_never_ran_is_due_immediately(self):
        self.assertTrue(parse("daily 03:30").is_due(None, at(2019, 7, 1)))

    def test_manual_is_never_due_even_if_it_never_ran(self):
        self.assertFalse(parse("manual").is_due(None, at(2019, 7, 1)))

    def test_due_only_once_the_next_run_has_arrived(self):
        schedule = parse("daily 03:30")
        last = at(2019, 7, 1, 3, 30)
        self.assertFalse(schedule.is_due(last, at(2019, 7, 2, 3, 0)))
        self.assertTrue(schedule.is_due(last, at(2019, 7, 2, 3, 30)))

    def test_a_long_outage_leaves_the_album_due(self):
        self.assertTrue(parse("every 6h").is_due(at(2019, 7, 1), at(2019, 8, 1)))


if __name__ == "__main__":
    unittest.main()
