"""Shared not-wanted state via Immich's public tag API.

Keep this protocol identical to PyImmichFrame's rejections.py. Tags belong to
an Immich account: both applications must use keys for the same account.
"""

from __future__ import annotations

import threading
import time

TAG = "photo-butler/rejected"
REFRESH_SECONDS = 5.0


class Rejections:
    def __init__(self, request, error_type):
        self.request = request
        self.error_type = error_type
        self._lock = threading.RLock()
        self._ids: frozenset[str] = frozenset()
        self._expires = 0.0

    def _tag(self, create: bool = False) -> str | None:
        tags = self.request("GET", "tags") or []
        found = next((tag["id"] for tag in tags if tag.get("value") == TAG), None)
        if found is None and create:
            tags = self.request("PUT", "tags", {"tags": [TAG]}) or []
            found = next((tag["id"] for tag in tags if tag.get("value") == TAG), None)
            if found is None:
                raise self.error_type("Immich did not create the not-wanted tag")
        return found

    def ids(self, *, force: bool = False) -> frozenset[str]:
        """One paged membership query, cached briefly for playback polls.

        Failures propagate instead of replacing known exclusions with an empty
        set. Publish a new snapshot only after every page has been read.
        """
        with self._lock:
            if not force and time.monotonic() < self._expires:
                return self._ids
            tag_id = self._tag()
            ids: set[str] = set()
            page = 1
            seen_pages = set()
            while tag_id is not None:
                if page in seen_pages:
                    raise self.error_type("Immich repeated a not-wanted search page")
                seen_pages.add(page)
                data = self.request("POST", "search/metadata", {
                    "tagIds": [tag_id], "withExif": False, "size": 1000, "page": page,
                })
                bucket = data["assets"]
                ids.update(item["id"] for item in bucket["items"])
                if not bucket.get("nextPage"):
                    break
                page = int(bucket["nextPage"])
            self._ids = frozenset(ids)
            self._expires = time.monotonic() + REFRESH_SECONDS
            return self._ids

    def set(self, asset_id: str, rejected: bool) -> None:
        """Add/remove only our tag. Never change stars or unrelated tags."""
        if not isinstance(rejected, bool):
            raise ValueError("rejected must be true or false")
        with self._lock:
            self._expires = 0.0
            tag_id = self._tag(create=rejected)
            if tag_id is not None:
                result = self.request("PUT" if rejected else "DELETE",
                                      f"tags/{tag_id}/assets", {"ids": [asset_id]})
                # Already-tagged responses can be reported as duplicate. The
                # authoritative check is the membership read below, not HTTP 200.
                if not isinstance(result, list):
                    raise self.error_type("Immich returned an invalid tag result")
            if (asset_id in self.ids(force=True)) != rejected:
                raise self.error_type("Immich did not save the not-wanted tag")
