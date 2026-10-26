"""The shared tag protocol, exercised over HTTP by independent clients."""

import unittest
from unittest import mock

from immich_album_butler.immich import ImmichClient, ImmichError
from immich_album_butler.rejections import TAG
from .fake_immich import FakeImmich


class RejectionsTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeImmich(extra=3).start()
        self.addCleanup(self.fake.stop)
        self.client = ImmichClient(self.fake.url, self.fake.API_KEY)
        self.asset = self.fake.assets[0]

    def test_reads_do_not_create_a_tag(self):
        self.assertEqual(self.client.rejections.ids(), frozenset())
        self.assertFalse(self.fake.tags)
        self.assertFalse(any(m in ("PUT", "DELETE") for m, _, _ in self.fake.requests))

    def test_reject_and_undo_from_another_client_preserve_stars_and_other_tags(self):
        self.asset.rating = 4
        self.asset.tags.append("unrelated-tag")
        self.client.rejections.set(self.asset.id, True)
        other = ImmichClient(self.fake.url, self.fake.API_KEY)
        self.assertIn(self.asset.id, other.rejections.ids())
        self.assertEqual(self.asset.rating, 4)
        other.rejections.set(self.asset.id, False)
        self.assertNotIn(self.asset.id, self.client.rejections.ids(force=True))
        self.assertEqual(self.asset.tags, ["unrelated-tag"])
        self.assertEqual(self.asset.rating, 4)
        self.assertFalse(any(m == "PUT" and p.startswith("/api/assets/")
                             for m, p, _ in self.fake.requests))

    def test_retrying_reject_and_undo_is_idempotent(self):
        for rejected in (True, True, False, False):
            self.client.rejections.set(self.asset.id, rejected)
            self.assertEqual(self.asset.id in self.client.rejections.ids(), rejected)
        self.assertEqual(len([t for t in self.fake.tags.values() if t["value"] == TAG]), 1)

    def test_cached_reads_refresh_and_do_not_hide_remote_undo_forever(self):
        self.client.rejections.set(self.asset.id, True)
        before = len(self.fake.requests)
        self.client.rejections.ids()
        self.assertEqual(before, len(self.fake.requests))
        other = ImmichClient(self.fake.url, self.fake.API_KEY)
        other.rejections.set(self.asset.id, False)
        with mock.patch("time.monotonic", return_value=self.client.rejections._expires + 1):
            self.assertNotIn(self.asset.id, self.client.rejections.ids())

    def test_success_status_without_membership_change_is_an_error(self):
        original = self.fake.answer

        def ignore_write(method, path, query, body):
            if path.startswith("/api/tags/") and path.endswith("/assets"):
                return 200, [{"id": self.asset.id, "success": False}]
            return original(method, path, query, body)

        self.fake.answer = ignore_write
        with self.assertRaisesRegex(ImmichError, "did not save"):
            self.client.rejections.set(self.asset.id, True)
        self.assertNotIn(self.asset.id, self.client.rejections.ids())

    def test_failed_refresh_does_not_publish_an_empty_snapshot(self):
        self.client.rejections.set(self.asset.id, True)
        self.fake.fail_next = 1
        with self.assertRaises(ImmichError):
            self.client.rejections.ids(force=True)
        self.assertIn(self.asset.id, self.client.rejections._ids)

    def test_all_membership_pages_are_read(self):
        self.client.rejections.set(self.asset.id, True)
        tag_id = next(t["id"] for t in self.fake.tags.values() if t["value"] == TAG)
        for asset in self.fake.assets:
            asset.tags = [tag_id]
        original = self.fake._metadata
        self.fake._metadata = lambda body: original({**body, "size": 1})
        self.assertEqual(self.client.rejections.ids(force=True),
                         frozenset(a.id for a in self.fake.assets))
