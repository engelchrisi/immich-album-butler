import datetime as dt
import tempfile
import unittest
from pathlib import Path

from immich_album_butler import config as cfg
from immich_album_butler.immich import ImmichClient
from immich_album_butler.runtime import Butler, run_once
from immich_album_butler.state import State

from .stub_immich import API_KEY, StubImmich, day, fake_id, make_asset

ALEX, SAM = "person-alex", "person-sam"
PEOPLE = [{"id": ALEX, "name": "Alex"}, {"id": SAM, "name": "Sam"}]

SETTINGS = """
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
"""

ITALY = """
name = "Italy 2019"

[match]
from = 2019-07-01
to   = 2019-07-31
countries = ["Italy"]
include_unlocated = true
"""

ALEX_ALBUM = """
name = "Photos of Alex"
sync = "mirror"

[match]
people = ["Alex"]
"""


def assets():
    return [
        make_asset(1, when=day(2019, 7, 2), lat=41.9, lon=12.5,
                   city="Rome", country="Italy", people=(ALEX,)),
        make_asset(2, when=day(2019, 7, 3), people=(ALEX, SAM)),
        make_asset(3, when=day(2019, 7, 4), lat=40.4, lon=-3.7,
                   city="Madrid", country="Spain", people=(SAM,)),
    ]


class Fixture:
    """A config directory, a state directory and a stub server, wired together."""

    def __init__(self, albums, stub):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.config_dir = root / "config"
        self.state_dir = root / "state"
        self.config_dir.mkdir(parents=True)
        self.state_dir.mkdir()
        parts = [SETTINGS.replace("http://immich.example.lan:2283", stub.url)]
        for slug, text in albums.items():
            parts.append(f"\n[albums.{slug}]\n"
                         + text.replace("[match]", f"[albums.{slug}.match]"))
        (self.config_dir / "config.toml").write_text("\n".join(parts),
                                                     encoding="utf-8")
        self.client = ImmichClient(stub.url, API_KEY)

    def load(self):
        return cfg.load(self.config_dir), State.load(self.state_dir)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._temp.cleanup()


