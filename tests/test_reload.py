"""Config edits take effect without restarting a running service.

`run_forever` (the daemon) rebuilds its Immich client every tick alongside the
config it already re-reads. Design mode's port is different: it is baked into
an already-bound socket, so `BindWatcher` waits for an edit to settle (or a
manual "Reload config" click) before asking the process to restart.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immich_album_butler.design.api import DesignApi
from immich_album_butler.immich import ImmichClient
from immich_album_butler.reload import BindWatcher
from immich_album_butler.runtime import run_forever

CONFIG = """\
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
timezone = "UTC"
"""

CONFIG_NEW_SERVER = """\
server = "http://immich2.example.lan:2283"
auto-update-schedule = "daily 03:30"
timezone = "UTC"
"""


class _StopLoop(Exception):
    """Breaks `run_forever`'s `while True` once the test has seen enough."""


class RunForeverRebuildsClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_dir = Path(self.tmp.name) / "config"
        self.state_dir = Path(self.tmp.name) / "state"
        self.config_dir.mkdir()
        self.state_dir.mkdir()
        (self.config_dir / "config.toml").write_text(CONFIG, encoding="utf-8")

    def test_an_edited_server_url_is_used_on_the_very_next_tick(self):
        seen_servers = []

        def factory(config):
            seen_servers.append(config.settings.server)
            return mock.Mock()

        calls = {"n": 0}

        def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                (self.config_dir / "config.toml").write_text(
                    CONFIG_NEW_SERVER, encoding="utf-8")
                return
            raise _StopLoop

        with mock.patch("immich_album_butler.runtime.time.sleep", fake_sleep), \
             mock.patch("immich_album_butler.runtime.Butler") as butler_cls:
            butler_cls.return_value.due_albums.return_value = []
            with self.assertRaises(_StopLoop):
                run_forever(factory, self.config_dir, self.state_dir, tick=1)

        self.assertEqual(seen_servers, [
            "http://immich.example.lan:2283",
            "http://immich2.example.lan:2283",
        ])


class BindWatcherTests(unittest.TestCase):
    """The debounce/manual-trigger logic behind design mode's port restart."""

    def test_an_unchanged_value_never_asks_to_restart(self):
        watcher = BindWatcher(8081, debounce_seconds=120)
        self.assertFalse(watcher.due(8081, now=0))
        self.assertFalse(watcher.due(8081, now=10_000))

    def test_a_change_waits_out_the_debounce(self):
        watcher = BindWatcher(8081, debounce_seconds=120)
        self.assertFalse(watcher.due(9090, now=0))       # just changed
        self.assertFalse(watcher.due(9090, now=119))      # still settling
        self.assertTrue(watcher.due(9090, now=120))        # quiet long enough

    def test_a_second_edit_resets_the_debounce(self):
        watcher = BindWatcher(8081, debounce_seconds=120)
        self.assertFalse(watcher.due(9090, now=0))
        self.assertFalse(watcher.due(9091, now=100))      # edited again
        self.assertFalse(watcher.due(9091, now=150))      # only 50s since the 2nd edit
        self.assertTrue(watcher.due(9091, now=220))

    def test_a_manual_trigger_applies_immediately(self):
        watcher = BindWatcher(8081, debounce_seconds=120)
        self.assertFalse(watcher.due(9090, now=0))
        watcher.force()
        self.assertTrue(watcher.due(9090, now=1))

    def test_a_manual_trigger_with_nothing_pending_does_nothing(self):
        watcher = BindWatcher(8081, debounce_seconds=120)
        watcher.force()
        self.assertFalse(watcher.due(8081, now=0))


class DesignApiClientRebuildTests(unittest.TestCase):
    """DesignApi's `.client` property, not its HTTP layer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_dir = Path(self.tmp.name) / "config"
        self.config_dir.mkdir()
        (self.config_dir / "config.toml").write_text(CONFIG, encoding="utf-8")

    def test_the_client_is_rebuilt_when_the_server_url_changes(self):
        built = []

        def factory(config):
            client = ImmichClient(config.settings.server, "key")
            built.append(client)
            return client

        api = DesignApi(factory, self.config_dir, self.tmp.name)
        first = api.client
        self.assertIs(api.client, first)          # same server: cached, not rebuilt
        self.assertEqual(len(built), 1)

        (self.config_dir / "config.toml").write_text(CONFIG_NEW_SERVER, encoding="utf-8")
        second = api.client
        self.assertIsNot(second, first)
        self.assertEqual(len(built), 2)

    def test_request_reload_invokes_the_bind_watchers_force(self):
        api = DesignApi(mock.Mock(), self.config_dir, self.tmp.name)
        api.reload_trigger = mock.Mock()
        result = api.request_reload()
        api.reload_trigger.assert_called_once()
        self.assertEqual(result, {"status": "requested"})

    def test_request_reload_without_a_trigger_is_a_harmless_no_op(self):
        api = DesignApi(mock.Mock(), self.config_dir, self.tmp.name)
        self.assertEqual(api.request_reload(), {"status": "requested"})


if __name__ == "__main__":
    unittest.main()
