import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from immich_album_butler import config as cfg

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "migrate-hints.py"

# The hyphenated file name is not importable, so load it by path.
spec = importlib.util.spec_from_file_location("migrate_hints", SCRIPT)
migrate_hints = importlib.util.module_from_spec(spec)
sys.modules["migrate_hints"] = migrate_hints
spec.loader.exec_module(migrate_hints)

# Every removed key, on albums of every rule shape.
OLD = '''server = "http://immich.example.lan:2283"
timezone = "Europe/Rome"

# A trip.
[albums.italy-2019]
name = "Italy 2019"
hint_kind = "trip"
hint_order = "time-asc"
caption = "place"

  [albums.italy-2019.match]
  from = 2019-07-01
  to   = 2019-07-21
  countries = ["Italy"]

[albums.alex-birthday]
name = "Alex's birthday"
auto-update-schedule = "weekly sun 04:00"
pics_per_year = 5
pick = "rotate"
active = "05-16..05-18"
dwell = 8
activity = "off"   # keep the clock quiet

  [albums.alex-birthday.match]
  people  = ["Alex"]
  on_from = "05-16"
  on_to   = "05-18"

[albums.photos-of-alex]
name = "Photos of Alex"
slot = "1-3"

  [albums.photos-of-alex.match]
  people = ["Alex"]

[albums.tuscany]
name = "Tuscany"

  [albums.tuscany.match]
  countries = ["Italy"]
  cities = ["Siena"]

[albums.year-2019]
name = "2019"

  [albums.year-2019.match]
  from = 2019-01-01
  to   = 2019-12-31

[albums.chunked]
name = "Already chunked"
chunk = "1/month"
hint_order = "random"

  [albums.chunked.match]
  people = ["Alex"]

[albums.no-hint]
name = "No hint"

  [albums.no-hint.match]
  include_videos = false
  from = 2019-07-01
'''


def migrated(text: str = OLD):
    return migrate_hints.migrate(text)


class MigrateTests(unittest.TestCase):
    def test_the_result_is_a_config_the_butler_loads(self):
        new, _, _ = migrated()
        self.assertTrue(self._load(OLD).errors)       # the old one is refused
        config = self._load(new)
        self.assertEqual(config.errors, [])
        albums = {a.slug: a for a in config.albums}
        self.assertEqual((albums["italy-2019"].chunk, albums["italy-2019"].chunk_order),
                         ("3/day", "random"))
        self.assertEqual((albums["alex-birthday"].chunk,
                          albums["alex-birthday"].chunk_order),
                         ("3/year", "chronological"))
        self.assertEqual((albums["photos-of-alex"].chunk,
                          albums["photos-of-alex"].chunk_order),
                         ("5/year", "random"))
        self.assertEqual(albums["tuscany"].chunk, "5/year")

    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "config.toml").write_text(text, encoding="utf-8")
            return cfg.load(Path(temp))

    def test_every_removed_key_goes(self):
        new, _, _ = migrated()
        for key in migrate_hints.REMOVED_KEYS:
            self.assertNotRegex(new, rf"(?m)^\s*{key}\s*=", key)

    def test_an_existing_chunk_is_kept(self):
        new, _, _ = migrated()
        self.assertIn('chunk = "1/month"', new)
        self.assertNotIn('"1/month"\nchunk', new)

    def test_the_chunk_lands_in_the_album_table_not_in_match(self):
        new, _, _ = migrated()
        album = new.split("[albums.tuscany]")[1].split("[albums.tuscany.match]")[0]
        self.assertIn('chunk = "5/year"', album)

    def test_comments_and_layout_survive(self):
        new, _, _ = migrated()
        self.assertIn("# A trip.\n", new)
        self.assertIn('pick = "rotate"', new)
        self.assertIn("  people  = [\"Alex\"]", new)

    def test_it_is_idempotent(self):
        once, changes, _ = migrated()
        twice, again, by_hand = migrated(once)
        self.assertEqual(twice, once)
        self.assertEqual((again, by_hand), ([], []))
        self.assertTrue(changes)

    def test_an_album_with_nothing_to_go_on_stays_shuffled(self):
        new, _, _ = migrated('server = "x"\n[albums.a]\nname = "A"\nslot = "1"\n'
                             '  [albums.a.match]\n  include_videos = false\n'
                             '  from = 2019-01-01\n')
        # a date window still counts; an empty rule would not load at all
        self.assertIn('chunk = "3/day"', new)
        none = migrate_hints.guess({"include_unlocated": True})
        self.assertIsNone(none)

    def test_the_shapes_it_cannot_tell_apart_are_listed(self):
        _, _, by_hand = migrated()
        listed = " ".join(by_hand)
        self.assertIn("alex-birthday", listed)       # birthday or Christmas?
        self.assertIn("year-2019", listed)           # trip or year in review?
        self.assertNotIn("italy-2019", listed)       # three weeks: a trip
        self.assertNotIn("photos-of-alex", listed)

    def test_the_changes_are_reported(self):
        _, changes, _ = migrated()
        self.assertIn('italy-2019: drop hint_kind = "trip"', changes)
        self.assertIn('italy-2019: add chunk = "3/day", chunk_order = "random"',
                      changes)

    def test_crlf_files_keep_their_line_endings(self):
        new, _, _ = migrated(OLD.replace("\n", "\r\n"))
        self.assertNotRegex(new, r"(?<!\r)\n")
        self.assertIn('chunk = "5/year"\r\n', new)


class CommandLineTests(unittest.TestCase):
    def run_main(self, *args):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = migrate_hints.main([str(a) for a in args])
        return code, out.getvalue()

    def test_a_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(OLD, encoding="utf-8")
            code, out = self.run_main(path)
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), OLD)
            self.assertFalse(path.with_name("config.toml.bak").exists())
            self.assertIn("dry run", out)

    def test_write_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(OLD, encoding="utf-8")
            code, _ = self.run_main(path, "--write")
            self.assertEqual(code, 0)
            self.assertEqual(path.with_name("config.toml.bak")
                             .read_text(encoding="utf-8"), OLD)
            self.assertNotIn("hint_kind", path.read_text(encoding="utf-8"))

    def test_a_second_write_changes_nothing_and_keeps_the_first_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(OLD, encoding="utf-8")
            self.run_main(path, "--write")
            migrated_text = path.read_text(encoding="utf-8")
            _, out = self.run_main(path, "--write")
            self.assertIn("nothing to change", out)
            self.assertEqual(path.read_text(encoding="utf-8"), migrated_text)
            self.assertEqual(path.with_name("config.toml.bak")
                             .read_text(encoding="utf-8"), OLD)

    def test_the_shipped_example_survives_a_migration(self):
        text = (REPO / "examples" / "config.toml").read_text(encoding="utf-8")
        new, _, _ = migrate_hints.migrate(text)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "config.toml").write_text(new, encoding="utf-8")
            self.assertEqual(cfg.load(Path(temp)).errors, [])


if __name__ == "__main__":
    unittest.main()
