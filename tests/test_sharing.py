"""`share_with`: letting another account on the same server see an album.

The album stays where it is and keeps its owner; another account is given a
role on it. So the tests below are mostly about restraint -- granting only what
is missing, never taking access away, and turning every kind of refusal into a
warning rather than a failed run, because an album that cannot be shared must
still be an album that gets filled.
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

SAM_ID = "user-sam"
ROBIN_ID = "user-robin"
SAM = {"id": SAM_ID, "name": "Sam", "email": "sam@example.com"}
ROBIN = {"id": ROBIN_ID, "name": "Robin", "email": "robin@example.com"}
USERS = [SAM, ROBIN]

SHARED_ALBUM = """
name = "Photos of Alex"
share_with = ["Sam"]

[match]
people = ["Alex"]
"""


def as_editor(body: str) -> str:
    """The same rule, sharing as an editor rather than a viewer."""
    return body.replace('share_with = ["Sam"]',
                        'share_with = ["Sam"]\nshare_role = "editor"')


def assets():
    return [make_asset(1, when=day(2019, 7, 2), people=(ALEX,)),
            make_asset(2, when=day(2019, 7, 3), people=(ALEX,))]


def album_of(stub):
    return stub.album_named("Photos of Alex")


class ConfigTests(unittest.TestCase):
    def load(self, album_body: str) -> cfg.Config:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        (directory / "config.toml").write_text(
            'server = "http://immich.example.lan:2283"\n\n'
            "[albums.alex]\n" + album_body.replace(
                "[match]", "[albums.alex.match]"),
            encoding="utf-8")
        return cfg.load(directory)

    def test_an_album_shares_with_nobody_by_default(self):
        config = self.load('name = "Photos of Alex"\n\n[match]\npeople = ["Alex"]\n')
        album = config.album("alex")
        self.assertEqual(album.share_with, ())
        self.assertFalse(album.shares)

    def test_share_with_and_its_role_survive_a_rewrite(self):
        config = self.load(SHARED_ALBUM)
        album = config.album("alex")
        self.assertEqual(album.share_with, ("Sam",))
        self.assertEqual(album.share_role, "viewer")
        rewritten = cfg.dump_album(album)
        self.assertIn('share_with = ["Sam"]', rewritten)
        self.assertIn('share_role = "viewer"', rewritten)

    def test_one_name_may_be_written_without_a_list(self):
        config = self.load(SHARED_ALBUM.replace('["Sam"]', '"Sam"'))
        self.assertEqual(config.album("alex").share_with, ("Sam",))

    def test_the_same_name_twice_is_kept_once(self):
        config = self.load(SHARED_ALBUM.replace('["Sam"]', '["Sam", "sam"]'))
        self.assertEqual(config.album("alex").share_with, ("Sam",))

    def test_an_unknown_role_is_refused(self):
        config = self.load(SHARED_ALBUM.replace(
            'share_with = ["Sam"]', 'share_with = ["Sam"]\nshare_role = "owner"'))
        self.assertTrue(config.errors)
        self.assertIn("share_role", " ".join(config.errors))

    def test_a_share_with_that_is_not_a_string_is_refused(self):
        config = self.load(SHARED_ALBUM.replace('["Sam"]', "[3]"))
        self.assertTrue(config.errors)


class SharingTests(unittest.TestCase):
    def test_an_album_is_shared_with_the_account_it_names(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].shared, 1)
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "viewer"})

    def test_an_existing_album_is_shared_too(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            stub.add_album("Photos of Alex", [fake_id(1)])
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].shared, 1)
        self.assertEqual(reports[0].added, 1)
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "viewer"})

    def test_an_address_names_an_account_as_well_as_its_name(self):
        body = SHARED_ALBUM.replace('["Sam"]', '["sam@example.com"]')
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": body}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "viewer"})

    def test_a_second_run_shares_nothing_again(self):
        """Immich refuses to re-add a member, so this is not merely wasteful."""
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                stub.requests.clear()
                config, state = fx.load()
                again = run_once(fx.client, config, state)
        self.assertEqual(again[0].shared, 0)
        self.assertFalse(again[0].warnings)
        self.assertNotIn("/api/albums/", [path for method, path in stub.requests
                                          if method == "PUT" and
                                          path.endswith("/users")])

    def test_only_the_missing_account_is_added(self):
        both = SHARED_ALBUM.replace('["Sam"]', '["Sam", "Robin"]')
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
            with Fixture({"alex": both}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].shared, 1)
        self.assertEqual(stub.shared_with(album_of(stub)),
                         {SAM_ID: "viewer", ROBIN_ID: "viewer"})

    def test_a_changed_role_is_applied_to_an_existing_member(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
            with Fixture({"alex": as_editor(SHARED_ALBUM)}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].shared, 1)
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "editor"})

    def test_an_account_dropped_from_the_rule_keeps_its_access(self):
        """Taking access away is a human act, never a side effect of an edit."""
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
            plain = SHARED_ALBUM.replace('share_with = ["Sam"]\n', "")
            with Fixture({"alex": plain}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].shared, 0)
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "viewer"})

    def test_a_dry_run_reports_the_share_and_performs_none(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            stub.add_album("Photos of Alex", [fake_id(1)])
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state, dry_run=True)
        self.assertEqual(reports[0].shared, 1)
        self.assertEqual(stub.shared_with(album_of(stub)), {})

    def test_an_album_that_shares_with_nobody_never_asks_for_the_user_list(self):
        """No share_with anywhere means the key never needs `user.read`."""
        plain = SHARED_ALBUM.replace('share_with = ["Sam"]\n', "")
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": plain}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertNotIn("/api/users", [path for _, path in stub.requests])


class HandMadeAlbumTests(unittest.TestCase):
    """`[[shares]]`: albums the butler has no rule for, which is most of them."""

    def fixture(self, stub, shares: str, albums=None):
        fx = Fixture(albums or {}, stub)
        path = fx.config_dir / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + shares,
                        encoding="utf-8")
        return fx

    def test_an_album_with_no_rule_is_shared_by_name(self):
        shares = '\n[[shares]]\nalbums = ["Holiday snaps"]\nwith = ["Sam"]\n'
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            hand_made = stub.add_album("Holiday snaps", [fake_id(1)])
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[-1].shared, 1)
        self.assertEqual(stub.shared_with(hand_made), {SAM_ID: "viewer"})
        # Nothing was added to it: a share rule hands out access, no more.
        self.assertEqual(len(hand_made["assets"]), 1)

    def test_a_star_shares_every_album_this_account_owns(self):
        shares = '\n[[shares]]\nalbums = ["*"]\nwith = ["Sam"]\n'
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            first = stub.add_album("Holiday snaps", [fake_id(1)])
            second = stub.add_album("Birthdays", [fake_id(2)])
            with self.fixture(stub, shares, {"alex": SHARED_ALBUM}) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertEqual(stub.shared_with(first), {SAM_ID: "viewer"})
        self.assertEqual(stub.shared_with(second), {SAM_ID: "viewer"})
        # Including the butler's own, which the star does not skip.
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "viewer"})

    def test_a_star_leaves_somebody_elses_album_alone(self):
        """Only an owner can share an album on, and the listing holds both."""
        shares = '\n[[shares]]\nalbums = ["*"]\nwith = ["Sam"]\n'
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            theirs = stub.add_album("Robin's own", [fake_id(1)])
            theirs["albumUsers"] = [{"user": ROBIN, "role": "owner"}]
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[-1].shared, 0)
        self.assertEqual(stub.shared_with(theirs), {})

    def test_a_marked_album_is_found_by_its_unmarked_name(self):
        shares = '\n[[shares]]\nalbums = ["Holiday snaps"]\nwith = ["Sam"]\n'
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            marked = stub.add_album("Holiday snaps [AB]", [fake_id(1)])
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertEqual(stub.shared_with(marked), {SAM_ID: "viewer"})

    def test_an_album_that_is_not_there_warns_and_nothing_else_breaks(self):
        shares = ('\n[[shares]]\nalbums = ["Holiday snaps", "Gone"]\n'
                  'with = ["Sam"]\n')
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            hand_made = stub.add_album("Holiday snaps", [fake_id(1)])
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        report = reports[-1]
        self.assertTrue(report.ok)
        self.assertEqual(report.shared, 1)
        self.assertIn("Gone", " ".join(report.warnings))
        self.assertEqual(stub.shared_with(hand_made), {SAM_ID: "viewer"})

    def test_a_second_run_shares_nothing_again(self):
        shares = '\n[[shares]]\nalbums = ["*"]\nwith = ["Sam"]\n'
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            stub.add_album("Holiday snaps", [fake_id(1)])
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
                config, state = fx.load()
                again = run_once(fx.client, config, state)
        self.assertEqual(again[-1].shared, 0)
        self.assertFalse(again[-1].warnings)

    def test_a_dry_run_reports_the_shares_and_performs_none(self):
        shares = '\n[[shares]]\nalbums = ["*"]\nwith = ["Sam"]\n'
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            hand_made = stub.add_album("Holiday snaps", [fake_id(1)])
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state, dry_run=True)
        self.assertEqual(reports[-1].shared, 1)
        self.assertEqual(stub.shared_with(hand_made), {})

    def test_one_album_may_be_named_by_two_rules_in_different_roles(self):
        shares = ('\n[[shares]]\nalbums = ["Holiday snaps"]\nwith = ["Sam"]\n'
                  '\n[[shares]]\nalbums = ["Holiday snaps"]\nwith = ["Robin"]\n'
                  'role = "editor"\n')
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            hand_made = stub.add_album("Holiday snaps", [fake_id(1)])
            with self.fixture(stub, shares) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
        self.assertEqual(stub.shared_with(hand_made),
                         {SAM_ID: "viewer", ROBIN_ID: "editor"})

    def test_a_rule_without_accounts_is_refused(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        (directory / "config.toml").write_text(
            'server = "http://immich.example.lan:2283"\n'
            '\n[[shares]]\nalbums = ["Holiday snaps"]\n', encoding="utf-8")
        with self.assertRaises(cfg.ConfigError):
            cfg.load(directory)

    def test_share_rules_survive_a_rewrite(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        (directory / "config.toml").write_text(
            'server = "http://immich.example.lan:2283"\n'
            '\n[[shares]]\nalbums = ["*"]\nwith = ["Sam"]\nrole = "editor"\n',
            encoding="utf-8")
        config = cfg.load(directory)
        text = cfg.dump_config(config)
        self.assertIn("[[shares]]", text)
        self.assertIn('albums = ["*"]', text)
        self.assertIn('role   = "editor"', text)


class ProblemTests(unittest.TestCase):
    def test_an_unknown_account_warns_and_the_album_still_fills(self):
        body = SHARED_ALBUM.replace('["Sam"]', '["Nobody"]')
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": body}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        report = reports[0]
        self.assertTrue(report.ok)
        self.assertEqual(report.added, 2)
        self.assertEqual(report.shared, 0)
        self.assertIn("Nobody", " ".join(report.warnings))

    def test_two_accounts_with_the_same_name_warn_rather_than_guess(self):
        twin = {"id": "user-sam-2", "name": "Sam", "email": "sam2@example.com"}
        with StubImmich(assets(), people=PEOPLE, users=USERS + [twin],
                        page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertEqual(reports[0].shared, 0)
        self.assertEqual(stub.shared_with(album_of(stub)), {})
        self.assertIn("e-mail", " ".join(reports[0].warnings))

    def test_a_key_without_user_read_keeps_the_album_working(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2,
                        missing_permissions={"user.read"}) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        report = reports[0]
        self.assertTrue(report.ok)
        self.assertEqual(report.added, 2)
        self.assertEqual(report.shared, 0)
        self.assertIn("user.read", " ".join(report.warnings))

    def test_a_key_without_album_user_create_keeps_the_album_working(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2,
                        missing_permissions={"albumUser.create"}) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        report = reports[0]
        self.assertTrue(report.ok)
        self.assertEqual(report.added, 2)
        self.assertEqual(report.shared, 0)
        self.assertIn("albumUser.create", " ".join(report.warnings))
        self.assertEqual(stub.shared_with(album_of(stub)), {})

    def test_a_key_without_album_user_update_still_fills_and_warns(self):
        with StubImmich(assets(), people=PEOPLE, users=USERS, page_size=2) as stub:
            with Fixture({"alex": SHARED_ALBUM}, stub) as fx:
                config, state = fx.load()
                run_once(fx.client, config, state)
            stub.missing_permissions = {"albumUser.update"}
            with Fixture({"alex": as_editor(SHARED_ALBUM)}, stub) as fx:
                config, state = fx.load()
                reports = run_once(fx.client, config, state)
        self.assertTrue(reports[0].ok)
        self.assertEqual(reports[0].shared, 0)
        self.assertIn("albumUser.update", " ".join(reports[0].warnings))
        self.assertEqual(stub.shared_with(album_of(stub)), {SAM_ID: "viewer"})


if __name__ == "__main__":
    unittest.main()
