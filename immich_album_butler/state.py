"""What the butler remembers between runs.

Only two things, and both are deliberately *not* in the config: the Immich
album id (an id means nothing to a person reading a config file) and the last
run per album (so a schedule survives a restart).

The file is rewritten atomically, because a half-written state file would make
the butler forget which album it owns and create a duplicate.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

STATE_FILE = "state.json"


@dataclass
class AlbumState:
    album_id: str | None = None
    last_run: str | None = None          # ISO 8601, timezone-aware
    last_result: str | None = None
    last_error: str | None = None
    assets_added: int = 0
    assets_removed: int = 0

    @property
    def last_run_at(self) -> dt.datetime | None:
        if not self.last_run:
            return None
        try:
            return dt.datetime.fromisoformat(self.last_run)
        except ValueError:
            return None


@dataclass
class State:
    path: Path
    albums: dict[str, AlbumState] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path) -> "State":
        path = Path(directory) / STATE_FILE
        state = cls(path=path)
        if not path.exists():
            return state
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt state file must not stop the daemon: worst case the
            # butler re-finds albums by name and re-runs them once.
            log.warning("ignoring unreadable state file %s: %s", path, exc)
            return state
        for slug, body in (data.get("albums") or {}).items():
            if isinstance(body, dict):
                known = {f: body.get(f) for f in AlbumState.__annotations__
                         if f in body}
                state.albums[slug] = AlbumState(**known)
        return state

    def for_album(self, slug: str) -> AlbumState:
        return self.albums.setdefault(slug, AlbumState())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1,
                   "albums": {slug: asdict(album)
                              for slug, album in sorted(self.albums.items())}}
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)
