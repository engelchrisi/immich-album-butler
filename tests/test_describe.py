import datetime as dt
import unittest

from immich_album_butler.config import Album, MatchRule
from immich_album_butler.describe import apply_hint, hint_line
from immich_album_butler.schedule import parse as parse_schedule


def album(rule: MatchRule, *, pick: str = "all",
          pics_per_year: int | None = None) -> Album:
    return Album(slug="x", name="X", match=rule,
                 schedule=parse_schedule("weekly sun 04:00"),
                 pick=pick, pics_per_year=pics_per_year)


class HintLineTests(unittest.TestCase):
    def test_a_dated_window_is_a_trip(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 21))
        self.assertEqual(hint_line(album(rule)), "[butler v1] kind=trip order=trip")

    def test_a_dated_window_with_people_is_still_a_trip(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), people=("Alex",))
        self.assertEqual(hint_line(album(rule)), "[butler v1] kind=trip order=trip")

    def test_people_only_is_a_person(self):
        self.assertEqual(hint_line(album(MatchRule(people=("Alex",)))),
                         "[butler v1] kind=person order=person")

    def test_a_recurring_day_is_recurring_day(self):
        self.assertEqual(hint_line(album(MatchRule(on="05-17"))),
                         "[butler v1] kind=recurring-day order=one-per-year")

    def test_places_only_have_no_order(self):
        self.assertEqual(hint_line(album(MatchRule(countries=("Italy",)))),
                         "[butler v1] kind=place")

    def test_rotating_overrides_everything(self):
        rule = MatchRule(on="05-17")
        line = hint_line(album(rule, pick="rotate", pics_per_year=5))
        self.assertEqual(
            line, "[butler v1] kind=recurring-day order=one-per-year rotating=yes")


class ApplyHintTests(unittest.TestCase):
    LINE = "[butler v1] kind=trip order=trip"

    def test_an_empty_description_becomes_the_hint(self):
        self.assertEqual(apply_hint("", self.LINE, True), self.LINE)

    def test_a_hand_written_line_is_preserved_above_the_hint(self):
        out = apply_hint("Our two weeks in Tuscany.", self.LINE, True)
        self.assertEqual(out, "Our two weeks in Tuscany.\n\n" + self.LINE)

    def test_the_hint_is_replaced_in_place(self):
        old = "Note.\n\n[butler v1] kind=person order=person"
        out = apply_hint(old, self.LINE, True)
        self.assertEqual(out, "Note.\n\n" + self.LINE)

    def test_other_lines_survive_a_replace(self):
        old = "Line one.\n[butler v1] kind=person order=person\nLine three."
        out = apply_hint(old, self.LINE, True)
        self.assertIn("Line one.", out)
        self.assertIn("Line three.", out)
        self.assertIn(self.LINE, out)

    def test_off_removes_the_line(self):
        old = "Our trip.\n\n[butler v1] kind=trip order=trip"
        self.assertEqual(apply_hint(old, self.LINE, False), "Our trip.")

    def test_off_with_no_line_changes_nothing(self):
        self.assertEqual(apply_hint("Just a note.", self.LINE, False), "Just a note.")


if __name__ == "__main__":
    unittest.main()
