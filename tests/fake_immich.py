"""A fake Immich server, so the tests need no real one.

Copied from PyImmichFrame's tests/fake_immich.py and extended with what the
Duplicates tab needs: duplicate groups (`GET duplicates`), the key's own
account (`GET users/me`), removing assets from an album (`DELETE
albums/{id}/assets`) and refusing a route for a missing permission.

It speaks enough of the API for the client to be exercised end to end over a
real socket -- not a mocked client object.
That matters here more than usual: most of what can go wrong between this
project and Immich is in the wire details (paging, Range, enum spellings), and
a mock would agree with whatever the code believes.

The library it serves is made up from a small description:

    server = FakeImmich(albums={"Holiday": 3, "Pets": 60})

and every asset it invents is a plausible `AssetResponseDto` -- the same shape
the real server returns, because the whole design rests on passing those
dictionaries through untouched.

Use it as a context manager; `url` is what the client should be pointed at.

    with FakeImmich(albums={"Pets": 5}) as server:
        client = ImmichClient(server.url, server.API_KEY)
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

API_KEY = "test-key"

# Deterministic ids: a test that fails should fail the same way twice.
def _fake_id(kind: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyimmichframe/{kind}/{index}"))


class FakeAsset:
    """One asset, as close to Immich's AssetResponseDto as the tests need."""

    def __init__(self, index: int, *, kind: str = "IMAGE",
                 taken: date | None = None, favorite: bool = False,
                 visibility: str = "timeline", rating: int | None = None) -> None:
        self.id = _fake_id("asset", index)
        self.index = index
        self.kind = kind
        self.taken = taken or (date(2020, 1, 1) + timedelta(days=index))
        self.favorite = favorite
        self.visibility = visibility
        self.rating = rating
        self.albums: list[str] = []          # album ids, filled by FakeImmich
        self.people: list[str] = []
        self.tags: list[str] = []
        self.duplicate_id: str | None = None

    def to_api(self, *, with_exif: bool = True, with_people: bool = True) -> dict:
        # Every field the contract marks required is sent, because Immich
        # sends them. A fake that omits them lets the server omit them too,
        # and the tests would still be green -- see tests/test_contract.py.
        stamp = f"{self.taken.isoformat()}T12:00:00.000Z"
        data: dict = {
            "id": self.id,
            "type": self.kind,
            "checksum": f"sha1-{self.index:08d}",
            "ownerId": _fake_id("owner", 0),
            "createdAt": stamp,
            "updatedAt": stamp,
            "fileCreatedAt": stamp,
            "fileModifiedAt": stamp,
            "localDateTime": stamp,
            "originalFileName": f"IMG_{self.index:04d}.jpg",
            "originalPath": f"/photos/IMG_{self.index:04d}.jpg",
            "thumbhash": "1QcSHQRnh493V4dIh4eXh1h4kJUI",
            "isFavorite": self.favorite,
            "isArchived": self.visibility == "archive",
            "visibility": self.visibility,
            # Integer milliseconds for a video (the client divides by 1000),
            # null for an image -- both as the live Immich 3.2 sends them. It
            # was once a string here, which would have made every video fall
            # back to the configured interval instead of its own length.
            "duration": 5000 if self.kind == "VIDEO" else None,
            # Null for every photo that is not stacked -- which the live
            # server sends and upstream's generated spec forgot can happen.
            "stack": None,
            "duplicateId": self.duplicate_id,
        }
        if with_exif:
            data["exifInfo"] = {
                "description": "",
                "dateTimeOriginal": f"{self.taken.isoformat()}T12:00:00.000Z",
                "city": "Vienna", "state": "Wien", "country": "Austria",
                "make": "Canon", "model": "EOS R", "rating": self.rating,
            }
        if with_people:
            data["people"] = [{"id": p, "name": f"Person {p[:4]}"}
                              for p in self.people]
        return data


