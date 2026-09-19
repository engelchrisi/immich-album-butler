"""Choosing an album cover, and setting it through a run."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from immich_album_butler import config as cfg
from immich_album_butler import cover
from immich_album_butler.immich import Asset
from immich_album_butler.runtime import run_once

from .stub_immich import StubImmich, day, fake_id, make_asset
from .test_runtime import SETTINGS, Fixture

ALEX, SAM, ROBIN = "person-alex", "person-sam", "person-robin"
PEOPLE = [{"id": ALEX, "name": "Alex"}, {"id": SAM, "name": "Sam"},
          {"id": ROBIN, "name": "Robin"}]


def asset(number: int, year: int, name: str = "", kind: str = "IMAGE") -> Asset:
    return Asset(id=fake_id(number), taken_at=dt.datetime(year, 7, 1),
                 kind=kind, file_name=name or f"IMG_{number:04d}.jpg")


class ChoosingTests(unittest.TestCase):
    def setUp(self):
        self.assets = [asset(1, 2019), asset(2, 2020), asset(3, 2021)]

    def test_auto_leaves_the_cover_alone(self):
        self.assertIsNone(cover.choose("auto", self.assets))

    def test_no_matching_assets_means_no_cover(self):
        self.assertIsNone(cover.choose("newest", []))

    def test_newest_and_oldest_are_the_ends_of_the_album(self):
        self.assertEqual(cover.choose("newest", self.assets), fake_id(3))
        self.assertEqual(cover.choose("oldest", self.assets), fake_id(1))

    def test_a_file_name_picks_that_picture(self):
        found = cover.choose("IMG_0002.jpg", self.assets)
        self.assertEqual(found, fake_id(2))

    def test_a_file_name_ignores_case(self):
        self.assertEqual(cover.choose("img_0002.JPG", self.assets), fake_id(2))

    def test_an_unknown_file_name_says_what_a_cover_may_be(self):
        with self.assertRaises(cover.CoverError) as caught:
            cover.choose("holiday.jpg", self.assets)
        self.assertIn("holiday.jpg", str(caught.exception))
        self.assertIn("everyone", str(caught.exception))

    def test_a_video_is_not_chosen_while_a_picture_exists(self):
        library = [asset(1, 2019), asset(9, 2024, kind="VIDEO")]
        self.assertEqual(cover.choose("newest", library), fake_id(1))

    def test_an_album_of_only_video_still_gets_a_cover(self):
        library = [asset(9, 2024, kind="VIDEO")]
        self.assertEqual(cover.choose("newest", library), fake_id(9))


class EveryoneTests(unittest.TestCase):
    """The point of the feature: the picture with the whole group in it."""

    def test_the_picture_with_the_most_people_wins(self):
        assets = [asset(1, 2019), asset(2, 2020), asset(3, 2021)]
        by_person = {ALEX: {fake_id(1), fake_id(2)},
                     SAM: {fake_id(2)},
                     ROBIN: {fake_id(2), fake_id(3)}}
        self.assertEqual(cover.choose("everyone", assets, by_person), fake_id(2))

    def test_a_tie_is_broken_by_the_newest_picture(self):
        assets = [asset(1, 2019), asset(2, 2020)]
        by_person = {ALEX: {fake_id(1), fake_id(2)}, SAM: {fake_id(1), fake_id(2)}}
        self.assertEqual(cover.choose("everyone", assets, by_person), fake_id(2))

    def test_nobody_to_count_is_an_error_rather_than_a_silent_guess(self):
        with self.assertRaises(cover.CoverError):
            cover.choose("everyone", [asset(1, 2019)], {})

    def test_people_who_appear_in_nothing_matched_are_reported(self):
        by_person = {ALEX: set(), SAM: set()}
        with self.assertRaises(cover.CoverError):
            cover.choose("everyone", [asset(1, 2019)], by_person)


class ConfigTests(unittest.TestCase):
    def config(self, body: str) -> cfg.Config:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        (directory / "config.toml").write_text(SETTINGS + body, encoding="utf-8")
        return cfg.load(directory)

    def test_an_album_without_a_cover_leaves_immich_alone(self):
        config = self.config('\n[albums.a]\nname = "A"\n'
                             '[albums.a.match]\npeople = ["Alex"]\n')
        self.assertEqual(config.albums[0].cover, "auto")
        self.assertFalse(config.albums[0].sets_cover)

    def test_a_cover_survives_the_round_trip(self):
        config = self.config('\n[albums.a]\nname = "A"\ncover = "everyone"\n'
                             '[albums.a.match]\npeople = ["Alex"]\n')
        text = cfg.dump_config(config)
        self.assertIn('cover   = "everyone"', text)

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        cfg.write_config(directory, config)
        self.assertEqual(cfg.load(directory).albums[0].cover, "everyone")

    def test_everyone_without_people_is_refused_where_it_is_written(self):
        config = self.config('\n[albums.a]\nname = "A"\ncover = "everyone"\n'
                             '[albums.a.match]\ncountries = ["Italy"]\n')
        self.assertEqual(config.albums, [])
        self.assertIn("everyone", config.errors[0])

    def test_a_cover_that_is_not_a_string_is_refused(self):
        config = self.config('\n[albums.a]\nname = "A"\ncover = 3\n'
                             '[albums.a.match]\npeople = ["Alex"]\n')
        self.assertEqual(config.albums, [])


GROUP_ALBUM = """
name = "The Three"
cover = "everyone"

