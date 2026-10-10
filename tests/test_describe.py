import datetime as dt
import unittest
from pathlib import Path

from immich_album_butler import describe
from immich_album_butler.config import Album, MatchRule
from immich_album_butler.describe import apply_hint, hint_line, parse_chunk
from immich_album_butler.schedule import parse as parse_schedule


def album(*, chunk: str = "", chunk_order: str = "",
          rule: MatchRule | None = None) -> Album:
    rule = rule or MatchRule(people=("Alex",))
    return Album(slug="x", name="X", match=rule,
                 schedule=parse_schedule("weekly sun 04:00"),
                 chunk=chunk, chunk_order=chunk_order)


class HintLineTests(unittest.TestCase):
    def test_an_album_without_a_chunk_has_no_line(self):
        self.assertEqual(hint_line(album()), "")

    def test_the_rule_shape_no_longer_matters(self):
        for rule in (MatchRule(from_date=dt.date(2019, 7, 1)),
                     MatchRule(on_from="05-17", on_to="05-17"),
                     MatchRule(countries=("Italy",))):
            self.assertEqual(hint_line(album(rule=rule)), "")

    def test_a_chunk_and_its_order(self):
        self.assertEqual(hint_line(album(chunk="3/day", chunk_order="random")),
                         "[butler v1] chunk=3/day chunk_order=random")

    def test_a_chunk_without_an_order_is_chronological(self):
        self.assertEqual(hint_line(album(chunk="3/year")),
                         "[butler v1] chunk=3/year chunk_order=chronological")

    def test_nothing_but_chunk_and_chunk_order_is_written(self):
        line = hint_line(album(chunk="5/month", chunk_order="chronological"))
        keys = [part.split("=")[0] for part in line.split()[2:]]   # after "[butler v1]"
        self.assertEqual(keys, ["chunk", "chunk_order"])


class ClosedVocabularyTests(unittest.TestCase):
    """No value can reach a description that isn't in describe.py's registry.

    Also guards docs/hints.md: every registry value must be documented there,
    so a value cannot be added to the vocabulary without documenting it.
    """

    def test_hint_line_refuses_an_unregistered_span(self):
        with self.assertRaises(AssertionError):
            hint_line(album(chunk="3/week"))

    def test_hint_line_refuses_a_count_out_of_range(self):
        for chunk in ("0/year", "51/year", "-1/year", "x/year", "3year"):
            with self.assertRaises(AssertionError, msg=chunk):
                hint_line(album(chunk=chunk))

    def test_hint_line_refuses_an_unregistered_order(self):
        with self.assertRaises(AssertionError):
            hint_line(album(chunk="3/year", chunk_order="backwards"))

    def test_parse_chunk(self):
        self.assertEqual(parse_chunk("3/year"), (3, "year"))
        self.assertEqual(parse_chunk("50/day"), (50, "day"))
        self.assertIsNone(parse_chunk("3/YEAR"))
        self.assertIsNone(parse_chunk("3/"))
        self.assertIsNone(parse_chunk(""))

    def test_every_registry_value_is_documented(self):
        docs = Path(__file__).resolve().parent.parent / "docs" / "hints.md"
        text = docs.read_text(encoding="utf-8")
        for value in (*describe.CHUNK_SPANS, *describe.CHUNK_ORDER_VALUES):
            self.assertIn(f"`{value}`", text,
                          f"{value!r} is in describe.py's registry but not "
                          f"documented in docs/hints.md")


class ApplyHintTests(unittest.TestCase):
    LINE = "[butler v1] chunk=3/day chunk_order=random"
    OLD = "[butler v1] chunk=5/year chunk_order=chronological"

    def test_an_empty_description_becomes_the_hint(self):
        self.assertEqual(apply_hint("", self.LINE), self.LINE)

    def test_a_hand_written_line_is_preserved_above_the_hint(self):
        out = apply_hint("Our two weeks in Tuscany.", self.LINE)
        self.assertEqual(out, "Our two weeks in Tuscany.\n\n" + self.LINE)

    def test_the_hint_is_replaced_in_place(self):
        out = apply_hint("Note.\n\n" + self.OLD, self.LINE)
        self.assertEqual(out, "Note.\n\n" + self.LINE)

    def test_other_lines_survive_a_replace(self):
        out = apply_hint(f"Line one.\n{self.OLD}\nLine three.", self.LINE)
        self.assertEqual(out, f"Line one.\n{self.LINE}\nLine three.")


class RemoveHintTests(unittest.TestCase):
    """An album that no longer has a chunk loses its line, and only its line."""

    OLD = "[butler v1] chunk=5/year chunk_order=chronological"

    def test_a_description_without_a_line_is_left_alone(self):
        for text in ("", "Our two weeks in Tuscany.", "A\n\nB"):
            self.assertEqual(apply_hint(text, ""), text)

    def test_a_description_holding_only_the_line_becomes_empty(self):
        self.assertEqual(apply_hint(self.OLD, ""), "")

    def test_the_hand_written_text_above_is_kept_without_a_trailing_gap(self):
        self.assertEqual(apply_hint("Note.\n\n" + self.OLD, ""), "Note.")

    def test_a_line_between_two_paragraphs_leaves_one_blank_line(self):
        self.assertEqual(apply_hint(f"One.\n\n{self.OLD}\n\nTwo.", ""),
                         "One.\n\nTwo.")

    def test_a_line_between_two_lines_leaves_them_adjacent(self):
        self.assertEqual(apply_hint(f"One.\n{self.OLD}\nTwo.", ""), "One.\nTwo.")

    def test_a_line_from_an_earlier_version_goes_too(self):
        old = "Note.\n\n[butler v1] kind=trip order=trip dwell=8"
        self.assertEqual(apply_hint(old, ""), "Note.")

    def test_only_the_last_marker_line_is_the_butlers(self):
        text = f"{self.OLD}\nNote.\n[butler v1] chunk=1/day chunk_order=random"
        self.assertEqual(apply_hint(text, ""), f"{self.OLD}\nNote.")


if __name__ == "__main__":
    unittest.main()