class CreateTests(unittest.TestCase):
    def test_a_missing_album_is_created_with_its_matching_assets(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
                album = stub.album_named("Italy 2019")
        self.assertTrue(reports[0].created)
        self.assertEqual(reports[0].added, 2)
        self.assertEqual({a["id"] for a in album["assets"]},
                         {fake_id(1), fake_id(2)})

    def test_a_second_run_changes_nothing(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                config, state = fx.load()
                again = run_once(fx.client, config, state)
        self.assertEqual(again[0].added, 0)
        self.assertEqual(again[0].removed, 0)
        self.assertFalse(again[0].created)

    def test_new_photos_are_added_to_the_existing_album(self):
        library = assets()
        with StubImmich(library, people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                library.append(make_asset(9, when=day(2019, 7, 20), lat=41.9,
                                          lon=12.5, city="Rome", country="Italy"))
                config, state = fx.load()
                second = run_once(fx.client, config, state)
        self.assertEqual(second[0].added, 1)

    def test_the_album_id_is_remembered_in_state_not_in_the_config(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                saved = State.load(fx.state_dir)
                config_text = (fx.config_dir / "config.toml").read_text()
        self.assertTrue(saved.for_album("italy-2019").album_id)
        self.assertNotIn(saved.for_album("italy-2019").album_id, config_text)

    def test_an_album_created_by_hand_is_adopted_by_name(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            existing = stub.add_album("Italy 2019", [fake_id(1)])
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertFalse(reports[0].created)
        self.assertEqual(reports[0].added, 1)
        self.assertEqual(len(stub.albums), 1)
        self.assertEqual(len(existing["assets"]), 2)


class DryRunTests(unittest.TestCase):
    def test_a_dry_run_reports_the_work_but_creates_nothing(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state, dry_run=True)
        self.assertTrue(reports[0].created)
        self.assertEqual(reports[0].added, 2)
        self.assertEqual(stub.albums, {})

    def test_a_dry_run_leaves_no_state_behind(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state, dry_run=True)
                self.assertFalse((fx.state_dir / "state.json").exists())


class SyncModeTests(unittest.TestCase):
    def test_add_mode_leaves_a_manually_added_photo_alone(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                album = stub.album_named("Italy 2019")
                album["assets"].append({"id": fake_id(3)})   # a person added this
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertIn(fake_id(3), {a["id"] for a in album["assets"]})

    def test_moving_the_first_photo_in_and_out_removes_and_adds_in_add_mode(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                album = stub.album_named("Italy 2019")
                album["assets"].append({"id": fake_id(3)})   # a person added this
                path = fx.config_dir / "config.toml"
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace("from = 2019-07-01",
                                             "from = 2019-07-03T00:00:00"),
                                encoding="utf-8")
                config, state = fx.load()
                trimmed = run_once(fx.client, config, state)
                after_trim = {a["id"] for a in album["assets"]}
                path.write_text(text, encoding="utf-8")
                config, state = fx.load()
                widened = run_once(fx.client, config, state)
                after_widen = {a["id"] for a in album["assets"]}
        # Moved in: the photo before the new first one goes; the one added by
        # hand, within the ends, stays. Moved back out: it returns.
        self.assertEqual(trimmed[0].removed, 1)
        self.assertEqual(after_trim, {fake_id(2), fake_id(3)})
        self.assertEqual(widened[0].added, 1)
        self.assertEqual(after_widen, {fake_id(1), fake_id(2), fake_id(3)})

    def test_mirror_mode_removes_what_no_longer_matches(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"photos-of-alex": ALEX_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                album = stub.album_named("Photos of Alex")
                album["assets"].append({"id": fake_id(3)})   # not a photo of Alex
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].removed, 1)
        self.assertEqual({a["id"] for a in album["assets"]},
                         {fake_id(1), fake_id(2)})


ROTATE_ALBUM = """
name = "Rotating"
sync = "mirror"
auto-update-schedule = "weekly sun 04:00"
pics_per_year = 2
pick = "rotate"

[match]
people = ["Alex"]
"""


def alex_photos(count=6):
    return [make_asset(n, when=day(2019, 5, 17), people=(ALEX,))
            for n in range(1, count + 1)]


class RotationTests(unittest.TestCase):
    def members(self, stub):
        return {a["id"] for a in stub.album_named("Rotating")["assets"]}

    def test_two_consecutive_runs_pick_disjoint_sets_and_a_third_wraps(self):
        with StubImmich(alex_photos(), people=PEOPLE, page_size=100) as stub:
            with Fixture({"rot": ROTATE_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                first = self.members(stub)
                config, state = fx.load()
                run_once(fx.client, config, state)
                second = self.members(stub)
                config, state = fx.load()
                run_once(fx.client, config, state)
                third = self.members(stub)
        self.assertEqual(len(first), 2)
        self.assertFalse(first & second)               # disjoint
        self.assertEqual(first | second | third, {fake_id(n) for n in range(1, 7)})

    def test_a_dry_run_is_deterministic_and_writes_no_state(self):
        with StubImmich(alex_photos(), people=PEOPLE, page_size=100) as stub:
            with Fixture({"rot": ROTATE_ALBUM}, stub) as fx:
                config, state = fx.load()
                butler = Butler(fx.client, config, state)
                first = butler.plan(config.album("rot")).matched
                second = butler.plan(config.album("rot")).matched
                run_once(fx.client, config, state, dry_run=True)
                exists = (fx.state_dir / "state.json").exists()
        self.assertEqual(first, second)
        self.assertFalse(exists)


class DescribeTests(unittest.TestCase):
    def _enable_hint(self, fx):
        path = fx.config_dir / "config.toml"
        path.write_text('describe = "hint"\n' + path.read_text(encoding="utf-8"),
                        encoding="utf-8")

    def test_the_hint_is_written_and_not_rewritten_when_unchanged(self):
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                self._enable_hint(fx)
                config, state = fx.load()
                run_once(fx.client, config, state)
                description = stub.album_named("Italy 2019")["description"]
                config, state = fx.load()
                run_once(fx.client, config, state)
                patches = sum(1 for m, p in stub.requests if m == "PATCH")
        self.assertIn("[butler v1] kind=trip order=trip", description)
        self.assertEqual(patches, 0)     # set at creation, never rewritten


class FailureTests(unittest.TestCase):
    def test_one_broken_album_does_not_stop_the_others(self):
        broken = 'name = "Ghost"\n[match]\npeople = ["Nobody"]\n'
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": ITALY, "ghost": broken}, stub) as fx:
                config, state = fx.load()
                with self.assertLogs("immich_album_butler.runtime", "ERROR"):
                    reports = run_once(fx.client, config, state)
        by_slug = {r.slug: r for r in reports}
        self.assertFalse(by_slug["ghost"].ok)
        self.assertTrue(by_slug["italy-2019"].ok)
        self.assertTrue(by_slug["italy-2019"].created)

    def test_the_failure_is_recorded_so_it_can_be_seen_later(self):
        broken = 'name = "Ghost"\n[match]\npeople = ["Nobody"]\n'
        with StubImmich(assets(), people=PEOPLE) as stub:
            with Fixture({"ghost": broken}, stub) as fx:
                config, state = fx.load()
                with self.assertLogs("immich_album_butler.runtime", "ERROR"):
                    run_once(fx.client, config, state)
                saved = State.load(fx.state_dir)
        self.assertIn("Nobody", saved.for_album("ghost").last_error or "")


class ScheduleTests(unittest.TestCase):
    def test_only_albums_whose_schedule_has_come_round_are_due(self):
        with StubImmich(assets(), people=PEOPLE) as stub:
            with Fixture({"italy-2019": ITALY}, stub) as fx:
                config, state = fx.load()
                butler = Butler(fx.client, config, state)
                now = dt.datetime(2019, 8, 1, 4, 0, tzinfo=dt.timezone.utc)

                self.assertEqual(len(butler.due_albums(now)), 1)  # never run yet

                state.for_album("italy-2019").last_run = now.isoformat()
                self.assertEqual(butler.due_albums(now), [])

                tomorrow = now + dt.timedelta(days=1)
                self.assertEqual(len(butler.due_albums(tomorrow)), 1)

    def test_a_disabled_album_is_never_due(self):
        disabled = ITALY.replace('name = "Italy 2019"',
                                 'name = "Italy 2019"\nenabled = false')
        with StubImmich(assets(), people=PEOPLE) as stub:
            with Fixture({"italy-2019": disabled}, stub) as fx:
                config, state = fx.load()
                butler = Butler(fx.client, config, state)
                now = dt.datetime(2019, 8, 1, 4, tzinfo=dt.timezone.utc)
                self.assertEqual(butler.due_albums(now), [])

    def test_a_manual_album_is_never_due_but_can_still_be_run_by_name(self):
        manual = ITALY.replace('name = "Italy 2019"',
                               'name = "Italy 2019"\nauto-update-schedule = "manual"')
        with StubImmich(assets(), people=PEOPLE, page_size=2) as stub:
            with Fixture({"italy-2019": manual}, stub) as fx:
                config, state = fx.load()
                butler = Butler(fx.client, config, state)
                now = dt.datetime(2019, 8, 1, 4, tzinfo=dt.timezone.utc)
                self.assertEqual(butler.due_albums(now), [])
                reports = run_once(fx.client, config, state, only="italy-2019")
        self.assertTrue(reports[0].created)


if __name__ == "__main__":
    unittest.main()
