import datetime as dt
import unittest

from immich_album_butler.config import MatchRule
from immich_album_butler.immich import Asset, ImmichClient, Person
from immich_album_butler.matcher import MatchError, match, place_matches, resolve_people

from .stub_immich import API_KEY, StubImmich, day, fake_id, make_asset

ALEX, SAM = "person-alex", "person-sam"

PEOPLE = [Person(id=ALEX, name="Alex"), Person(id=SAM, name="Sam")]


def asset(**kwargs):
    base = dict(id=fake_id(1), taken_at=dt.datetime(2019, 7, 4))
    base.update(kwargs)
    return Asset(**base)


class ResolvePeopleTests(unittest.TestCase):
    def test_names_map_to_ids(self):
        self.assertEqual(resolve_people(["Alex", "Sam"], PEOPLE), [ALEX, SAM])

    def test_matching_ignores_case(self):
        self.assertEqual(resolve_people(["alex"], PEOPLE), [ALEX])

    def test_an_unknown_name_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(MatchError) as caught:
            resolve_people(["Nobody"], PEOPLE)
        self.assertIn("Nobody", str(caught.exception))

    def test_two_people_with_the_same_name_are_refused(self):
        duplicated = PEOPLE + [Person(id="person-alex-2", name="Alex")]
        with self.assertRaises(MatchError) as caught:
            resolve_people(["Alex"], duplicated)
        self.assertIn("rename", str(caught.exception))


class PlaceFilterTests(unittest.TestCase):
    """include_unlocated is an OR the Immich API cannot express."""

    def test_a_rule_without_places_accepts_everything(self):
        self.assertTrue(place_matches(asset(), MatchRule(from_date=dt.date(2019, 1, 1))))

    def test_a_matching_country_is_kept(self):
        rule = MatchRule(countries=("Italy",))
        self.assertTrue(place_matches(asset(latitude=41.9, longitude=12.5,
                                            country="Italy"), rule))

    def test_another_country_is_dropped(self):
        rule = MatchRule(countries=("Italy",))
        self.assertFalse(place_matches(asset(latitude=40.4, longitude=-3.7,
                                             country="Spain"), rule))

    def test_country_matching_ignores_case(self):
        rule = MatchRule(countries=("italy",))
        self.assertTrue(place_matches(asset(latitude=41.9, longitude=12.5,
                                            country="Italy"), rule))

    def test_a_state_or_city_also_counts_as_the_place(self):
        rule = MatchRule(countries=("France",), cities=("Rome",))
        self.assertTrue(place_matches(asset(latitude=41.9, longitude=12.5,
                                            city="Rome", country="Italy"), rule))

    def test_photos_without_gps_are_kept_when_include_unlocated_is_true(self):
        rule = MatchRule(countries=("Italy",), include_unlocated=True)
        self.assertTrue(place_matches(asset(), rule))

    def test_photos_without_gps_are_dropped_when_it_is_false(self):
        rule = MatchRule(countries=("Italy",), include_unlocated=False)
        self.assertFalse(place_matches(asset(), rule))


class MatchAgainstStubTests(unittest.TestCase):
    def setUp(self):
        self.assets = [
            make_asset(1, when=day(2019, 7, 2), lat=41.9, lon=12.5,
                       city="Rome", country="Italy", people=(ALEX,)),
            make_asset(2, when=day(2019, 7, 3), people=(ALEX, SAM)),     # no GPS
            make_asset(3, when=day(2019, 7, 4), lat=40.4, lon=-3.7,
                       city="Madrid", country="Spain", people=(SAM,)),
            make_asset(4, when=day(2020, 1, 1), lat=41.9, lon=12.5,
                       city="Rome", country="Italy", people=(ALEX,)),
        ]

    def client(self, stub):
        return ImmichClient(stub.url, API_KEY)

    def test_a_date_window_bounds_the_result(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 31))
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids), {fake_id(1), fake_id(2), fake_id(3)})

    def test_a_place_rule_keeps_unlocated_photos_in_the_window(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 31),
                         countries=("Italy",), include_unlocated=True)
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids), {fake_id(1), fake_id(2)})
        self.assertTrue(any("no GPS" in w for w in found.warnings))

    def test_the_same_rule_without_unlocated_keeps_only_located_photos(self):
        rule = MatchRule(from_date=dt.date(2019, 7, 1), to_date=dt.date(2019, 7, 31),
                         countries=("Italy",), include_unlocated=False)
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids), {fake_id(1)})

    def test_people_any_is_the_union_of_their_photos(self):
        rule = MatchRule(people=("Alex", "Sam"), people_mode="any")
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids),
                         {fake_id(1), fake_id(2), fake_id(3), fake_id(4)})

    def test_people_all_is_the_intersection(self):
        rule = MatchRule(people=("Alex", "Sam"), people_mode="all")
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids), {fake_id(2)})

    def test_a_person_album_is_just_the_people_criterion(self):
        rule = MatchRule(people=("Alex",))
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids), {fake_id(1), fake_id(2), fake_id(4)})

    def test_results_come_back_in_time_order(self):
        rule = MatchRule(from_date=dt.date(2019, 1, 1))
        with StubImmich(self.assets, page_size=2) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(found.ids, [fake_id(1), fake_id(2), fake_id(3), fake_id(4)])

    def test_a_single_country_without_unlocated_is_pushed_to_the_server(self):
        """Cheaper, and safe only because unlocated photos are not wanted."""
        rule = MatchRule(countries=("Italy",), include_unlocated=False)
        with StubImmich(self.assets, page_size=100) as stub:
            found = match(self.client(stub), rule, PEOPLE)
        self.assertEqual(set(found.ids), {fake_id(1), fake_id(4)})

    def test_an_unknown_person_stops_this_album_with_a_clear_error(self):
        rule = MatchRule(people=("Nobody",))
        with StubImmich(self.assets) as stub:
            with self.assertRaises(MatchError):
                match(self.client(stub), rule, PEOPLE)

    def test_names_are_resolved_against_the_server_when_none_are_supplied(self):
        """The normal path: thousands of face clusters, resolved by name."""
        api_people = [{"id": ALEX, "name": "Alex"}, {"id": SAM, "name": "Sam"}]
        rule = MatchRule(people=("Alex",))
        with StubImmich(self.assets, people=api_people, unnamed_people=5000,
                        page_size=100) as stub:
            found = match(self.client(stub), rule)
            listed = sum(1 for _, path in stub.requests if path == "/api/people")
        self.assertEqual(set(found.ids), {fake_id(1), fake_id(2), fake_id(4)})
        self.assertEqual(listed, 0)      # asked by name, never listed everyone

    def test_an_unknown_name_is_an_error_on_that_path_too(self):
        api_people = [{"id": ALEX, "name": "Alex"}]
        with StubImmich(self.assets, people=api_people) as stub:
            with self.assertRaises(MatchError):
                match(self.client(stub), MatchRule(people=("Nobody",)))


if __name__ == "__main__":
    unittest.main()
