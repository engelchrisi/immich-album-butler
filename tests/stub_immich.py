"""A fake Immich, just real enough to test the client and the runtime against.

It speaks the handful of endpoints the butler uses, over a real socket, so the
tests exercise the actual HTTP client rather than a mock of it -- including
paging, chunked adds and error codes.

All data here is fictional (see CLAUDE.md).
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

API_KEY = "test-key"

# A 1x1 transparent GIF, standing in for a thumbnail.
_PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!"
          b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
          b"\x00\x02\x02D\x01\x00;")

# Deliberately obvious placeholders, never anything from a real library.
ZERO = "00000000-0000-0000-0000-0000000000"


def fake_id(number: int) -> str:
    return f"{ZERO}{number:02d}"


def make_asset(number: int, *, when: str, lat: float | None = None,
               lon: float | None = None, city: str | None = None,
               state: str | None = None, country: str | None = None,
               kind: str = "IMAGE", people: tuple[str, ...] = (),
               file_name: str | None = None) -> dict:
    """One asset as Immich's search/metadata returns it."""
    return {
        "id": fake_id(number),
        "type": kind,
        "localDateTime": when,
        "originalFileName": file_name or f"IMG_{number:04d}.jpg",
        "_people": list(people),          # stub-only, used for personIds filtering
        "exifInfo": {"latitude": lat, "longitude": lon, "city": city,
                     "state": state, "country": country,
                     "dateTimeOriginal": when},
    }


