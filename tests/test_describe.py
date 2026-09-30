import datetime as dt
import re
import unittest
from pathlib import Path

from immich_album_butler import describe
from immich_album_butler.config import Album, MatchRule
from immich_album_butler.describe import apply_hint, hint_line
from immich_album_butler.schedule import parse as parse_schedule


def album(rule: MatchRule, *, pick: str = "all",
          pics_per_year: int | None = None, hint_kind: str = "",
          hint_order: str = "", slot: str = "", dwell: int | None = None,
          active: str = "", caption: str = "", activity: str = "") -> Album:
    return Album(slug="x", name="X", match=rule,
                 schedule=parse_schedule("weekly sun 04:00"),
                 pick=pick, pics_per_year=pics_per_year,
                 hint_kind=hint_kind, hint_order=hint_order, slot=slot,
                 dwell=dwell, active=active, caption=caption, activity=activity)


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
        self.assertEqual(hint_line(album(MatchRule(on_from="05-17", on_to="05-17"))),
                         "[butler v1] kind=recurring-day order=one-per-year")

    def test_places_only_have_no_order(self):
        self.assertEqual(hint_line(album(MatchRule(countries=("Italy",)))),
                         "[butler v1] kind=place")

    def test_rotating_overrides_everything(self):
        rule = MatchRule(on_from="05-17", on_to="05-17")
        line = hint_line(album(rule, pick="rotate", pics_per_year=5))
        self.assertEqual(
            line, "[butler v1] kind=recurring-day order=one-per-year rotating=yes")


class HintOverrideTests(unittest.TestCase):
    def test_hint_kind_overrides_the_derived_kind(self):
        rule = MatchRule(people=("Alex",))
        line = hint_line(album(rule, hint_kind="place"))
        self.assertEqual(line, "[butler v1] kind=place order=person")

    def test_hint_order_overrides_the_derived_order(self):
        rule = MatchRule(people=("Alex",))
        line = hint_line(album(rule, hint_order="slots"))
        self.assertEqual(line, "[butler v1] kind=person order=slots")

    def test_places_with_an_order_override_get_one(self):
        rule = MatchRule(countries=("Italy",))
        line = hint_line(album(rule, hint_order="random"))
        self.assertEqual(line, "[butler v1] kind=place order=random")

    def test_playback_fields_are_appended_in_order(self):
        rule = MatchRule(people=("Alex",))
        line = hint_line(album(rule, slot="2-3", dwell=8, active="12-01..12-31",
                                caption="year", activity="kenburns"))
        self.assertEqual(
            line,
            "[butler v1] kind=person order=person slot=2-3 dwell=8 "
            "active=12-01..12-31 caption=year activity=kenburns")

    def test_unset_playback_fields_are_omitted(self):
        rule = MatchRule(people=("Alex",))
        line = hint_line(album(rule))
        self.assertEqual(line, "[butler v1] kind=person order=person")


class ClosedVocabularyTests(unittest.TestCase):
    """No value can reach a description that isn't in describe.py's registry.

    Also guards docs/hints.md: every registry value must be documented there,
    so a value cannot be added to the vocabulary without documenting it.
    """

    def test_hint_line_refuses_an_unregistered_kind(self):
        rule = MatchRule(people=("Alex",))
        with self.assertRaises(AssertionError):
            hint_line(album(rule, hint_kind="vacation"))

    def test_hint_line_refuses_an_unregistered_order(self):
        rule = MatchRule(people=("Alex",))
        with self.assertRaises(AssertionError):
            hint_line(album(rule, hint_order="backwards"))

    def test_every_registry_value_is_documented(self):
        docs = Path(__file__).resolve().parent.parent / "docs" / "hints.md"
        text = docs.read_text(encoding="utf-8")
        for value in (*describe.KIND_VALUES, *describe.ORDER_VALUES,
                      *describe.CAPTION_VALUES, *describe.ACTIVITY_VALUES):
            self.assertIn(f"`{value}`", text,
                          f"{value!r} is in describe.py's registry but not "
                          f"documented in docs/hints.md")


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
