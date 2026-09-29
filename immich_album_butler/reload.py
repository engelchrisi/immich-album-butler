"""Restarting design mode for a setting baked into the bound socket.

Design mode already re-reads `config.toml` on every request, so almost
everything in it takes effect immediately with no restart at all. The one
exception is `design_port`: once `ThreadingHTTPServer` has bound to it,
changing it in the config has no effect on the running process. `BindWatcher`
notices that, waits for the edit to settle (so a burst of saves does not
restart the socket mid-edit), and then re-execs the process in place.

A manual "Reload config" click in the UI (`DesignApi.request_reload`) skips
the wait and restarts as soon as the next background poll runs.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

POLL_SECONDS = 5
DEBOUNCE_SECONDS = 120

_NOTHING = object()


class BindWatcher:
    """Tracks one value (e.g. the configured port) and asks for a restart
    once it has changed and settled -- or been told to apply right away."""

    def __init__(self, current, debounce_seconds: float = DEBOUNCE_SECONDS) -> None:
        self.bound = current
        self.debounce_seconds = debounce_seconds
        self._pending = _NOTHING
        self._changed_at: float | None = None
        self._forced = threading.Event()

    def force(self) -> None:
        """The UI's "Reload config" button: apply a pending change now."""
        self._forced.set()

    def due(self, current, now: float | None = None) -> bool:
        """True once `current` differs from what is bound and should be applied."""
        now = now if now is not None else time.monotonic()
        if current == self.bound:
            self._pending = _NOTHING
            self._changed_at = None
            self._forced.clear()
            return False
        if current != self._pending:
            self._pending = current
            self._changed_at = now
            log.info("design mode: bind setting changed to %r; restarting "
                     "after %ds quiet, or on a manual reload", current,
                     self.debounce_seconds)
        return self._forced.is_set() or now - self._changed_at >= self.debounce_seconds

    def watch(self, get_current: Callable[[], object], stop: threading.Event) -> None:
        """Poll `get_current` until it settles on a new value, then restart."""
        while not stop.wait(POLL_SECONDS):
            try:
                current = get_current()
            except Exception:                      # noqa: BLE001
                log.exception("design mode: could not check for a bind change")
                continue
            if self.due(current):
                log.info("design mode restarting to apply %r", current)
                restart()


def restart() -> None:              # pragma: no cover - replaces the process
    os.execv(sys.executable, [sys.executable] + sys.argv)
