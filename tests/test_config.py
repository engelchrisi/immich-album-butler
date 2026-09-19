import datetime as dt
import tempfile
import unittest
from pathlib import Path

from immich_album_butler import config as cfg

GLOBAL = """
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
timezone = "Europe/Rome"
"""

ITALY = """
name = "Italy 2019"
auto-update-schedule = "weekly sun 04:00"

[match]
from = 2019-07-01
to   = 2019-07-21
countries = ["Italy"]
people = ["Family Example"]
include_unlocated = true
"""

PERSON_ALBUM = """
name = "Photos of Alex"
sync = "mirror"

[match]
people = ["Alex"]
"""

GROUPS = """
["Family Example"]
members = ["Alex", "Sam", "Robin"]
"""


class ConfigDir:
    """A throwaway config directory built from strings."""

    def __init__(self, settings=GLOBAL, albums=None, groups=None):
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name)
        if settings is not None:
            (self.path / "config.toml").write_text(settings, encoding="utf-8")
        if groups is not None:
            (self.path / "groups.toml").write_text(groups, encoding="utf-8")
        albums_dir = self.path / "albums.d"
        albums_dir.mkdir()
        for name, text in (albums or {}).items():
            (albums_dir / f"{name}.toml").write_text(text, encoding="utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._temp.cleanup()


class LoadingTests(unittest.TestCase):
    def test_loads_settings_albums_and_groups(self):
        with ConfigDir(albums={"italy-2019": ITALY}, groups=GROUPS) as d:
            config = cfg.load(d.path)
        self.assertEqual(config.settings.server, "http://immich.example.lan:2283")
        self.assertEqual(config.settings.timezone, "Europe/Rome")
        self.assertEqual(len(config.albums), 1)
        self.assertEqual(config.groups["Family Example"], ("Alex", "Sam", "Robin"))
        self.assertEqual(config.errors, [])

    def test_album_fields_are_parsed_into_real_types(self):
        with ConfigDir(albums={"italy-2019": ITALY}) as d:
            album = cfg.load(d.path).albums[0]
        self.assertEqual(album.slug, "italy-2019")
        self.assertEqual(album.name, "Italy 2019")
        self.assertEqual(album.match.from_date, dt.date(2019, 7, 1))
        self.assertEqual(album.match.countries, ("Italy",))
        self.assertEqual(str(album.schedule), "weekly sun 04:00")
        self.assertFalse(album.schedule_inherited)

    def test_an_album_without_a_schedule_inherits_the_global_one(self):
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM}) as d:
            album = cfg.load(d.path).albums[0]
        self.assertTrue(album.schedule_inherited)
        self.assertEqual(str(album.schedule), "daily 03:30")

    def test_without_a_global_schedule_albums_default_to_manual(self):
        with ConfigDir(settings='server = "http://immich.example.lan:2283"',
                       albums={"photos-of-alex": PERSON_ALBUM}) as d:
            album = cfg.load(d.path).albums[0]
        self.assertFalse(album.schedule.automatic)

    def test_mirror_is_opt_in_and_add_is_the_default(self):
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM,
                               "italy-2019": ITALY}) as d:
            config = cfg.load(d.path)
        self.assertTrue(config.album("photos-of-alex").mirrors)
        self.assertFalse(config.album("italy-2019").mirrors)

    def test_missing_config_toml_is_fatal(self):
        with ConfigDir(settings=None) as d:
            with self.assertRaises(cfg.ConfigError):
                cfg.load(d.path)

    def test_a_server_without_a_scheme_is_rejected(self):
        with ConfigDir(settings='server = "immich.example.lan:2283"') as d:
            with self.assertRaises(cfg.ConfigError):
                cfg.load(d.path)


class BrokenAlbumTests(unittest.TestCase):
    """One bad file must not take the other albums down with it."""

    def test_a_broken_album_is_reported_and_the_rest_still_load(self):
        with ConfigDir(albums={"italy-2019": ITALY,
                               "broken": 'name = "X"\n[match]\nfrom = "not-a-date"\n'},
                       groups=GROUPS) as d:
            config = cfg.load(d.path)
        self.assertEqual([a.slug for a in config.albums], ["italy-2019"])
        self.assertEqual(len(config.errors), 1)
        self.assertIn("broken.toml", config.errors[0])

    def test_an_empty_match_is_refused_rather_than_matching_everything(self):
        with ConfigDir(albums={"everything": 'name = "All"\n[match]\n'}) as d:
            config = cfg.load(d.path)
        self.assertEqual(config.albums, [])
        self.assertIn("whole library", config.errors[0])

    def test_a_backwards_date_range_is_refused(self):
        with ConfigDir(albums={"backwards": 'name = "B"\n[match]\n'
                                            "from = 2019-07-21\nto = 2019-07-01\n"}) as d:
            self.assertIn("is after", cfg.load(d.path).errors[0])

    def test_an_unknown_sync_mode_is_refused(self):
        with ConfigDir(albums={"odd": 'name = "O"\nsync = "delete"\n'
                                      '[match]\npeople = ["Alex"]\n'}) as d:
            self.assertIn("sync", cfg.load(d.path).errors[0])

    def test_a_bad_schedule_names_the_album_file(self):
        with ConfigDir(albums={"odd": 'name = "O"\nauto-update-schedule = "hourly"\n'
                                      '[match]\npeople = ["Alex"]\n'}) as d:
            self.assertIn("odd.toml", cfg.load(d.path).errors[0])

    def test_two_albums_with_the_same_name_are_reported(self):
        with ConfigDir(albums={"a": 'name = "Same"\n[match]\npeople = ["Alex"]\n',
                               "b": 'name = "Same"\n[match]\npeople = ["Sam"]\n'}) as d:
            errors = cfg.load(d.path).errors
        self.assertTrue(any("already used" in e for e in errors))