[match]
people = ["Alex", "Sam", "Robin"]
"""


def group_assets():
    return [
        make_asset(1, when=day(2019, 7, 2), people=(ALEX,)),
        make_asset(2, when=day(2019, 7, 3), people=(ALEX, SAM, ROBIN)),
        make_asset(3, when=day(2019, 7, 4), people=(SAM,)),
    ]


class RunTests(unittest.TestCase):
    def test_a_created_album_gets_the_picture_with_everyone_in_it(self):
        with StubImmich(group_assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"the-three": GROUP_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
                album = stub.album_named("The Three")
        self.assertTrue(reports[0].cover_set)
        self.assertEqual(album["albumThumbnailAssetId"], fake_id(2))

    def test_a_second_run_does_not_set_the_same_cover_again(self):
        with StubImmich(group_assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"the-three": GROUP_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                stub.requests.clear()
                config, state = fx.load()
                again = run_once(fx.client, config, state)
        self.assertFalse(again[0].cover_set)
        self.assertNotIn("PATCH", [method for method, _ in stub.requests])

    def test_a_dry_run_says_it_would_set_a_cover_and_sets_none(self):
        with StubImmich(group_assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"the-three": GROUP_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state, dry_run=True)
        self.assertTrue(reports[0].cover_set)
        self.assertNotIn("PATCH", [method for method, _ in stub.requests])

    def test_a_key_without_album_update_still_adds_the_photos(self):
        """The one write needing album.update must not fail the whole run."""
        with StubImmich(group_assets(), people=PEOPLE, page_size=2,
                        missing_permissions={"album.update"}) as stub:
            with Fixture({"the-three": GROUP_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
                album = stub.album_named("The Three")
        report = reports[0]
        self.assertTrue(report.ok)
        self.assertEqual(report.added, 3)
        self.assertFalse(report.cover_set)
        self.assertIn("album.update", " ".join(report.warnings))
        self.assertIsNone(album.get("albumThumbnailAssetId"))

    def test_a_cover_naming_a_missing_file_is_a_warning_not_a_failure(self):
        album_rule = GROUP_ALBUM.replace('"everyone"', '"nowhere.jpg"')
        with StubImmich(group_assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"the-three": album_rule}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertTrue(reports[0].ok)
        self.assertEqual(reports[0].added, 3)
        self.assertFalse(reports[0].cover_set)

    def test_a_cover_by_file_name_is_set(self):
        album_rule = GROUP_ALBUM.replace('"everyone"', '"IMG_0003.jpg"')
        with StubImmich(group_assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"the-three": album_rule}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                album = stub.album_named("The Three")
        self.assertEqual(album["albumThumbnailAssetId"], fake_id(3))


if __name__ == "__main__":
    unittest.main()
