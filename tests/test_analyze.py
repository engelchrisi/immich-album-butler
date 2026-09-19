import datetime as dt
import unittest

from immich_album_butler.analyze import analyze, people_elsewhere
from immich_album_butler.config import MatchRule
from immich_album_butler.immich import ImmichClient
from immich_album_butler.trips import Point

from .stub_immich import API_KEY, StubImmich, day, fake_id, make_asset

ALEX, SAM = "person-alex", "person-sam"


def point(number, when, *, lat=None, lon=None, city=None, country=None):
    return Point(id=fake_id(number), taken_at=when, latitude=lat, longitude=lon,
                 city=city, country=country)


def at(year, month, day_of_month, hour=12):
    return dt.datetime(year, month, day_of_month, hour)


ROME = dict(lat=41.9, lon=12.5, city="Rome", country="Italy")
MADRID = dict(lat=40.4, lon=-3.7, city="Madrid", country="Spain")


class CloseInTimeTests(unittest.TestCase):
    """The journey there and back: same occasion, just past the boundary."""

    def setUp(self):
        self.points = [
            point(1, at(2019, 6, 30, 22), **ROME),      # 14h before: too early
            point(2, at(2019, 7, 1, 6), **ROME),        # 6h before the album
            point(3, at(2019, 7, 1, 12), **ROME),       # in the album
            point(4, at(2019, 7, 2, 12), **ROME),       # in the album
            point(5, at(2019, 7, 2, 20), **ROME),       # 8h after
        ]
        self.matched = [fake_id(3), fake_id(4)]
        self.rule = MatchRule(from_date=dt.date(2019, 7, 1),
                              to_date=dt.date(2019, 7, 2))

    def _group(self, key, **kwargs):
        found = analyze(self.points, self.matched, self.rule, **kwargs)
        return next((s for s in found if s.key == key), None)

    def test_it_finds_the_assets_just_outside_the_window(self):
        group = self._group("close_in_time")
        self.assertEqual(set(group.asset_ids), {fake_id(2), fake_id(5)})

    def test_something_far_outside_is_not_included(self):
        group = self._group("close_in_time")
        self.assertNotIn(fake_id(1), group.asset_ids)

    def test_a_wider_window_reaches_further(self):
        group = self._group("close_in_time", close_hours=24)
        self.assertIn(fake_id(1), group.asset_ids)

    def test_the_suggested_adjustment_widens_the_dates_to_cover_them(self):
        group = self._group("close_in_time")
        self.assertEqual(group.adjust["from"], "2019-07-01")
        self.assertEqual(group.adjust["to"], "2019-07-02")

    def test_an_album_with_nothing_in_it_yields_no_suggestions(self):
        self.assertEqual(analyze(self.points, [], self.rule), [])


class WindowTests(unittest.TestCase):
    def test_unlocated_assets_are_offered_when_the_rule_excludes_them(self):
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2019, 7, 1, 13))]              # no GPS
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2),
                         countries=("Italy",), include_unlocated=False)
        found = analyze(points, [fake_id(1)], rule)
        group = next(s for s in found if s.key == "window_unlocated")
        self.assertEqual(group.asset_ids, [fake_id(2)])
        self.assertEqual(group.adjust, {"include_unlocated": True})

    def test_nothing_is_offered_when_the_rule_already_includes_them(self):
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2019, 7, 1, 13))]
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2),
                         countries=("Italy",), include_unlocated=True)
        keys = {s.key for s in analyze(points, [fake_id(1)], rule)}
        self.assertNotIn("window_unlocated", keys)

    def test_a_day_trip_to_another_country_is_offered_with_its_country(self):
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2019, 7, 2), **MADRID)]
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 3),
                         countries=("Italy",))
        group = next(s for s in analyze(points, [fake_id(1)], rule)
                     if s.key == "window_other_place")
        self.assertEqual(group.asset_ids, [fake_id(2)])
        self.assertEqual(group.adjust, {"countries": ["Italy", "Spain"]})

    def test_a_rule_without_places_offers_no_place_groups(self):
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2019, 7, 2), **MADRID)]
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 3))
        keys = {s.key for s in analyze(points, [fake_id(1)], rule)}
        self.assertNotIn("window_other_place", keys)
        self.assertNotIn("window_unlocated", keys)


class NearbyTests(unittest.TestCase):
    def test_the_same_place_in_another_year_is_reported(self):
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2022, 5, 5), **ROME)]
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2))
        group = next(s for s in analyze(points, [fake_id(1)], rule)
                     if s.key == "nearby_other_time")
        self.assertEqual(group.asset_ids, [fake_id(2)])

    def test_it_offers_no_adjustment(self):
        """Widening the dates that far would swallow everything in between."""
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2022, 5, 5), **ROME)]
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2))
        group = next(s for s in analyze(points, [fake_id(1)], rule)
                     if s.key == "nearby_other_time")
        self.assertIsNone(group.adjust)

    def test_a_different_city_is_not_nearby(self):
        points = [point(1, at(2019, 7, 1), **ROME),
                  point(2, at(2022, 5, 5), **MADRID)]
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2))
        keys = {s.key for s in analyze(points, [fake_id(1)], rule)}
        self.assertNotIn("nearby_other_time", keys)


class PeopleElsewhereTests(unittest.TestCase):
    ASSETS = [
        make_asset(1, when=day(2019, 7, 1), people=(ALEX,)),     # in the album
        make_asset(2, when=day(2019, 8, 15), people=(ALEX,)),    # weeks later
        make_asset(3, when=day(2015, 1, 1), people=(ALEX,)),     # years earlier
    ]
    PEOPLE = [{"id": ALEX, "name": "Alex"}]

    def test_it_finds_the_same_person_just_outside_the_window(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2),
                         people=("Alex",))
        with StubImmich(self.ASSETS, people=self.PEOPLE, page_size=100) as stub:
            group = people_elsewhere(ImmichClient(stub.url, API_KEY), rule,
                                     [fake_id(1)])
        self.assertEqual(group.asset_ids, [fake_id(2)])

    def test_a_rule_without_people_asks_immich_nothing(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2))
        with StubImmich(self.ASSETS, people=self.PEOPLE) as stub:
            self.assertIsNone(people_elsewhere(ImmichClient(stub.url, API_KEY),
                                               rule, [fake_id(1)]))
            self.assertEqual(stub.requests, [])

    def test_a_person_album_with_no_dates_has_no_outside(self):
        rule = MatchRule(people=("Alex",))
        with StubImmich(self.ASSETS, people=self.PEOPLE) as stub:
            self.assertIsNone(people_elsewhere(ImmichClient(stub.url, API_KEY),
                                               rule, [fake_id(1)]))

    def test_an_unknown_name_is_not_an_error_here(self):
        """Analyze is advisory: it must not break on what the preview reports."""
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 2),
                         people=("Nobody",))
        with StubImmich(self.ASSETS, people=self.PEOPLE) as stub:
            self.assertIsNone(people_elsewhere(ImmichClient(stub.url, API_KEY),
                                               rule, [fake_id(1)]))


if __name__ == "__main__":
    unittest.main()