class FakeImmich:
    """A thread-backed HTTP server pretending to be Immich.

    `albums` maps an album name to how many assets it holds; those assets are
    created for it. `extra` adds assets that belong to no album -- the rest of
    the library, which whole-library mode draws on.
    """

    API_KEY = API_KEY

    def __init__(self, *, albums: dict[str, int] | None = None, extra: int = 0,
                 favorites: int = 0, videos: int = 0,
                 tags: dict[str, int] | None = None,
                 people: dict[str, int] | None = None,
                 version: tuple[int, int, int] = (3, 1, 0)) -> None:
        self.version = version
        self.assets: list[FakeAsset] = []
        self.albums: dict[str, dict] = {}     # id -> {"name", "assets": [ids]}
        self.tags: dict[str, dict] = {}
        self.people: dict[str, dict] = {}
        self.memories: list[dict] = []
        # Every request that arrived, as (method, path, body). Tests assert on
        # *how many* requests happened as well as on the answers: a cache that
        # does not cache is otherwise invisible.
        self.requests: list[tuple[str, str, dict | None]] = []
        self.fail_next: int = 0               # make the next N requests 500
        # Paths answered 403, as Immich does for a key lacking a permission.
        self.denied: set[str] = set()
        self.owner = {"id": _fake_id("owner", 0), "name": "Me",
                      "email": "me@example.com"}

        counter = 0
        for name, count in (albums or {}).items():
            album_id = _fake_id("album", len(self.albums))
            members = []
            for _ in range(count):
                asset = FakeAsset(counter)
                asset.albums.append(album_id)
                self.assets.append(asset)
                members.append(asset.id)
                counter += 1
            self.albums[album_id] = {"id": album_id, "albumName": name,
                                     "assets": members}
        for name, count in (people or {}).items():
            person_id = _fake_id("person", len(self.people))
            self.people[person_id] = {"id": person_id, "name": name}
            for _ in range(count):
                asset = FakeAsset(counter)
                asset.people.append(person_id)
                self.assets.append(asset)
                counter += 1
        for value, count in (tags or {}).items():
            tag_id = _fake_id("tag", len(self.tags))
            self.tags[tag_id] = {"id": tag_id, "value": value}
            for _ in range(count):
                asset = FakeAsset(counter)
                asset.tags.append(tag_id)
                self.assets.append(asset)
                counter += 1
        for _ in range(favorites):
            self.assets.append(FakeAsset(counter, favorite=True))
            counter += 1
        for _ in range(videos):
            self.assets.append(FakeAsset(counter, kind="VIDEO"))
            counter += 1
        for _ in range(extra):
            self.assets.append(FakeAsset(counter))
            counter += 1

        self._by_id = {a.id: a for a in self.assets}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "FakeImmich":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def start(self) -> "FakeImmich":
        fake = self

        class Handler(_Handler):
            server_fake = fake

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A short poll interval because shutdown() waits for one: at the
        # default half second the suite spends most of its time stopping
        # servers rather than testing anything.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02},
            daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        assert self._server is not None, "start() the fake before using its url"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- library ----------------------------------------------------------

    def album_id(self, name: str) -> str:
        for album in self.albums.values():
            if album["albumName"] == name:
                return album["id"]
        raise KeyError(name)

    def add_to_album(self, name: str, asset_index: int) -> None:
        """Put an existing asset into another album as well."""
        album_id = self.album_id(name)
        asset = self.assets[asset_index]
        asset.albums.append(album_id)
        self.albums[album_id]["assets"].append(asset.id)

    def mark_duplicates(self, asset_indexes: list[int]) -> str:
        """Make assets one duplicate group, as Immich's detection would."""
        group = _fake_id("duplicate", sum(1 for a in self.assets if a.duplicate_id))
        for index in asset_indexes:
            self.assets[index].duplicate_id = group
        return group

    def album_members(self, name: str) -> list[str]:
        return list(self.albums[self.album_id(name)]["assets"])

    def add_memory(self, years_ago: int, asset_indexes: list[int]) -> None:
        """A memory as /api/memories returns it, for the memories pool."""
        year = date.today().year - years_ago
        self.memories.append({
            "id": _fake_id("memory", len(self.memories)),
            "type": "on_this_day",
            "data": {"year": year},
            "memoryAt": f"{year}-{date.today():%m-%d}T12:00:00.000Z",
            "assets": [self.assets[i].to_api() for i in asset_indexes],
        })

    def count_requests(self, path_fragment: str) -> int:
        return sum(1 for _, path, _ in self.requests if path_fragment in path)

    # -- answering --------------------------------------------------------

    def answer(self, method: str, path: str, query: dict,
               body: dict | None) -> tuple[int, object]:
        self.requests.append((method, path, body))
        if self.fail_next > 0:
            self.fail_next -= 1
            return 500, {"message": "fake failure"}
        if path in self.denied:
            return 403, {"message": "Missing required permission"}

        if path == "/api/duplicates":
            groups: dict[str, list[dict]] = {}
            for asset in self.assets:
                if asset.duplicate_id:
                    groups.setdefault(asset.duplicate_id, []).append(asset.to_api())
            return 200, [{"duplicateId": gid, "assets": members}
                         for gid, members in groups.items()]

        if path == "/api/users/me":
            return 200, self.owner

        if (method == "DELETE" and path.startswith("/api/albums/")
                and path.endswith("/assets")):
            album = self.albums.get(path.split("/")[3])
            if album is None:
                return 404, {"message": "not found"}
            results = []
            for asset_id in (body or {}).get("ids", []):
                present = asset_id in album["assets"]
                if present:
                    album["assets"].remove(asset_id)
                    self._by_id[asset_id].albums.remove(album["id"])
                results.append({"id": asset_id, "success": present,
                                **({} if present else {"error": "not_found"})})
            return 200, results

        if path == "/api/server/version":
            major, minor, patch = self.version
            return 200, {"major": major, "minor": minor, "patch": patch}

        if path == "/api/assets/statistics":
            images = sum(1 for a in self.assets if a.kind == "IMAGE")
            videos = sum(1 for a in self.assets if a.kind == "VIDEO")
            return 200, {"images": images, "videos": videos,
                         "total": images + videos}

        if path == "/api/albums":
            asset_id = (query.get("assetId") or [None])[0]
            albums = list(self.albums.values())
            if asset_id:
                albums = [a for a in albums if asset_id in a["assets"]]
            stamp = "2020-01-01T00:00:00.000Z"
            return 200, [{"id": a["id"], "albumName": a["albumName"],
                          "assetCount": len(a["assets"]),
                          "description": "", "owner": self.owner,
                          "albumUsers": [{"user": self.owner, "role": "owner"}],
                          "createdAt": stamp, "updatedAt": stamp}
                         for a in albums]

        if path == "/api/tags":
            return 200, list(self.tags.values())

        if path == "/api/search/person":
            wanted = (query.get("name") or [""])[0].casefold()
            return 200, [p for p in self.people.values()
                         if wanted in (p["name"] or "").casefold()]

        if path == "/api/faces":
            # "person": None is a face nobody has named -- the common case,
            # and one more field upstream's spec wrongly made non-nullable.
            return 200, [{"id": _fake_id("face", 0), "person": None,
                          "boundingBoxX1": 10, "boundingBoxY1": 10,
                          "boundingBoxX2": 90, "boundingBoxY2": 90,
                          "imageWidth": 100, "imageHeight": 100}]

        if path == "/api/memories":
            # As strict as Immich 3.2: `for` must be a plain YYYY-MM-DD date.
            # A full timestamp is a 400 there, which the client once sent.
            wanted = (query.get("for") or [None])[0]
            if wanted is not None:
                try:
                    date.fromisoformat(wanted)
                    if len(wanted) != 10:
                        raise ValueError
                except ValueError:
                    return 400, {"message": "Validation failed",
                                 "errors": [{"code": "invalid_format",
                                             "format": "date"}]}
            return 200, self.memories

        if path.startswith("/api/assets/") and path.count("/") == 3:
            asset = self._by_id.get(path.rsplit("/", 1)[-1])
            return (200, asset.to_api()) if asset else (404, {"message": "not found"})

        if path == "/api/search/metadata":
            return 200, self._metadata(body or {})

        if path == "/api/search/random":
            return 200, self._random(body or {})

        return 404, {"message": f"fake Immich has no {path}"}

    # -- media ------------------------------------------------------------

    def media(self, path: str, range_header: str | None
              ) -> tuple[int, bytes, str, dict[str, str]] | None:
        """Answer the two byte-serving routes, or None if this is not one.

        Real bytes over a real socket, because what usually goes wrong between
        a frame and its server is Range handling and content types -- neither
        of which a JSON mock would ever exercise.
        """
        parts = path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "assets":
            return None
        asset = self._by_id.get(parts[2])
        if asset is None:
            return None

        if parts[3] == "thumbnail":
            # A real PNG, not a placeholder string: a browser has to be able
            # to decode it for the slideshow to be worth looking at, and
            # "the bytes arrived" is a much weaker claim than "it rendered".
            return 200, _png(asset.index), "image/png", {}
        if parts[3:5] == ["video", "playback"]:
            body = f"MP4-{asset.index}".encode() * 256
            if not range_header:
                return 200, body, "video/mp4", {}
            start, end = _parse_range(range_header, len(body))
            if start is None:
                return (416, b"", "video/mp4",
                        {"Content-Range": f"bytes */{len(body)}"})
            chunk = body[start:end + 1]
            return (206, chunk, "video/mp4", {
                "Content-Range": f"bytes {start}-{end}/{len(body)}"})
        return None

    def _select(self, body: dict) -> list[FakeAsset]:
        found = list(self.assets)
        if album_ids := body.get("albumIds"):
            found = [a for a in found
                     if any(i in a.albums for i in album_ids)]
        if person_ids := body.get("personIds"):
            found = [a for a in found
                     if any(i in a.people for i in person_ids)]
        if tag_ids := body.get("tagIds"):
            found = [a for a in found if any(i in a.tags for i in tag_ids)]
        if body.get("isFavorite") is not None:
            found = [a for a in found if a.favorite == body["isFavorite"]]
        if kind := body.get("type"):
            found = [a for a in found if a.kind == kind]
        # Immich's visibility is a switch, not an addition: asking for the
        # archive excludes the timeline. The fake has to behave the same way or
        # the filter tests would pass against a server that does not exist.
        visibility = body.get("visibility")
        if visibility:
            found = [a for a in found if a.visibility == visibility]
        if rating := body.get("rating"):
            found = [a for a in found if a.rating == rating]
        if after := body.get("takenAfter"):
            cutoff = datetime.fromisoformat(after.replace("Z", "+00:00")).date()
            found = [a for a in found if a.taken >= cutoff]
        if before := body.get("takenBefore"):
            cutoff = datetime.fromisoformat(before.replace("Z", "+00:00")).date()
            found = [a for a in found if a.taken <= cutoff]
        return found

    def _metadata(self, body: dict) -> dict:
        found = self._select(body)
        size = int(body.get("size") or 250)
        page = int(body.get("page") or 1)
        start = (page - 1) * size
        items = found[start:start + size]
        has_more = start + size < len(found)
        return {"assets": {
            "total": len(found),
            "count": len(items),
            "items": [a.to_api(with_exif=bool(body.get("withExif")),
                               with_people=bool(body.get("withPeople")))
                      for a in items],
            "nextPage": str(page + 1) if has_more else None,
            "facets": [],
        }}

    def _random(self, body: dict) -> list[dict]:
        """Deliberately *not* random: a test that needs randomness makes its own.

        Returning the first N keeps failures reproducible; what the tests check
        about /search/random is that the right filters were sent, which the
        recorded request body shows.
        """
        found = self._select(body)
        size = int(body.get("size") or 25)
        return [a.to_api(with_exif=bool(body.get("withExif")),
                         with_people=bool(body.get("withPeople")))
                for a in found[:size]]


