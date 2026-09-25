"""The Duplicates tab: albums holding more than one copy of a duplicate group.

Run against the fake Immich copied from PyImmichFrame, over a real socket.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from immich_album_butler.design.api import ApiError, DesignApi
from immich_album_butler.immich import ImmichClient

from .fake_immich import FakeImmich

CONFIG_TOML = """\
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
timezone = "UTC"
"""


class DuplicatesTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        (root / "config").mkdir()
        (root / "config" / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")

        # Assets 0-2 are in "Italy 2019", 3-4 in "Spain". 0 and 1 are one
        # duplicate group; 1 is also in "Spain", where it has no twin.
        self.fake = FakeImmich(albums={"Italy 2019": 3, "Spain": 2}).start()
        self.addCleanup(self.fake.stop)
        self.fake.mark_duplicates([0, 1])
        self.fake.add_to_album("Spain", 1)
        self.ids = [a.id for a in self.fake.assets]
        self.italy = self.fake.album_id("Italy 2019")

        client = ImmichClient(self.fake.url, self.fake.API_KEY)
        self.api = DesignApi(client, root / "config", root / "state")

    def remove(self, *indexes, album=None):
        return self.api.remove_duplicates(
            {"album_id": album or self.italy,
             "asset_ids": [self.ids[i] for i in indexes]})


class ListingTests(DuplicatesTestCase):
    def test_only_albums_holding_two_group_members_are_listed(self):
        albums = self.api.duplicates()["albums"]
        self.assertEqual([a["name"] for a in albums], ["Italy 2019"])
        self.assertEqual(albums[0]["removable"], 1)
        self.assertEqual(albums[0]["groups"], 1)
        self.assertEqual(albums[0]["cover"], self.ids[0])
        self.assertFalse(albums[0]["rule_managed"])

    def test_the_album_page_lists_its_groups(self):
        album = self.api.duplicate_album(self.italy)
        group = album["groups"][0]
        self.assertEqual([a["id"] for a in group["assets"]], self.ids[:2])
        self.assertEqual(group["keep"], self.ids[0])        # the earliest taken

    def test_an_album_without_duplicates_has_no_page(self):
        with self.assertRaises(ApiError) as caught:
            self.api.duplicate_album(self.fake.album_id("Spain"))
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(ApiError):
            self.api.duplicate_album("../x")

    def test_no_groups_means_nothing_listed(self):
        for asset in self.fake.assets:
            asset.duplicate_id = None
        self.assertEqual(self.api.duplicates()["albums"], [])

    def test_a_missing_permission_is_reported(self):
        self.fake.denied.add("/api/duplicates")
        with self.assertRaises(ApiError):
            self.api.duplicates()


class CacheTests(DuplicatesTestCase):
    def scans(self):
        return self.fake.count_requests("/api/duplicates")

    def test_the_scan_is_cached_on_disk_and_in_memory(self):
        self.api.duplicates()
        self.assertTrue((self.root / "state" / "duplicates.json").exists())
        self.api.duplicates()
        self.api.duplicate_album(self.italy)
        self.assertEqual(self.scans(), 1)

    def test_a_new_session_reads_the_file(self):
        self.api.duplicates()
        fresh = DesignApi(self.api.client, self.root / "config", self.root / "state")
        self.assertEqual(len(fresh.duplicates()["albums"]), 1)
        self.assertEqual(self.scans(), 1)

    def test_rescan_asks_immich_again(self):
        self.api.duplicates()
        self.fake.mark_duplicates([3, 4])
        albums = self.api.duplicates(rescan=True)["albums"]
        self.assertEqual(self.scans(), 2)
        self.assertEqual(len(albums), 2)

    def test_a_scan_from_yesterday_is_redone(self):
        self.api.duplicates()
        path = self.root / "state" / "duplicates.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["scanned_at"] = (dt.datetime.now() - dt.timedelta(days=1)).isoformat()
        path.write_text(json.dumps(data), encoding="utf-8")
        fresh = DesignApi(self.api.client, self.root / "config", self.root / "state")
        fresh.duplicates()
        self.assertEqual(self.scans(), 2)

    def test_a_removal_updates_the_cache(self):
        self.api.duplicates()
        self.remove(1)
        self.assertEqual(self.api.duplicates()["albums"], [])
        fresh = DesignApi(self.api.client, self.root / "config", self.root / "state")
        self.assertEqual(fresh.duplicates()["albums"], [])


class RemovalTests(DuplicatesTestCase):
    def test_the_copy_leaves_the_album_but_not_the_library(self):
        self.assertEqual(self.remove(1)["removed"], 1)
        self.assertEqual(self.fake.album_members("Italy 2019"),
                         [self.ids[0], self.ids[2]])
        self.assertIn(self.ids[1], self.fake.album_members("Spain"))
        self.assertEqual(len(self.fake.assets), 5)
        self.assertEqual(self.api.duplicates()["albums"], [])

    def test_the_later_copy_may_be_the_one_kept(self):
        self.remove(0)
        self.assertEqual(self.fake.album_members("Italy 2019"), self.ids[1:3])

    def test_a_whole_group_cannot_be_removed(self):
        with self.assertRaises(ApiError):
            self.remove(0, 1)
        self.assertEqual(len(self.fake.album_members("Italy 2019")), 3)

    def test_a_non_duplicate_cannot_be_removed(self):
        with self.assertRaises(ApiError):
            self.remove(1, 2)
        self.assertEqual(len(self.fake.album_members("Italy 2019")), 3)

    def test_an_album_without_duplicates_is_refused(self):
        with self.assertRaises(ApiError):
            self.remove(1, album=self.fake.album_id("Spain"))
        self.assertIn(self.ids[1], self.fake.album_members("Spain"))

    def test_a_bad_id_is_refused_before_asking_immich(self):
        before = len(self.fake.requests)
        with self.assertRaises(ApiError):
            self.api.remove_duplicates({"album_id": self.italy,
                                        "asset_ids": ["../x"]})
        self.assertEqual(len(self.fake.requests), before)

    def test_the_library_is_never_deleted_from(self):
        self.remove(1)
        self.assertFalse([p for m, p, _ in self.fake.requests
                          if m == "DELETE" and not p.startswith("/api/albums/")])


if __name__ == "__main__":
    unittest.main()
