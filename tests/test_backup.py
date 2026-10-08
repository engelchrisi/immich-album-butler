"""Album backup and restore, against the fake Immich."""

import copy
import datetime as dt
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from immich_album_butler import backup
from immich_album_butler.design.api import ApiError, DesignApi
from immich_album_butler.immich import ImmichClient
from immich_album_butler.state import State

from .stub_immich import API_KEY, StubImmich, day, fake_id, make_asset

LIBRARY = [
    make_asset(1, when=day(2019, 7, 1), lat=41.9, lon=12.5, city="Rome",
               country="Italy"),
    make_asset(2, when=day(2019, 7, 2), city="Rome", country="Italy"),
    make_asset(3, when=day(2020, 1, 1), city="Madrid", country="Spain"),
]

CONFIG_TOML = """\
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
"""


class BackupCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.config_dir = root / "config"
        self.state_dir = root / "state"
        self.config_dir.mkdir()
        (self.config_dir / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")
        self.stub = self.start(LIBRARY)
        album = self.stub.add_album("Italy 2019", [fake_id(1), fake_id(2)],
                                    description="Summer")
        album["albumThumbnailAssetId"] = fake_id(2)
        self.album_id = album["id"]
        state = State.load(self.state_dir)
        state.for_album("italy-2019").album_id = self.album_id
        state.save()

    def start(self, assets):
        stub = StubImmich(assets, page_size=100)
        stub.__enter__()
        self.addCleanup(stub.__exit__, None, None, None)
        stub.client = ImmichClient(stub.url, API_KEY)
        return stub

    def make(self):
        path = backup.create_backup(self.stub.client, self.config_dir,
                                    self.state_dir)
        return path, backup.load_backup(path)

    def writes(self, stub):
        return [r for r in stub.requests if r[0] != "GET" and r[1] != "/api/search/metadata"]


class CreateTests(BackupCase):
    def test_backup_holds_albums_assets_config_and_state(self):
        path, data = self.make()
        album, = data["immich_albums"]
        self.assertEqual(album["name"], "Italy 2019")
        self.assertEqual(album["description"], "Summer")
        self.assertEqual(album["cover_asset_id"], fake_id(2))
        first = album["assets"][0]
        self.assertEqual(first["id"], fake_id(1))
        self.assertEqual(first["checksum"], "checksum-0001")
        self.assertEqual(first["file_name"], "IMG_0001.jpg")
        self.assertEqual(first["exif"]["city"], "Rome")
        self.assertEqual(data["butler"]["config_toml"], CONFIG_TOML)
        self.assertEqual(data["butler"]["state_albums"],
                         {"italy-2019": self.album_id})
        self.assertEqual(backup.list_backups(path.parent)[0].assets, 2)

    @unittest.skipIf(os.name == "nt", "no POSIX modes on Windows")
    def test_backup_is_private(self):
        path, _ = self.make()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)

    def test_albums_owned_by_others_are_left_out(self):
        other = self.stub.add_album("Theirs", [fake_id(3)])
        other["albumUsers"] = [{"user": {"id": "user-other"}, "role": "owner"}]
        _, data = self.make()
        self.assertEqual([a["name"] for a in data["immich_albums"]],
                         ["Italy 2019"])

    def test_rejects_a_file_that_is_not_a_backup(self):
        bad = Path(self._temp.name) / "backup-bad.json"
        bad.write_text(json.dumps({"hello": 1}), encoding="utf-8")
        with self.assertRaises(backup.BackupError):
            backup.load_backup(bad)

    def test_list_is_newest_first_with_album_names(self):
        older = backup.create_backup(
            self.stub.client, self.config_dir, self.state_dir,
            now=dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc))
        newer, _ = self.make()
        listed = backup.list_backups(newer.parent)
        self.assertEqual([b.name for b in listed], [newer.name, older.name])
        self.assertEqual(listed[0].album_names, ("Italy 2019",))

    def test_delete_removes_only_named_backups(self):
        path, _ = self.make()
        keep = path.parent / "notes.txt"
        keep.write_text("x", encoding="utf-8")
        self.assertEqual(backup.delete_backups(path.parent, [path.name]), [path.name])
        self.assertFalse(path.exists())
        self.assertTrue(keep.exists())
        for bad in ("notes.txt", "../backup-x.json", "backup-x.txt"):
            with self.assertRaises(backup.BackupError):
                backup.delete_backups(path.parent, [bad])

    def test_latest_resolves_the_newest(self):
        path, _ = self.make()
        self.assertEqual(backup.resolve_backup(path.parent, "latest"), path)


