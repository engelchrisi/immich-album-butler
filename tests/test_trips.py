import datetime as dt
import tempfile
import unittest
from pathlib import Path

from immich_album_butler import trips

# Fictional coordinates: "home" in one place, a trip far away.
HOME = (45.46, 9.19)
FAR = (40.71, -74.01)          # about 6,500 km from home
NEARBY = (45.50, 9.25)         # a few km from home


def point(number, when, where=None, city=None, state=None, country=None):
    lat, lon = where if where else (None, None)
    return trips.Point(id=f"{number:04d}", taken_at=when, latitude=lat, longitude=lon,
                       city=city, state=state, country=country)


def series(start, count, where, *, hours=2, first=0, **place):
    """A run of photos every couple of hours from `start`."""
    return [point(first + n, start + dt.timedelta(hours=hours * n), where, **place)
            for n in range(count)]


class DistanceTests(unittest.TestCase):
    def test_the_same_point_is_zero_apart(self):
        self.assertAlmostEqual(trips.haversine_km(*HOME, *HOME), 0.0, places=6)

    def test_a_known_distance_is_about_right(self):
        km = trips.haversine_km(*HOME, *FAR)
        self.assertTrue(6000 < km < 7000, km)


class HomeTests(unittest.TestCase):
    def test_home_is_where_most_photos_are(self):
        points = series(dt.datetime(2019, 1, 1), 50, HOME) + \
                 series(dt.datetime(2019, 6, 1), 5, FAR, first=100)
        lat, lon = trips.find_home(points)
        self.assertAlmostEqual(lat, HOME[0], places=1)
        self.assertAlmostEqual(lon, HOME[1], places=1)

    def test_without_any_coordinates_there_is_no_home(self):
        points = [point(n, dt.datetime(2019, 1, 1) + dt.timedelta(days=n))
                  for n in range(10)]
        self.assertIsNone(trips.find_home(points))


class DetectionTests(unittest.TestCase):
    def test_photos_only_from_home_are_not_a_trip(self):
        points = series(dt.datetime(2019, 1, 1), 100, HOME)
        self.assertEqual(trips.detect(points, home=HOME), [])

    def test_a_fortnight_away_is_one_trip(self):
        points = (series(dt.datetime(2019, 1, 1), 60, HOME)
                  + series(dt.datetime(2019, 7, 1), 60, FAR, first=1000,
                           city="Somewhere", country="Farland"))
        found = trips.detect(points, home=HOME)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].start, dt.date(2019, 7, 1))
        self.assertEqual(found[0].located, 60)

    def test_a_short_gap_inside_a_trip_does_not_split_it(self):
        points = (series(dt.datetime(2019, 7, 1), 30, FAR, country="Farland")
                  + series(dt.datetime(2019, 7, 5), 30, FAR, first=500,
                           country="Farland")
                  + series(dt.datetime(2019, 1, 1), 60, HOME, first=2000))
        found = trips.detect(points, home=HOME, max_gap_days=3)
        self.assertEqual(len(found), 1)

    def test_a_long_gap_splits_two_trips(self):
        points = (series(dt.datetime(2019, 3, 1), 40, FAR, country="Farland")
                  + series(dt.datetime(2019, 9, 1), 40, FAR, first=500,
                           country="Farland")
                  + series(dt.datetime(2019, 1, 1), 60, HOME, first=2000))
        found = trips.detect(points, home=HOME)
        self.assertEqual(len(found), 2)
        self.assertLess(found[0].start, found[1].start)

    def test_a_day_trip_is_below_the_minimums(self):
        points = (series(dt.datetime(2019, 7, 1), 4, FAR)
                  + series(dt.datetime(2019, 1, 1), 60, HOME, first=2000))
        self.assertEqual(trips.detect(points, home=HOME), [])

    def test_somewhere_nearby_is_not_far_enough_to_be_a_trip(self):
        points = series(dt.datetime(2019, 7, 1), 60, NEARBY) + \
                 series(dt.datetime(2019, 1, 1), 60, HOME, first=2000)
        self.assertEqual(trips.detect(points, home=HOME, away_km=100), [])

    def test_photos_without_gps_in_the_window_join_the_trip(self):
        away = series(dt.datetime(2019, 7, 1), 40, FAR, country="Farland")
        undated_place = [point(900 + n, dt.datetime(2019, 7, 2, n)) for n in range(10)]
        outside = [point(950, dt.datetime(2018, 1, 1))]
        found = trips.detect(away + undated_place + outside, home=HOME)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].unlocated, 10)
        self.assertEqual(found[0].total, 50)
        self.assertNotIn("0950", found[0].asset_ids)

    def test_unlocated_photos_can_be_left_out(self):
        away = series(dt.datetime(2019, 7, 1), 40, FAR)
        loose = [point(900 + n, dt.datetime(2019, 7, 2, n)) for n in range(10)]
        found = trips.detect(away + loose, home=HOME, include_unlocated=False)
        self.assertEqual(found[0].unlocated, 0)

    def test_an_empty_library_produces_no_trips(self):
        self.assertEqual(trips.detect([]), [])


