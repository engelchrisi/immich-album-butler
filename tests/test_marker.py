"""The album_suffix marker: telling the butler's albums apart in Immich.

Immich albums carry no tags, so the name is the only marker its UI can show.
The risk the tests below are really about is duplication: an album that the
butler already fills must be recognised whether or not it carries the marker
yet, or turning the marker on would build a second copy of every album.
"""

import tempfile
import unittest
from pathlib import Path

from immich_album_butler import config as cfg
from immich_album_butler.runtime import run_once

from .stub_immich import StubImmich, day, fake_id, make_asset
from .test_runtime import Fixture

ALEX = "person-alex"
PEOPLE = [{"id": ALEX, "name": "Alex"}]

MARKED_SETTINGS = """
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
album_suffix = "[AB]"
"""

ALEX_ALBUM = """
name = "Photos of Alex"

[match]
people = ["Alex"]
"""


def assets():
    return [make_asset(1, when=day(2019, 7, 2), people=(ALEX,)),
            make_asset(2, when=day(2019, 7, 3), people=(ALEX,))]


class MarkedFixture(Fixture):
    """Fixture, but with album_suffix turned on."""

    def __init__(self, albums, stub):
        super().__init__(albums, stub)
        path = self.config_dir / "config.toml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('auto-update-schedule = "daily 03:30"',
                                     'auto-update-schedule = "daily 03:30"\n'
                                     'album_suffix = "[AB]"'), encoding="utf-8")


class SettingTests(unittest.TestCase):
    def load(self, text: str) -> cfg.Config:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        (directory / "config.toml").write_text(text, encoding="utf-8")
        return cfg.load(directory)

    def test_no_suffix_by_default(self):
        config = self.load('server = "http://immich.example.lan:2283"\n')
        self.assertEqual(config.settings.album_suffix, "")

    def test_the_suffix_survives_a_rewrite(self):
        config = self.load(MARKED_SETTINGS)
        self.assertEqual(config.settings.album_suffix, "[AB]")
        self.assertIn('album_suffix = "[AB]"', cfg.dump_config(config))

    def test_a_suffix_that_is_not_a_string_is_refused(self):
        with self.assertRaises(cfg.ConfigError):
            self.load('server = "http://immich.example.lan:2283"\n'
                      'album_suffix = 3\n')


class NamingTests(unittest.TestCase):
    def test_a_new_album_is_created_with_the_marker(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertIsNotNone(stub.album_named("Photos of Alex [AB]"))
        self.assertIsNone(stub.album_named("Photos of Alex"))

    def test_an_existing_album_is_renamed_not_duplicated(self):
        """The thing that would hurt: one album per name, not two."""
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            stub.add_album("Photos of Alex", [fake_id(1)])
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(len(stub.albums), 1)
        self.assertTrue(reports[0].renamed)
        self.assertIsNotNone(stub.album_named("Photos of Alex [AB]"))
        self.assertEqual(reports[0].added, 1)      # the one it was missing

    def test_a_second_run_does_not_rename_again(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                stub.requests.clear()
                config, state = fx.load()
                again = run_once(fx.client, config, state)
        self.assertFalse(again[0].renamed)
        self.assertNotIn("PATCH", [method for method, _ in stub.requests])

    def test_a_marked_album_is_still_found_with_the_marker_turned_off(self):
        """Turning the suffix off must not orphan the albums it named."""
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
            # A fresh config directory: no state file, so this is a lookup by
            # name alone, exactly as a rebuilt container would do it.
            with Fixture({"alex": ALEX_ALBUM}, stub) as plain:
                config, state = plain.load()
                butler_album = config.album("alex")
                from immich_album_butler.runtime import Butler
                found = Butler(plain.client, config, state).find_album(butler_album)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "Photos of Alex [AB]")

    def test_a_dry_run_reports_the_rename_and_performs_none(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            stub.add_album("Photos of Alex", [fake_id(1)])
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state, dry_run=True)
        self.assertTrue(reports[0].renamed)
        self.assertIsNotNone(stub.album_named("Photos of Alex"))

    def test_a_key_without_album_update_keeps_the_album_working(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2,
                        missing_permissions={"album.update"}) as stub:
            stub.add_album("Photos of Alex", [fake_id(1)])
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        report = reports[0]
        self.assertTrue(report.ok)
        self.assertFalse(report.renamed)
        self.assertEqual(report.added, 1)
        self.assertIn("album.update", " ".join(report.warnings))
        self.assertIsNotNone(stub.album_named("Photos of Alex"))

    def test_an_unrelated_album_starting_with_the_same_word_is_not_adopted(self):
        """A library really does hold 'Alex' next to 'Alex at the seaside'."""
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            stub.add_album("Photos of Alex at the seaside", [fake_id(1)])
            with MarkedFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertEqual(len(stub.albums), 2)
        self.assertIsNotNone(stub.album_named("Photos of Alex [AB]"))

    def test_a_name_that_already_ends_in_the_marker_is_left_alone(self):
        album = ALEX_ALBUM.replace('"Photos of Alex"', '"Photos of Alex [AB]"')
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with MarkedFixture({"alex": album}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertIsNotNone(stub.album_named("Photos of Alex [AB]"))
        self.assertIsNone(stub.album_named("Photos of Alex [AB] [AB]"))


SPLIT_SETTINGS = ('auto-update-schedule = "daily 03:30"\n'
                  'album_suffix_fixed = "●"\n'
                  'album_suffix_updating = "↻"')


class SplitFixture(Fixture):
    """Fixture with separate suffixes for fixed and updating albums."""

    def __init__(self, albums, stub):
        super().__init__(albums, stub)
        path = self.config_dir / "config.toml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('auto-update-schedule = "daily 03:30"',
                                     SPLIT_SETTINGS), encoding="utf-8")


FIXED_ALBUM = ALEX_ALBUM.replace('name = "Photos of Alex"',
                                 'name = "Photos of Alex"\n'
                                 'auto-update-schedule = "manual"')


class SplitSuffixTests(unittest.TestCase):
    def test_fixed_and_updating_albums_get_their_own_suffix(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with SplitFixture({"alex": ALEX_ALBUM,
                               "fixed": FIXED_ALBUM.replace("Photos of Alex", "Photos of Fixed")},
                              stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertIsNotNone(stub.album_named("Photos of Alex ↻"))
        self.assertIsNotNone(stub.album_named("Photos of Fixed ●"))

    def test_switching_kind_renames_instead_of_duplicating(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            stub.add_album("Photos of Alex ●", [fake_id(1)])
            with SplitFixture({"alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(len(stub.albums), 1)
        self.assertTrue(reports[0].renamed)
        self.assertIsNotNone(stub.album_named("Photos of Alex ↻"))

    def test_the_common_suffix_is_the_fallback(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with MarkedFixture({"alex": FIXED_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertIsNotNone(stub.album_named("Photos of Alex [AB]"))


if __name__ == "__main__":
    unittest.main()
