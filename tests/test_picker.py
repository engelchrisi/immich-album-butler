import datetime as dt
import random
import unittest

from immich_album_butler.immich import Asset
from immich_album_butler.picker import pick


def asset(n: int, year: int, *, favorite: bool = False,
          rating: int | None = None) -> Asset:
    return Asset(id=f"a{n:03d}", taken_at=dt.datetime(year, 5, 17, 12),
                 is_favorite=favorite, rating=rating)


def ids(assets) -> set[str]:
    return {a.id for a in assets}


class AllModeTests(unittest.TestCase):
    def test_all_keeps_everything_and_carries_no_state(self):
        matched = [asset(n, 2019) for n in range(10)]
        picked, bag, last = pick("all", None, matched, {}, {}, random.Random(0))
        self.assertEqual(ids(picked), ids(matched))
        self.assertEqual((bag, last), ({}, {}))


class BestModeTests(unittest.TestCase):
    def test_best_prefers_favourites_then_ratings(self):
        matched = [asset(1, 2019, rating=1), asset(2, 2019, favorite=True),
                   asset(3, 2019, rating=5), asset(4, 2019)]
        picked, _, _ = pick("best", 2, matched, {}, {}, random.Random(0))
        self.assertEqual(ids(picked), {"a002", "a003"})   # favourite, then 5-star

    def test_best_is_stable_between_runs(self):
        matched = [asset(n, 2019, rating=n) for n in range(1, 6)]
        first, _, _ = pick("best", 2, matched, {}, {}, random.Random(1))
        second, _, _ = pick("best", 2, matched, {}, {}, random.Random(2))
        self.assertEqual(ids(first), ids(second))


class YearBucketTests(unittest.TestCase):
    def test_each_year_is_capped_on_its_own(self):
        matched = ([asset(1, 2018)]                          # one photo: kept
                   + [asset(100 + n, 2019) for n in range(10)])
        picked, _, _ = pick("random", 2, matched, {}, {}, random.Random(0))
        by_year = {}
        for a in picked:
            by_year.setdefault(a.taken_at.year, 0)
            by_year[a.taken_at.year] += 1
        self.assertEqual(by_year[2018], 1)   # below the cap: all kept
        self.assertEqual(by_year[2019], 2)   # above the cap: capped


class RotateModeTests(unittest.TestCase):
    def setUp(self):
        self.matched = [asset(n, 2019) for n in range(6)]

    def test_two_consecutive_runs_pick_disjoint_sets(self):
        rng = random.Random("x|0")
        first, bag, last = pick("rotate", 2, self.matched, {}, {}, rng)
        second, bag, last = pick("rotate", 2, self.matched, bag, last, rng)
        self.assertFalse(ids(first) & ids(second))

    def test_the_cycle_wraps_after_the_whole_set_is_shown(self):
        rng = random.Random("x|0")
        seen = set()
        bag, last = {}, {}
        for _ in range(3):                    # 3 runs x 2 = the whole set of 6
            picked, bag, last = pick("rotate", 2, self.matched, bag, last, rng)
            seen |= ids(picked)
        self.assertEqual(seen, ids(self.matched))
        fourth, bag, last = pick("rotate", 2, self.matched, bag, last, rng)
        self.assertEqual(len(fourth), 2)      # wrapped, still a full draw

    def test_a_dropped_asset_leaves_the_bag_and_a_new_one_joins(self):
        rng = random.Random("x|0")
        _, bag, last = pick("rotate", 2, self.matched, {}, {}, rng)
        changed = [a for a in self.matched if a.id != "a000"] + [asset(9, 2019)]
        picked, bag, last = pick("rotate", 2, changed, bag, last, rng)
        self.assertNotIn("a000", {i for v in bag.values() for i in v})
        self.assertEqual(len(picked), 2)

    def test_a_year_at_or_below_the_cap_keeps_all_and_rotates_nothing(self):
        matched = [asset(n, 2019) for n in range(2)]
        picked, bag, last = pick("rotate", 5, matched, {}, {}, random.Random(0))
        self.assertEqual(ids(picked), ids(matched))
        self.assertEqual(bag, {})


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_pick(self):
        matched = [asset(n, 2019) for n in range(8)]
        a, _, _ = pick("random", 3, matched, {}, {}, random.Random("s|4"))
        b, _, _ = pick("random", 3, matched, {}, {}, random.Random("s|4"))
        self.assertEqual(ids(a), ids(b))

    def test_different_seed_may_differ(self):
        matched = [asset(n, 2019) for n in range(50)]
        a, _, _ = pick("random", 3, matched, {}, {}, random.Random("s|1"))
        b, _, _ = pick("random", 3, matched, {}, {}, random.Random("s|2"))
        self.assertNotEqual(ids(a), ids(b))


if __name__ == "__main__":
    unittest.main()