class NamingTests(unittest.TestCase):
    def test_a_single_country_names_the_states(self):
        trip = trips.Trip(start=dt.date(2019, 7, 1), end=dt.date(2019, 7, 14),
                          countries=["Farland"], states=["North", "South"])
        self.assertEqual(trip.suggested_name(), "Farland – North, South 2019")

    def test_several_countries_are_listed_instead(self):
        trip = trips.Trip(start=dt.date(2019, 7, 1), end=dt.date(2019, 7, 14),
                          countries=["Farland", "Nearland"], states=["North"])
        self.assertEqual(trip.suggested_name(), "Farland, Nearland 2019")

    def test_without_a_country_the_cities_carry_the_name(self):
        trip = trips.Trip(start=dt.date(2019, 7, 1), end=dt.date(2019, 7, 14),
                          cities=["Somewhere"])
        self.assertEqual(trip.suggested_name(), "Somewhere 2019")

    def test_a_trip_over_new_year_names_both_years(self):
        trip = trips.Trip(start=dt.date(2019, 12, 28), end=dt.date(2020, 1, 4),
                          countries=["Farland"])
        self.assertEqual(trip.suggested_name(), "Farland 2019/2020")

    def test_a_trip_with_no_places_still_gets_a_name(self):
        trip = trips.Trip(start=dt.date(2019, 7, 1), end=dt.date(2019, 7, 14))
        self.assertEqual(trip.suggested_name(), "Trip 2019")

    def test_days_counts_both_end_days(self):
        trip = trips.Trip(start=dt.date(2019, 7, 1), end=dt.date(2019, 7, 14))
        self.assertEqual(trip.days, 14)


class ScanCacheTests(unittest.TestCase):
    def test_a_scan_round_trips_through_the_cache(self):
        points = series(dt.datetime(2019, 7, 1), 5, FAR, city="Somewhere",
                        country="Farland")
        with tempfile.TemporaryDirectory() as temp:
            trips.save_scan(Path(temp), points)
            loaded, when = trips.load_scan(Path(temp))
        self.assertEqual([p.id for p in loaded], [p.id for p in points])
        self.assertEqual(loaded[0].country, "Farland")
        self.assertEqual(loaded[0].taken_at, points[0].taken_at)
        self.assertIsNotNone(when)

    def test_no_cache_yet_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(trips.load_scan(Path(temp)), ([], None))

    def test_a_corrupt_cache_is_ignored_rather_than_fatal(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / trips.SCAN_FILE).write_text("{not json", encoding="utf-8")
            with self.assertLogs("immich_album_butler.trips", "WARNING"):
                points, _ = trips.load_scan(Path(temp))
        self.assertEqual(points, [])

    def test_a_cache_from_an_older_version_is_discarded(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / trips.SCAN_FILE).write_text(
                '{"version": 0, "points": [["x", "2019-07-01T00:00:00", null, null,'
                ' null, null, null, false]]}', encoding="utf-8")
            self.assertEqual(trips.load_scan(Path(temp))[0], [])


if __name__ == "__main__":
    unittest.main()
