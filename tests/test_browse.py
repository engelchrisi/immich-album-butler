"""The Browse tab: any Immich album's media, with what they are grouped by.

Run against the fake Immich copied from PyImmichFrame, over a real socket.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immich_album_butler.design.api import ApiError, DesignApi
from immich_album_butler.immich import ImmichClient, User

from .fake_immich import FakeImmich

CONFIG_TOML = """\
server = "http://immich.example.lan:2283"
auto-update-schedule = "manual"
timezone = "UTC"

[albums.italy-2019]
name = "Italy 2019"

[albums.italy-2019.match]
from = "2019-01-01"
to = "2019-12-31"
"""


class BrowseTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "config").mkdir()
        (root / "config" / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")

        # "Italy 2019" is kept by a rule; "Spain" is an album made by hand.
        self.fake = FakeImmich(albums={"Italy 2019": 3, "Spain": 2}).start()
        self.addCleanup(self.fake.stop)
        self.ids = [a.id for a in self.fake.assets]
        self.fake.assets[1].kind = "VIDEO"
        self.italy = self.fake.album_id("Italy 2019")
        self.spain = self.fake.album_id("Spain")

        self.client = ImmichClient(self.fake.url, self.fake.API_KEY)
        self.api = DesignApi(self.client, root / "config", root / "state")


class AlbumListTests(BrowseTestCase):
    def test_every_album_is_listed_not_only_the_butlers(self):
        albums = self.api.browse_albums()["albums"]
        self.assertEqual([a["name"] for a in albums], ["Italy 2019", "Spain"])
        self.assertEqual([a["asset_count"] for a in albums], [3, 2])
        self.assertEqual([a["butler"] for a in albums], [True, False])
        self.assertFalse(any(a["shared"] for a in albums))

    def test_albums_of_another_owner_are_marked_shared(self):
        other = User(id="00000000-0000-0000-0000-000000000009", name="Sam")
        with mock.patch.object(ImmichClient, "me", return_value=other):
            albums = self.api.browse_albums()["albums"]
        self.assertTrue(all(a["shared"] for a in albums))

    def test_an_unreachable_immich_is_a_502(self):
        self.fake.denied.add("/api/albums")
        with self.assertRaises(ApiError) as caught:
            self.api.browse_albums()
        self.assertEqual(caught.exception.status, 502)


class AlbumPageTests(BrowseTestCase):
    def test_an_album_brings_what_it_is_grouped_by(self):
        album = self.api.browse_album(self.spain)
        self.assertEqual(album["name"], "Spain")
        self.assertEqual([a["id"] for a in album["assets"]], self.ids[3:5])
        first = album["assets"][0]
        self.assertEqual(first["folder"], "/photos")
        self.assertEqual(first["file_name"], "IMG_0003.jpg")
        self.assertEqual(first["camera"], "Canon EOS R")
        self.assertEqual((first["city"], first["country"]), ("Vienna", "Austria"))
        self.assertTrue(first["taken_at"].startswith("2020-01-04"))

    def test_media_come_oldest_first_with_their_kind(self):
        album = self.api.browse_album(self.italy)
        self.assertEqual([a["id"] for a in album["assets"]], self.ids[:3])
        self.assertEqual([a["kind"] for a in album["assets"]],
                         ["IMAGE", "VIDEO", "IMAGE"])

    def test_a_bad_id_is_refused_before_asking_immich(self):
        before = len(self.fake.requests)
        with self.assertRaises(ApiError):
            self.api.browse_album("../albums")
        self.assertEqual(len(self.fake.requests), before)

    def test_an_unknown_album_is_a_404(self):
        with self.assertRaises(ApiError) as caught:
            self.api.browse_album("00000000-0000-0000-0000-000000000000")
        self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
