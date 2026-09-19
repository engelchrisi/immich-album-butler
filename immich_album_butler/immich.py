"""A small Immich API client, standard library only.

Only the endpoints the butler needs, and only the fields it uses. Deliberately
narrow: the tool reads assets, people and albums, creates albums and adds
assets to them. Everything else Immich can do is out of scope.

The API key is passed in the `x-api-key` header and is never logged, never put
on a command line and never handed to a browser.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

log = logging.getLogger(__name__)

# Immich rejects very large bodies; adding assets in chunks also means a failure
# costs one chunk rather than the whole album.
ADD_CHUNK = 500
PAGE_SIZE = 1000

# The roles Immich gives a user on an album. "owner" is the album's own owner
# and is never something the butler grants.
VIEWER = "viewer"
EDITOR = "editor"
OWNER = "owner"
SHARE_ROLES = (VIEWER, EDITOR)


class ImmichError(RuntimeError):
    """A request to Immich failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Asset:
    """The handful of asset fields album rules are built from."""

    id: str
    taken_at: dt.datetime | None
    kind: str = "IMAGE"                 # IMAGE or VIDEO
    file_name: str = ""                 # the original file name, for cover = "<name>"
    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None

    @property
    def located(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def is_video(self) -> bool:
        return self.kind.upper() == "VIDEO"

    @classmethod
    def from_api(cls, data: dict) -> "Asset":
        exif = data.get("exifInfo") or {}
        # localDateTime is the wall-clock time where the photo was taken, which
        # is what a person means by "our trip was in August".
        taken = _parse_time(data.get("localDateTime")
                            or exif.get("dateTimeOriginal")
                            or data.get("fileCreatedAt"))
        return cls(
            id=data["id"],
            taken_at=taken,
            kind=str(data.get("type", "IMAGE")),
            file_name=str(data.get("originalFileName") or ""),
            latitude=_as_float(exif.get("latitude")),
            longitude=_as_float(exif.get("longitude")),
            city=exif.get("city") or None,
            state=exif.get("state") or None,
            country=exif.get("country") or None,
        )


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    hidden: bool = False

    @classmethod
    def from_api(cls, data: dict) -> "Person":
        return cls(id=data["id"], name=data.get("name") or "",
                   hidden=bool(data.get("isHidden")))


@dataclass(frozen=True)
class User:
    """Another account on the same Immich server.

    Only what is needed to share an album with somebody and to name them in a
    message: the config refers to people by name or address, never by id.
    """

    id: str
    name: str
    email: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "User":
        return cls(id=data["id"], name=data.get("name") or "",
                   email=data.get("email") or "")

    def answers_to(self, wanted: str) -> bool:
        folded = wanted.strip().casefold()
        return folded in (self.name.casefold(), self.email.casefold())

    def label(self) -> str:
        return self.name or self.email or self.id


@dataclass(frozen=True)
class AlbumInfo:
    id: str
    name: str
    asset_count: int = 0
    cover_asset_id: str | None = None
    # Who else can see this album: user id -> role ("viewer" or "editor").
    # The owner is left out; an album is not shared with the person who owns it.
    shared_with: dict[str, str] = field(default_factory=dict)
    # Whose album this is. The listing returns albums shared *with* this
    # account as well as its own, and only an owner may share one on.
    owner_id: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> "AlbumInfo":
        shared = {}
        owner = (data.get("owner") or {}).get("id")
        for entry in data.get("albumUsers") or []:
            role = entry.get("role") or ""
            user = (entry.get("user") or {}).get("id")
            if not user:
                continue
            if role == OWNER:
                owner = owner or user
            else:
                shared[user] = role
        return cls(id=data["id"], name=data.get("albumName") or "",
                   asset_count=int(data.get("assetCount") or 0),
                   cover_asset_id=data.get("albumThumbnailAssetId") or None,
                   shared_with=shared, owner_id=owner)


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        if not api_key:
            raise ImmichError("no API key: set IMMICH_KEY in the environment")
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def request(self, method: str, path: str, body: Any = None,
                params: dict | None = None) -> Any:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        data = None
        headers = {"x-api-key": self._key, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise ImmichError(_describe(exc, method, path), status=exc.code) from None
        except urllib.error.URLError as exc:
            raise ImmichError(f"cannot reach Immich at {self.base_url}: "
                              f"{exc.reason}") from None
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            raise ImmichError(f"{method} {path}: Immich did not return JSON") from None

    # -- reads ------------------------------------------------------------

    def server_version(self) -> str:
        data = self.request("GET", "server/version") or {}
        return ".".join(str(data.get(k, 0)) for k in ("major", "minor", "patch"))

    def people(self, include_hidden: bool = False,
               max_pages: int = 50) -> list[Person]:
        """Every *named* person, most-photographed first.

        Two things to know about this endpoint. It reports more pages with
        `hasNextPage`, not the `nextPage` that search/metadata uses. And a
        library can hold tens of thousands of unnamed people (one per face
        cluster) against a handful of named ones -- Immich returns the named
        ones first, so we stop at the first page that has none rather than
        paging through all of them for nothing.
        """
        found: list[Person] = []
        for page in range(1, max_pages + 1):
            data = self.request("GET", "people", params={
                "page": page, "size": 500,
                "withHidden": str(include_hidden).lower()}) or {}
            items = data.get("people", [])
            named = [Person.from_api(p) for p in items if p.get("name")]
            found.extend(named)
            if not named or not data.get("hasNextPage"):
                break
        return found

    def find_people(self, name: str) -> list[Person]:
        """People whose name matches exactly, ignoring case.

        Used instead of paging the whole list: resolving three names should be
        three small requests, not a walk through every face cluster.
        """
        data = self.request("GET", "search/person",
                            params={"name": name, "withHidden": "false"}) or []
        folded = name.casefold()
        return [Person.from_api(p) for p in data
                if (p.get("name") or "").casefold() == folded]

    def albums(self) -> list[AlbumInfo]:
        data = self.request("GET", "albums") or []
        return [AlbumInfo.from_api(a) for a in data]

    def users(self) -> list[User]:
        """The other accounts on this server, for `share_with`.

        Needs `user.read` on the key. Listing them is what lets the config name
        an account the way a human does -- "viewer", or an address -- instead
        of carrying a UUID, which is the same reason people are named by name.
        """
        data = self.request("GET", "users") or []
        return [User.from_api(u) for u in data]

    def me(self) -> User:
        """The account this key belongs to. Needs `user.read`, like users()."""
        return User.from_api(self.request("GET", "users/me") or {})

    def album_asset_ids(self, album_id: str) -> set[str]:
        """Which assets are in an album.

        Not from `GET /api/albums/{id}`: that returns the album's metadata with
        no asset list at all, so reading it would make every run think the
        album was empty and re-send everything. Searching by album id is the
        way that actually enumerates them.

        Note the album's own `assetCount` is a *display* count and can be lower
        than this -- it collapses stacked assets and live-photo pairs -- so it
        must not be used to decide whether anything is missing.
        """
        return {asset.id for asset in self.search_metadata(album_ids=[album_id],
                                                           with_exif=False)}

    def search_metadata(self, *, taken_after: dt.date | None = None,
                        taken_before: dt.date | None = None,
                        person_ids: list[str] | None = None,
                        album_ids: list[str] | None = None,
                        country: str | None = None,
                        state: str | None = None,
                        city: str | None = None,
                        with_exif: bool = True,
                        page_size: int = PAGE_SIZE) -> Iterator[Asset]:
        """Page through POST /api/search/metadata, yielding assets.

        Dates are inclusive: `taken_before` is sent as the end of that day.
        """
        body: dict[str, Any] = {"size": page_size, "withExif": with_exif}
        if taken_after:
            body["takenAfter"] = _start_of_day(taken_after)
        if taken_before:
            body["takenBefore"] = _end_of_day(taken_before)
        if person_ids:
            body["personIds"] = person_ids
        if album_ids:
            body["albumIds"] = album_ids
        if country:
            body["country"] = country
        if state:
            body["state"] = state
        if city:
            body["city"] = city

        page = 1
        seen = 0
        while True:
            data = self.request("POST", "search/metadata", {**body, "page": page})
            bucket = (data or {}).get("assets") or {}
            items = bucket.get("items") or []
            for item in items:
                yield Asset.from_api(item)
            seen += len(items)
            next_page = bucket.get("nextPage")
            if not next_page or not items:
                break
            page = int(next_page)
        log.debug("search/metadata returned %d assets over %d page(s)", seen, page)

    def thumbnail(self, asset_id: str, size: str = "thumbnail") -> tuple[bytes, str]:
        """Fetch a thumbnail server-side, so the browser never sees the key."""
        url = f"{self.base_url}/api/assets/{asset_id}/thumbnail?size={size}"
        request = urllib.request.Request(url, headers={"x-api-key": self._key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read(), response.headers.get("Content-Type", "image/jpeg")
        except urllib.error.HTTPError as exc:
            raise ImmichError(f"thumbnail for {asset_id}: HTTP {exc.code}",
                              status=exc.code) from None
        except urllib.error.URLError as exc:
            raise ImmichError(f"cannot reach Immich: {exc.reason}") from None

    # -- writes (albums only) ---------------------------------------------

    def create_album(self, name: str, description: str = "",
                     asset_ids: list[str] | None = None) -> AlbumInfo:
        body = {"albumName": name, "description": description,
                "assetIds": (asset_ids or [])[:ADD_CHUNK]}
        data = self.request("POST", "albums", body) or {}
        album = AlbumInfo.from_api(data)
        rest = (asset_ids or [])[ADD_CHUNK:]
        if rest:
            self.add_assets(album.id, rest)
        return album

    def add_assets(self, album_id: str, asset_ids: list[str]) -> int:
        """Add assets in chunks. Returns how many Immich accepted as new."""
        added = 0
        for chunk in _chunks(asset_ids, ADD_CHUNK):
            results = self.request("PUT", f"albums/{album_id}/assets",
                                   {"ids": chunk}) or []
            added += sum(1 for r in results if r.get("success"))
        return added

    def rename_album(self, album_id: str, name: str) -> None:
        """Rename an album, e.g. to add the marker suffix to an adopted one.

        Like set_album_cover, this needs `album.update` on the API key.
        """
        self.request("PATCH", f"albums/{album_id}", {"albumName": name})

    def set_album_cover(self, album_id: str, asset_id: str) -> None:
        """Point the album's cover at one of its assets.

        This is the one write that needs `album.update` on the API key, which
        no other feature uses. A key without it fails here and nowhere else,
        so the failure is reported per album rather than stopping a run.
        """
        self.request("PATCH", f"albums/{album_id}",
                     {"albumThumbnailAssetId": asset_id})

    def share_album(self, album_id: str, grants: list[tuple[str, str]]) -> None:
        """Give other accounts access to an album. Needs `albumUser.create`.

        Only ever *adds* access. There is deliberately no unshare here: taking
        somebody's access away is not something an unattended daemon should do
        on the strength of an edited config file, so it stays a human act in
        the Immich UI -- the same reasoning that keeps `albumAsset.delete` off
        the key unless an album asks to mirror.
        """
        self.request("PUT", f"albums/{album_id}/users",
                     {"albumUsers": [{"userId": user_id, "role": role}
                                     for user_id, role in grants]})

    def set_album_user_role(self, album_id: str, user_id: str, role: str) -> None:
        """Change the role of somebody who already has access.

        A separate call from share_album, and a separate permission
        (`albumUser.update`), because Immich refuses to re-add a user who is
        already on the album.
        """
        self.request("PUT", f"albums/{album_id}/user/{user_id}", {"role": role})

    def remove_assets(self, album_id: str, asset_ids: list[str]) -> int:
        """Remove assets from an album. Never deletes them from the library."""
        removed = 0
        for chunk in _chunks(asset_ids, ADD_CHUNK):
            results = self.request("DELETE", f"albums/{album_id}/assets",
                                   {"ids": chunk}) or []
            removed += sum(1 for r in results if r.get("success"))
        return removed

    # -- suggestions (design mode's pickers) ------------------------------

    def suggestions(self, kind: str, *, country: str | None = None,
                    state: str | None = None) -> list[str]:
        params = {"type": kind}
        if country:
            params["country"] = country
        if state:
            params["state"] = state
        data = self.request("GET", "search/suggestions", params=params) or []
        return [item for item in data if isinstance(item, str) and item]


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _describe(exc: urllib.error.HTTPError, method: str, path: str) -> str:
    detail = ""
    try:
        body = json.loads(exc.read() or b"{}")
        detail = body.get("message") or ""
    except Exception:                       # noqa: BLE001 - diagnostics only
        pass
    if exc.code == 401:
        return f"{method} {path}: Immich rejected the API key (401)"
    if exc.code == 403:
        return (f"{method} {path}: the API key lacks a permission"
                + (f" -- {detail}" if detail else ""))
    return f"{method} {path}: HTTP {exc.code}" + (f" -- {detail}" if detail else "")


def _parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    # Compare wall-clock times: the trip was in August wherever the camera was.
    return parsed.replace(tzinfo=None)


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _start_of_day(day: dt.date) -> str:
    return dt.datetime(day.year, day.month, day.day).isoformat() + ".000Z"


def _end_of_day(day: dt.date) -> str:
    return dt.datetime(day.year, day.month, day.day, 23, 59, 59).isoformat() + ".999Z"