class StubImmich:
    """Start with `with StubImmich(assets) as stub: ...`; `stub.url` is the base."""

    def __init__(self, assets: list[dict] | None = None,
                 people: list[dict] | None = None,
                 albums: list[dict] | None = None,
                 page_size: int = 2,
                 unnamed_people: int = 0,
                 stacked_pairs: int = 0,
                 missing_permissions: set[str] | None = None) -> None:
        self.stacked_pairs = stacked_pairs
        self.assets = assets or []
        self.people = people or []
        # Face clusters nobody has named. A real library has thousands.
        self.unnamed_people = [{"id": f"cluster-{n}", "name": ""}
                               for n in range(unnamed_people)]
        self.albums = {a["id"]: a for a in (albums or [])}
        self.page_size = page_size
        self.missing_permissions = missing_permissions or set()
        self.requests: list[tuple[str, str]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "StubImmich":
        stub = self
        handler = _make_handler(stub)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- behaviour --------------------------------------------------------

    def add_album(self, name: str, asset_ids: list[str] | None = None) -> dict:
        album = {"id": str(uuid.uuid4()), "albumName": name,
                 "assets": [{"id": i} for i in (asset_ids or [])]}
        album["assetCount"] = len(album["assets"])
        self.albums[album["id"]] = album
        return album

    def album_named(self, name: str) -> dict | None:
        for album in self.albums.values():
            if album["albumName"] == name:
                return album
        return None

    def display_count(self, album: dict) -> int:
        """Immich's own count, which under-reports: one stacked pair shows as one."""
        return max(0, len(album["assets"]) - self.stacked_pairs)

    def search(self, body: dict) -> dict:
        items = list(self.assets)

        if album_ids := body.get("albumIds"):
            in_albums = set()
            for album_id in album_ids:
                album = self.albums.get(album_id)
                in_albums |= {a["id"] for a in (album or {}).get("assets", [])}
            items = [a for a in items if a["id"] in in_albums]
        if after := body.get("takenAfter"):
            items = [a for a in items if a["localDateTime"] >= _norm(after)]
        if before := body.get("takenBefore"):
            items = [a for a in items if a["localDateTime"] <= _norm(before)]
        if person_ids := body.get("personIds"):
            items = [a for a in items
                     if any(p in a.get("_people", []) for p in person_ids)]
        for key in ("country", "state", "city"):
            if value := body.get(key):
                items = [a for a in items if (a["exifInfo"].get(key) or "") == value]

        size = int(body.get("size") or self.page_size)
        page = int(body.get("page") or 1)
        start = (page - 1) * size
        chunk = items[start:start + size]
        has_more = start + size < len(items)
        return {"assets": {"total": len(items), "count": len(chunk),
                           "items": [_public(a) for a in chunk],
                           "nextPage": str(page + 1) if has_more else None}}


def _public(asset: dict) -> dict:
    return {k: v for k, v in asset.items() if not k.startswith("_")}


def _norm(stamp: str) -> str:
    return stamp.replace("Z", "").replace("+00:00", "")


def _make_handler(stub: StubImmich):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:       # keep the test output clean
            pass

        # -- helpers ------------------------------------------------------

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        def _send(self, status: int, payload) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, permission: str | None = None) -> bool:
            if self.headers.get("x-api-key") != API_KEY:
                self._send(401, {"message": "Invalid API key"})
                return False
            if permission and permission in stub.missing_permissions:
                self._send(403, {"message": f"Missing required permission: {permission}"})
                return False
            return True

        # -- routes -------------------------------------------------------

        def do_GET(self) -> None:                    # noqa: N802
            path, _, query = self.path.partition("?")
            stub.requests.append(("GET", path))

            if path == "/api/server/version":
                return self._send(200, {"major": 3, "minor": 2, "patch": 0})

            if path == "/api/people":
                if not self._authorized("person.read"):
                    return
                # Mirrors the real endpoint: it reports more pages with
                # `hasNextPage` (search/metadata uses `nextPage` instead), and
                # a real library holds tens of thousands of unnamed people
                # against a handful of named ones, named ones first.
                params = parse_qs(query)
                page = int((params.get("page") or ["1"])[0])
                size = int((params.get("size") or ["500"])[0])
                everyone = stub.people + stub.unnamed_people
                start = (page - 1) * size
                chunk = everyone[start:start + size]
                return self._send(200, {
                    "people": chunk, "total": len(everyone),
                    "hidden": 0, "hasNextPage": start + size < len(everyone)})

            if path == "/api/search/person":
                if not self._authorized("person.read"):
                    return
                wanted = (parse_qs(query).get("name") or [""])[0].casefold()
                return self._send(200, [p for p in stub.people
                                        if wanted in (p.get("name") or "").casefold()])

            if path == "/api/albums":
                if not self._authorized("album.read"):
                    return
                return self._send(200, [
                    {"id": a["id"], "albumName": a["albumName"],
                     "assetCount": stub.display_count(a),
                     "albumThumbnailAssetId": a.get("albumThumbnailAssetId")}
                    for a in stub.albums.values()])

            if path.startswith("/api/albums/"):
                if not self._authorized("album.read"):
                    return
                album = stub.albums.get(path.rsplit("/", 1)[-1])
                if album is None:
                    return self._send(404, {"message": "Not found"})
                # Like the real endpoint: metadata only, no asset list, and an
                # assetCount that collapses stacked/live-photo pairs, so it is
                # lower than the number of assets actually in the album.
                return self._send(200, {
                    "id": album["id"], "albumName": album["albumName"],
                    "description": "", "assetCount": stub.display_count(album)})

            if path.startswith("/api/assets/") and path.endswith("/thumbnail"):
                if not self._authorized("asset.view"):
                    return
                asset_id = path.split("/")[3]
                if not any(a["id"] == asset_id for a in stub.assets):
                    return self._send(404, {"message": "Not found"})
                # A one-pixel GIF stands in for the real JPEG.
                self.send_response(200)
                self.send_header("Content-Type", "image/gif")
                self.send_header("Content-Length", str(len(_PIXEL)))
                self.end_headers()
                return self.wfile.write(_PIXEL)

            if path == "/api/search/suggestions":
                if not self._authorized():
                    return
                kind = (parse_qs(query).get("type") or ["country"])[0]
                field = {"country": "country", "state": "state", "city": "city"}
                if kind not in field:
                    return self._send(400, {"message": f"bad type {kind}"})
                values = sorted({a["exifInfo"].get(field[kind]) for a in stub.assets
                                 if a["exifInfo"].get(field[kind])})
                return self._send(200, values)

            self._send(404, {"message": f"no route {path}"})

        def do_POST(self) -> None:                   # noqa: N802
            path = self.path.partition("?")[0]
            stub.requests.append(("POST", path))
            body = self._body()

            if path == "/api/search/metadata":
                if not self._authorized("asset.read"):
                    return
                return self._send(200, stub.search(body))

            if path == "/api/albums":
                if not self._authorized("album.create"):
                    return
                album = stub.add_album(body.get("albumName", ""),
                                       body.get("assetIds") or [])
                return self._send(201, album)

            self._send(404, {"message": f"no route {path}"})

        def do_PUT(self) -> None:                    # noqa: N802
            path = self.path.partition("?")[0]
            stub.requests.append(("PUT", path))
            body = self._body()

            if path.startswith("/api/albums/") and path.endswith("/assets"):
                if not self._authorized("albumAsset.create"):
                    return
                album = stub.albums.get(path.split("/")[3])
                if album is None:
                    return self._send(404, {"message": "Not found"})
                present = {a["id"] for a in album["assets"]}
                results = []
                for asset_id in body.get("ids", []):
                    if asset_id in present:
                        results.append({"id": asset_id, "success": False,
                                        "error": "duplicate"})
                    else:
                        album["assets"].append({"id": asset_id})
                        present.add(asset_id)
                        results.append({"id": asset_id, "success": True})
                album["assetCount"] = len(album["assets"])
                return self._send(200, results)

            self._send(404, {"message": f"no route {path}"})

        def do_PATCH(self) -> None:                  # noqa: N802
            path = self.path.partition("?")[0]
            stub.requests.append(("PATCH", path))
            body = self._body()

            # Setting a cover is the one call that needs album.update, so the
            # stub guards it separately -- a key without that permission has to
            # fail here and nowhere else.
            if path.startswith("/api/albums/"):
                if not self._authorized("album.update"):
                    return
                album = stub.albums.get(path.rsplit("/", 1)[-1])
                if album is None:
                    return self._send(404, {"message": "Not found"})
                if "albumThumbnailAssetId" in body:
                    cover = body["albumThumbnailAssetId"]
                    if not any(a["id"] == cover for a in album["assets"]):
                        return self._send(
                            400, {"message": "cover asset is not in the album"})
                    album["albumThumbnailAssetId"] = cover
                return self._send(200, {
                    "id": album["id"], "albumName": album["albumName"],
                    "albumThumbnailAssetId": album.get("albumThumbnailAssetId")})

            self._send(404, {"message": f"no route {path}"})

        def do_DELETE(self) -> None:                 # noqa: N802
            path = self.path.partition("?")[0]
            stub.requests.append(("DELETE", path))
            body = self._body()

            if path.startswith("/api/albums/") and path.endswith("/assets"):
                if not self._authorized("albumAsset.delete"):
                    return
                album = stub.albums.get(path.split("/")[3])
                if album is None:
                    return self._send(404, {"message": "Not found"})
                wanted = set(body.get("ids", []))
                before = len(album["assets"])
                album["assets"] = [a for a in album["assets"] if a["id"] not in wanted]
                album["assetCount"] = len(album["assets"])
                removed = before - len(album["assets"])
                return self._send(200, [{"id": i, "success": True} for i in wanted][:removed])

            self._send(404, {"message": f"no route {path}"})

    return Handler


def day(year: int, month: int, day_of_month: int, hour: int = 12) -> str:
    return dt.datetime(year, month, day_of_month, hour).isoformat() + ".000Z"