class RestoreTests(BackupCase):
    def restore(self, stub, data, **kwargs):
        return backup.restore(stub.client, data, self.config_dir,
                              self.state_dir, **kwargs)

    def test_missing_album_is_recreated_with_cover_and_state(self):
        _, data = self.make()
        fresh = self.start(LIBRARY)
        report = self.restore(fresh, data)
        album = fresh.album_named("Italy 2019")
        self.assertEqual(report.albums[0].action, "create")
        self.assertEqual({a["id"] for a in album["assets"]},
                         {fake_id(1), fake_id(2)})
        self.assertEqual(album["description"], "Summer")
        self.assertEqual(album["albumThumbnailAssetId"], fake_id(2))
        self.assertEqual(State.load(self.state_dir).albums["italy-2019"].album_id,
                         album["id"])

    def test_existing_album_only_gains_missing_assets(self):
        _, data = self.make()
        self.stub.albums[self.album_id]["assets"] = [{"id": fake_id(1)}]
        report = self.restore(self.stub, data)
        self.assertEqual(report.albums[0].action, "extend")
        self.assertEqual(report.albums[0].added, 1)
        self.assertEqual(len(self.stub.albums[self.album_id]["assets"]), 2)

    def test_complete_album_is_left_alone(self):
        _, data = self.make()
        before = len(self.stub.requests)
        report = self.restore(self.stub, data)
        self.assertEqual(report.albums[0].action, "skip")
        self.assertEqual(self.writes(self.stub), [])
        self.assertGreater(len(self.stub.requests), before)

    def test_nothing_is_ever_removed_or_deleted(self):
        _, data = self.make()
        self.stub.albums[self.album_id]["assets"].append({"id": fake_id(3)})
        self.restore(self.stub, data)
        self.assertEqual(len(self.stub.albums[self.album_id]["assets"]), 3)
        self.assertFalse([r for r in self.stub.requests if r[0] == "DELETE"])

    def test_dry_run_writes_nothing(self):
        _, data = self.make()
        fresh = self.start(LIBRARY)
        report = self.restore(fresh, data, dry_run=True)
        self.assertEqual(report.albums[0].to_add, 2)
        self.assertEqual(self.writes(fresh), [])
        self.assertEqual(fresh.albums, {})

    def test_asset_with_a_new_id_is_found_by_checksum(self):
        _, data = self.make()
        moved = copy.deepcopy(data)
        moved["immich_albums"][0]["assets"][0]["id"] = fake_id(99)   # gone
        fresh = self.start(LIBRARY)
        report = self.restore(fresh, moved)
        self.assertEqual(report.albums[0].remapped, 1)
        self.assertEqual(report.albums[0].unmatched, [])
        self.assertEqual({a["id"] for a in fresh.album_named("Italy 2019")["assets"]},
                         {fake_id(1), fake_id(2)})

    def test_asset_missing_everywhere_is_reported(self):
        _, data = self.make()
        fresh = self.start(LIBRARY[1:])                  # photo 1 is gone
        report = self.restore(fresh, data)
        self.assertEqual(report.albums[0].unmatched, ["IMG_0001.jpg"])
        self.assertEqual(report.albums[0].added, 1)

    def test_only_restricts_to_one_album(self):
        _, data = self.make()
        fresh = self.start(LIBRARY)
        report = self.restore(fresh, data, only="Other")
        self.assertEqual(report.albums, [])
        self.assertEqual(fresh.albums, {})

    def test_config_is_only_restored_on_request_and_old_one_kept(self):
        _, data = self.make()
        current = self.config_dir / "config.toml"
        current.write_text(CONFIG_TOML.replace("daily 03:30", "manual"),
                           encoding="utf-8")
        self.restore(self.stub, data)
        self.assertIn("manual", current.read_text(encoding="utf-8"))

        report = self.restore(self.stub, data, restore_config=True)
        self.assertTrue(report.config_restored)
        self.assertEqual(current.read_text(encoding="utf-8"), CONFIG_TOML)
        self.assertIn("manual", (self.config_dir / "config.toml.bak")
                      .read_text(encoding="utf-8"))

    def test_broken_config_in_a_backup_is_not_restored(self):
        _, data = self.make()
        data["butler"]["config_toml"] = "not = [valid"
        report = self.restore(self.stub, data, restore_config=True)
        self.assertFalse(report.config_restored)
        self.assertEqual((self.config_dir / "config.toml").read_text(encoding="utf-8"),
                         CONFIG_TOML)


class DesignApiTests(BackupCase):
    def setUp(self):
        super().setUp()
        self.api = DesignApi(self.stub.client, self.config_dir, self.state_dir)

    def test_create_list_and_restore(self):
        name = self.api.create_backup()["name"]
        listed = self.api.backups()["backups"]
        self.assertEqual([b["name"] for b in listed], [name])
        self.assertEqual(listed[0]["albums"], 1)
        result = self.api.restore_backup({"name": name, "dry_run": True})
        self.assertEqual(result["albums"][0]["action"], "skip")

    def test_list_shows_directory_and_create_returns_path(self):
        made = self.api.create_backup()
        listed = self.api.backups()
        self.assertTrue(listed["directory"])
        self.assertEqual(Path(made["path"]).parent, Path(listed["directory"]))
        self.assertEqual(listed["backups"][0]["album_names"], ["Italy 2019"])

    def test_delete_backups(self):
        name = self.api.create_backup()["name"]
        self.assertEqual(self.api.delete_backups({"names": [name]})["deleted"], [name])
        self.assertEqual(self.api.backups()["backups"], [])
        for bad in ({}, {"names": []}, {"names": ["../config.toml"]}):
            with self.assertRaises(ApiError):
                self.api.delete_backups(bad)

    def test_restore_refuses_a_path(self):
        for bad in ("../x.json", "", "/etc/passwd"):
            with self.assertRaises(ApiError):
                self.api.restore_backup({"name": bad})


if __name__ == "__main__":
    unittest.main()