class GroupTests(unittest.TestCase):
    def test_group_names_expand_to_members(self):
        with ConfigDir(albums={"italy-2019": ITALY}, groups=GROUPS) as d:
            config = cfg.load(d.path)
        people, problems = config.expand_people(("Family Example",))
        self.assertEqual(people, ["Alex", "Sam", "Robin"])
        self.assertEqual(problems, [])

    def test_a_plain_person_name_passes_through(self):
        with ConfigDir(groups=GROUPS) as d:
            config = cfg.load(d.path)
        self.assertEqual(config.expand_people(("Alex",))[0], ["Alex"])

    def test_a_group_and_a_person_together_deduplicate(self):
        with ConfigDir(groups=GROUPS) as d:
            config = cfg.load(d.path)
        people, _ = config.expand_people(("Family Example", "Alex"))
        self.assertEqual(people, ["Alex", "Sam", "Robin"])

    def test_a_group_listing_itself_is_reported(self):
        with ConfigDir(groups='["Loop"]\nmembers = ["Loop", "Alex"]\n') as d:
            config = cfg.load(d.path)
        self.assertIn("itself", config.errors[0])
        self.assertEqual(config.groups, {})

    def test_an_empty_group_is_reported(self):
        with ConfigDir(groups='["Nobody"]\nmembers = []\n') as d:
            self.assertIn("no members", cfg.load(d.path).errors[0])

    def test_a_name_that_is_both_a_group_and_a_member_is_refused(self):
        groups = '["Alex"]\nmembers = ["Sam"]\n["Family Example"]\nmembers = ["Alex"]\n'
        with ConfigDir(albums={"x": 'name = "X"\n[match]\npeople = ["Alex"]\n'},
                       groups=groups) as d:
            self.assertIn("both a group", cfg.load(d.path).errors[0])


class WritingTests(unittest.TestCase):
    """Design mode writes these files; they must load back identically."""

    def test_an_album_round_trips_through_toml(self):
        with ConfigDir(albums={"italy-2019": ITALY}, groups=GROUPS) as d:
            original = cfg.load(d.path).albums[0]
            cfg.write_album(d.path, original)
            reloaded = cfg.load(d.path).albums[0]
        self.assertEqual(original.name, reloaded.name)
        self.assertEqual(original.match, reloaded.match)
        self.assertEqual(str(original.schedule), str(reloaded.schedule))
        self.assertEqual(original.sync, reloaded.sync)

    def test_a_mirror_person_album_round_trips(self):
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM}) as d:
            original = cfg.load(d.path).albums[0]
            cfg.write_album(d.path, original)
            reloaded = cfg.load(d.path).albums[0]
        self.assertTrue(reloaded.mirrors)
        self.assertEqual(reloaded.match.people, ("Alex",))

    def test_an_inherited_schedule_is_written_as_a_comment_not_a_value(self):
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM}) as d:
            album = cfg.load(d.path).albums[0]
            text = cfg.dump_album(album)
        self.assertIn("# auto-update-schedule", text)
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM}) as d:
            cfg.write_album(d.path, album)
            self.assertTrue(cfg.load(d.path).albums[0].schedule_inherited)

    def test_quotes_in_a_name_survive_the_round_trip(self):
        with ConfigDir(albums={"x": 'name = "X"\n[match]\npeople = ["Alex"]\n'}) as d:
            album = cfg.load(d.path).albums[0]
            from dataclasses import replace
            album = replace(album, name='The "Big" Trip')
            cfg.write_album(d.path, album)
            self.assertEqual(cfg.load(d.path).albums[0].name, 'The "Big" Trip')

    def test_groups_round_trip(self):
        groups = {"Family Example": ("Alex", "Sam"), "Book Club": ("Robin",)}
        with ConfigDir() as d:
            cfg.write_groups(d.path, groups)
            self.assertEqual(cfg.load(d.path).groups, groups)

    def test_slugify_makes_a_safe_non_empty_file_name(self):
        self.assertEqual(cfg.slugify("Italy 2019"), "italy-2019")
        self.assertEqual(cfg.slugify("Photos of Alex!"), "photos-of-alex")
        self.assertEqual(cfg.slugify("!!!"), "album")


if __name__ == "__main__":
    unittest.main()