def _png(index: int, width: int = 480, height: int = 320) -> bytes:
    """A real, decodable PNG in a colour derived from the index.

    Written by hand with zlib and struct rather than a library, because the
    fake has to stay as stdlib-only as the thing it stands in for. Distinct
    colours per asset so that a slideshow is visibly a slideshow.
    """
    import binascii
    import struct
    import zlib

    hue = (index * 47) % 360
    red, green, blue = _hue_to_rgb(hue)

    raw = b"".join(b"\x00" + bytes([red, green, blue]) * width
                   for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _hue_to_rgb(hue: int) -> tuple[int, int, int]:
    """Full-saturation colour wheel, so consecutive photos look different."""
    sector, offset = divmod(hue, 60)
    rising, falling = int(offset * 255 / 60), 255 - int(offset * 255 / 60)
    return [(255, rising, 0), (falling, 255, 0), (0, 255, rising),
            (0, falling, 255), (rising, 0, 255), (255, 0, falling)][sector % 6]


def _parse_range(header: str, size: int) -> tuple[int | None, int]:
    """"bytes=0-99" -> (0, 99). (None, 0) when it cannot be satisfied."""
    if not header.startswith("bytes="):
        return None, 0
    first, _, last = header[len("bytes="):].partition("-")
    try:
        start = int(first) if first else 0
        end = int(last) if last else size - 1
    except ValueError:
        return None, 0
    if start >= size or start > end:
        return None, 0
    return start, min(end, size - 1)


class _Handler(BaseHTTPRequestHandler):
    server_fake: FakeImmich

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:                        # noqa: N802 - http.server API
        self._handle("GET", None)

    def do_POST(self) -> None:                       # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"message": "not JSON"})
            return
        self._handle("POST", body)

    def do_DELETE(self) -> None:                     # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"message": "not JSON"})
            return
        self._handle("DELETE", body)

    def _handle(self, method: str, body: dict | None) -> None:
        if self.headers.get("x-api-key") != API_KEY:
            self._send(401, {"message": "Invalid API key"})
            return
        parsed = urlparse(self.path)
        media = self.server_fake.media(parsed.path, self.headers.get("Range"))
        if media is not None:
            self.server_fake.requests.append((method, parsed.path, None))
            status, data, kind, extra = media
            self._send_bytes(status, data, kind, extra)
            return
        status, payload = self.server_fake.answer(
            method, parsed.path, parse_qs(parsed.query), body)
        self._send(status, payload)

    def _send(self, status: int, payload: object) -> None:
        self._send_bytes(status, json.dumps(payload).encode("utf-8"),
                         "application/json", {})

    def _send_bytes(self, status: int, data: bytes, kind: str,
                    extra: dict[str, str]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args: object) -> None:
        """Quiet: the tests print their own failures."""
