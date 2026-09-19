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
[groups."Family Example"]
members = ["Alex", "Sam", "Robin"]
"""


class ConfigDir:
    """A throwaway config directory holding one config.toml, built from strings.

    Albums are given here as the body of a rule and pasted under their own
    `[albums.<slug>]` heading, which keeps these tests about what a rule means
    rather than about where its heading sits.
    """

    def __init__(self, settings=GLOBAL, albums=None, groups=None):
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name)
        if settings is None:
            return
        parts = [settings]
        if groups is not None:
            parts.append(groups)
        for slug, text in (albums or {}).items():
            body = text.replace("[match]", f"[albums.{slug}.match]")
            parts.append(f"\n[albums.{slug}]\n{body}")
        (self.path / "config.toml").write_text("\n".join(parts), encoding="utf-8")

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
        self.assertIn("[albums.broken]", config.errors[0])

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

    def test_a_bad_schedule_names_the_album(self):
        with ConfigDir(albums={"odd": 'name = "O"\nauto-update-schedule = "hourly"\n'
                                      '[match]\npeople = ["Alex"]\n'}) as d:
            self.assertIn("[albums.odd]", cfg.load(d.path).errors[0])

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
        with ConfigDir(groups='[groups."Loop"]\nmembers = ["Loop", "Alex"]\n') as d:
            config = cfg.load(d.path)
        self.assertIn("itself", config.errors[0])
        self.assertEqual(config.groups, {})

    def test_an_empty_group_is_reported(self):
        with ConfigDir(groups='[groups."Nobody"]\nmembers = []\n') as d:
            self.assertIn("no members", cfg.load(d.path).errors[0])

    def test_a_name_that_is_both_a_group_and_a_member_is_refused(self):
        groups = ('[groups."Alex"]\nmembers = ["Sam"]\n'
                  '[groups."Family Example"]\nmembers = ["Alex"]\n')
        with ConfigDir(albums={"x": 'name = "X"\n[match]\npeople = ["Alex"]\n'},
                       groups=groups) as d:
            self.assertIn("both a group", cfg.load(d.path).errors[0])


class WritingTests(unittest.TestCase):
    """Design mode rewrites config.toml whole; it must load back identically."""

    def test_an_album_round_trips_through_toml(self):
        with ConfigDir(albums={"italy-2019": ITALY}, groups=GROUPS) as d:
            config = cfg.load(d.path)
            original = config.albums[0]
            cfg.write_config(d.path, config)
            reloaded = cfg.load(d.path).albums[0]
        self.assertEqual(original.name, reloaded.name)
        self.assertEqual(original.match, reloaded.match)
        self.assertEqual(str(original.schedule), str(reloaded.schedule))
        self.assertEqual(original.sync, reloaded.sync)

    def test_a_mirror_person_album_round_trips(self):
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM}) as d:
            config = cfg.load(d.path)
            cfg.write_config(d.path, config)
            reloaded = cfg.load(d.path).albums[0]
        self.assertTrue(reloaded.mirrors)
        self.assertEqual(reloaded.match.people, ("Alex",))

    def test_rewriting_keeps_the_settings_groups_and_every_album(self):
        """A save touches one album, so everything else must survive it."""
        with ConfigDir(albums={"italy-2019": ITALY, "photos-of-alex": PERSON_ALBUM},
                       groups=GROUPS) as d:
            before = cfg.load(d.path)
            cfg.write_config(d.path, before)
            after = cfg.load(d.path)
        self.assertEqual(after.settings.server, before.settings.server)
        self.assertEqual(after.settings.timezone, before.settings.timezone)
        self.assertEqual(str(after.settings.schedule), str(before.settings.schedule))
        self.assertEqual(after.groups, before.groups)
        self.assertEqual([a.slug for a in after.albums],
                         [a.slug for a in before.albums])
        self.assertEqual(after.errors, [])

    def test_a_login_hash_survives_a_save(self):
        """Design mode writes the file the logins live in. Losing one would
        lock the user out of the tool that just saved."""
        block = (f'\n[[design.users]]\nname = "designer"\n'
                 f'password = "{DesignUserTests.HASH}"\n')
        with ConfigDir(settings=GLOBAL + block,
                       albums={"photos-of-alex": PERSON_ALBUM}) as d:
            config = cfg.load(d.path)
            cfg.write_config(d.path, config)
            users = cfg.load(d.path).settings.design_users
        self.assertEqual([u.name for u in users], ["designer"])
        self.assertEqual(users[0].password_hash, DesignUserTests.HASH)

    def test_adding_an_album_leaves_the_others_alone(self):
        with ConfigDir(albums={"italy-2019": ITALY}, groups=GROUPS) as d:
            config = cfg.load(d.path)
            extra = cfg.Album(slug="photos-of-sam", name="Photos of Sam",
                              match=cfg.MatchRule(people=("Sam",)),
                              schedule=config.settings.schedule,
                              schedule_inherited=True)
            cfg.write_config(d.path, config.with_album(extra))
            after = cfg.load(d.path)
        self.assertEqual([a.slug for a in after.albums],
                         ["italy-2019", "photos-of-sam"])
        self.assertEqual(after.errors, [])

    def test_deleting_an_album_removes_only_that_one(self):
        with ConfigDir(albums={"italy-2019": ITALY, "photos-of-alex": PERSON_ALBUM}) as d:
            config = cfg.load(d.path)
            cfg.write_config(d.path, config.without_album("photos-of-alex"))
            after = cfg.load(d.path)
        self.assertEqual([a.slug for a in after.albums], ["italy-2019"])

    def test_an_inherited_schedule_is_written_as_a_comment_not_a_value(self):
        with ConfigDir(albums={"photos-of-alex": PERSON_ALBUM}) as d:
            config = cfg.load(d.path)
            self.assertIn("# auto-update-schedule", cfg.dump_album(config.albums[0]))
            cfg.write_config(d.path, config)
            self.assertTrue(cfg.load(d.path).albums[0].schedule_inherited)

    def test_quotes_in_a_name_survive_the_round_trip(self):
        with ConfigDir(albums={"x": 'name = "X"\n[match]\npeople = ["Alex"]\n'}) as d:
            config = cfg.load(d.path)
            from dataclasses import replace
            renamed = replace(config.albums[0], name='The "Big" Trip')
            cfg.write_config(d.path, config.with_album(renamed))
            self.assertEqual(cfg.load(d.path).albums[0].name, 'The "Big" Trip')

    def test_a_group_name_with_spaces_survives_the_round_trip(self):
        groups = {"Family Example": ("Alex", "Sam"), "Book Club": ("Robin",)}
        with ConfigDir() as d:
            cfg.write_config(d.path, cfg.load(d.path).with_groups(groups))
            self.assertEqual(cfg.load(d.path).groups, groups)

    def test_slugify_makes_a_safe_non_empty_key(self):
        self.assertEqual(cfg.slugify("Italy 2019"), "italy-2019")
        self.assertEqual(cfg.slugify("Photos of Alex!"), "photos-of-alex")
        self.assertEqual(cfg.slugify("!!!"), "album")


class DesignUserTests(unittest.TestCase):
    """`[[design.users]]`: the login that stands in front of the library."""

    HASH = ("scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA=="
            "$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

    def settings(self, block):
        return GLOBAL + block

    def test_no_users_configured_is_allowed(self):
        with ConfigDir() as d:
            self.assertEqual(cfg.load(d.path).settings.design_users, ())

    def test_a_user_is_read_with_its_hash(self):
        block = f'\n[[design.users]]\nname = "designer"\npassword = "{self.HASH}"\n'
        with ConfigDir(settings=self.settings(block)) as d:
            users = cfg.load(d.path).settings.design_users
        self.assertEqual([u.name for u in users], ["designer"])
        self.assertEqual(users[0].password_hash, self.HASH)

    def test_several_users_are_read(self):
        block = (f'\n[[design.users]]\nname = "alex"\npassword = "{self.HASH}"\n'
                 f'\n[[design.users]]\nname = "sam"\npassword = "{self.HASH}"\n')
        with ConfigDir(settings=self.settings(block)) as d:
            users = cfg.load(d.path).settings.design_users
        self.assertEqual([u.name for u in users], ["alex", "sam"])

    def test_a_plaintext_password_is_refused_with_the_command_to_run(self):
        """A config file must not become a password file."""
        block = '\n[[design.users]]\nname = "designer"\npassword = "hunter2"\n'
        with ConfigDir(settings=self.settings(block)) as d:
            with self.assertRaises(cfg.ConfigError) as caught:
                cfg.load(d.path)
        self.assertIn("passwd designer", str(caught.exception))

    def test_a_user_without_a_password_is_refused(self):
        block = '\n[[design.users]]\nname = "designer"\n'
        with ConfigDir(settings=self.settings(block)) as d:
            with self.assertRaises(cfg.ConfigError):
                cfg.load(d.path)

    def test_a_user_without_a_name_is_refused(self):
        block = f'\n[[design.users]]\npassword = "{self.HASH}"\n'
        with ConfigDir(settings=self.settings(block)) as d:
            with self.assertRaises(cfg.ConfigError):
                cfg.load(d.path)

    def test_two_users_with_the_same_name_are_refused(self):
        block = (f'\n[[design.users]]\nname = "designer"\npassword = "{self.HASH}"\n'
                 f'\n[[design.users]]\nname = "Designer"\npassword = "{self.HASH}"\n')
        with ConfigDir(settings=self.settings(block)) as d:
            with self.assertRaises(cfg.ConfigError) as caught:
                cfg.load(d.path)
        self.assertIn("named", str(caught.exception))

    def test_the_example_config_shipped_with_the_repo_parses(self):
        """The example is what people copy, so it must actually load."""
        example = Path(__file__).resolve().parent.parent / "examples" / "config.toml"
        with ConfigDir(settings=example.read_text(encoding="utf-8")) as d:
            settings = cfg.load(d.path).settings
        self.assertEqual([u.name for u in settings.design_users], ["designer"])

if __name__ == "__main__":
    unittest.main()
