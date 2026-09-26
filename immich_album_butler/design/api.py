"""Design mode without the HTTP.

Every operation the UI can perform is a method here, taking and returning
plain dictionaries. That keeps the browser-facing layer thin and, more to the
point, makes the whole feature testable without a browser.

Two rules this layer keeps:

* **Preview never writes.** It builds the same `Plan` runtime mode does, so
  what the UI shows is exactly what a run would do -- not a second, parallel
  implementation that can drift.
* **Saving is the only write to disk.** The whole configuration is one file,
  so every save rewrites `config.toml` atomically from the config that was just
  read, carrying the login hashes and everything else through untouched. The
  daemon re-reads the file every tick, so a save takes effect without
  restarting anything.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
import re
import time
from pathlib import Path

from .. import analyze as analyze_module
from .. import config as config_module
from .. import cover as cover_module
from .. import immich as immich_module
from .. import trips as trips_module
from ..config import Album, MatchRule, slugify
from ..immich import ImmichClient, ImmichError
from ..matcher import MatchError
from ..runtime import Butler, timezone_of
from ..schedule import ScheduleError
from ..schedule import parse as parse_schedule
from ..state import State

log = logging.getLogger(__name__)

# The preview ships thumbnails for the first and the last few media only; the
# count is what the decision actually rests on. The last ones matter as much as
# the first: they show whether the end of a trip is covered.
PREVIEW_EDGE = 12

# The first strip scrolls, so the real start of a trip can be found in it. It
# holds at most this many; the thumbnails themselves load only when seen.
PREVIEW_SCROLL = 500

# The Trips tab shows this many, drawn at random from the whole trip (3 x 4).
TRIP_THUMBS = 12

# When an Immich album counts as holding a trip: it has at least this share of
# the trip's media, and more than this share of its own media falls within the
# trip's dates (give or take the slack). The second test is what keeps an album
# of one person, spanning years, from claiming every trip that person was on.
ALBUM_HOLDS = 0.5
ALBUM_WITHIN = 0.5
ALBUM_SLACK = dt.timedelta(days=2)
ALBUM_INDEX_TTL = 600               # seconds

# What an asset id looks like. Ids go into a URL path towards Immich, so
# anything else is refused rather than passed on.
ASSET_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def _edges(ids: list[str]) -> dict:
    """The first and the last few of a chronological list, without overlap.

    A short list is all "first" and has no "last", so nothing shows twice. A
    long one is split in halves, each of up to PREVIEW_SCROLL: the first strip
    is scrolled to find the real start, the last one to find the real end.
    """
    if len(ids) <= 2 * PREVIEW_EDGE:
        return {"thumbnails": ids, "thumbnails_last": []}
    half = min(PREVIEW_SCROLL, (len(ids) + 1) // 2)
    return {"thumbnails": ids[:half],
            "thumbnails_last": ids[max(half, len(ids) - PREVIEW_SCROLL):]}


def _sample(ids: list[str], taken: dict, seed: str) -> list[str]:
    """A dozen pictures drawn at random from a whole trip, oldest first.

    Random rather than the first few, so a card shows the whole trip and not
    just its opening day. Seeded by the trip's dates, so a card does not
    reshuffle every time the page is opened.
    """
    chosen = random.Random(seed).sample(ids, min(len(ids), TRIP_THUMBS))
    return sorted(chosen, key=lambda i: (taken.get(i) or dt.datetime.min, i))


DUPLICATES_FILE = "duplicates.json"
DUPLICATES_VERSION = 1


def _save_duplicates(directory: Path, albums: list[dict],
                     scanned_at: dt.datetime | None) -> None:
    path = Path(directory) / DUPLICATES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": DUPLICATES_VERSION,
               "scanned_at": scanned_at.isoformat() if scanned_at else None,
               "albums": albums}
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    temp.replace(path)


def _load_duplicates(directory: Path) -> tuple[list[dict] | None, dt.datetime | None]:
    """The cached duplicates scan; (None, None) when there is none to trust."""
    path = Path(directory) / DUPLICATES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != DUPLICATES_VERSION:
            return None, None
        return data["albums"], dt.datetime.fromisoformat(data["scanned_at"])
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("ignoring unreadable duplicates cache: %s", exc)
        return None, None


class ApiError(Exception):
    """Something the user should see, with the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class DesignApi:
    def __init__(self, client: ImmichClient, config_dir: Path,
                 state_dir: Path) -> None:
        self.client = client
        self.config_dir = Path(config_dir)
        self.state_dir = Path(state_dir)
        self._scan: list[trips_module.Point] | None = None
        self._scanned_at: dt.datetime | None = None
        self._albums: dict[str, set[str]] | None = None
        self._albums_read: float = 0.0
        self._dups: list[dict] | None = None
        self._dups_at: dt.datetime | None = None

    # -- one picture, for the hover card ----------------------------------

    def asset_details(self, asset_id: str) -> dict:
        """What the hover card shows for a picture: file, date, camera, place.

        Curated on purpose -- the browser gets these fields and not Immich's
        whole record, which carries paths and ids nobody needs there.
        """
        if not ASSET_ID.fullmatch(asset_id):
            raise ApiError("not an asset id")
        data = self.client.asset(asset_id)
        exif = data.get("exifInfo") or {}
        path = str(data.get("originalPath") or "")
        return {
            "id": asset_id,
            "file_name": data.get("originalFileName") or "",
            "folder": path.rsplit("/", 1)[0] if "/" in path else "",
            "kind": str(data.get("type") or "IMAGE"),
            "taken": data.get("localDateTime") or exif.get("dateTimeOriginal"),
            "time_zone": exif.get("timeZone"),
            "duration": data.get("duration") or None,
            "size_bytes": exif.get("fileSizeInByte"),
            "width": exif.get("exifImageWidth"),
            "height": exif.get("exifImageHeight"),
            "camera": " ".join(part for part in (exif.get("make"), exif.get("model"))
                               if part) or None,
            "lens": exif.get("lensModel"),
            "f_number": exif.get("fNumber"),
            "focal_length": exif.get("focalLength"),
            "iso": exif.get("iso"),
            "exposure": exif.get("exposureTime"),
            "latitude": exif.get("latitude"),
            "longitude": exif.get("longitude"),
            "city": exif.get("city"),
            "state": exif.get("state"),
            "country": exif.get("country"),
            "description": exif.get("description") or None,
            "people": [p["name"] for p in data.get("people") or []
                       if p.get("name")],
            "favorite": bool(data.get("isFavorite")),
        }

    # -- configuration ----------------------------------------------------

    def config(self):
        """Re-read the config on every call: the daemon may have nothing to do
        with the last thing this UI wrote, and hand edits should show up."""
        return config_module.load(self.config_dir)

    def _butler(self, config) -> Butler:
        return Butler(self.client, config, State.load(self.state_dir))

    # -- pickers ----------------------------------------------------------

    def people(self, query: str = "") -> dict:
        """Named people for the Who picker.

        Falls back to listing when there is no query, because the UI opens
        before anybody has typed anything.
        """
        found = (self.client.find_people(query) if query.strip()
                 else self.client.people())
        if query.strip() and not found:
            # find_people is an exact match; offer prefix hits so typing half a
            # name still shows something.
            folded = query.strip().casefold()
            found = [p for p in self.client.people()
                     if p.name.casefold().startswith(folded)]
        return {"people": [{"id": p.id, "name": p.name} for p in found]}

    def places(self, kind: str, country: str = "", state: str = "") -> dict:
        if kind not in ("country", "state", "city"):
            raise ApiError(f"unknown place type {kind!r}")
        values = self.client.suggestions(kind, country=country or None,
                                         state=state or None)
        return {"type": kind, "values": values}

    def groups(self) -> dict:
        config = self.config()
        return {"groups": [{"name": name, "members": list(members)}
                           for name, members in sorted(config.groups.items())]}

    def save_group(self, name: str, members: list[str]) -> dict:
        name = (name or "").strip()
        if not name:
            raise ApiError("a group needs a name")
        members = [m.strip() for m in members if m and m.strip()]
        if not members:
            raise ApiError(f"group {name!r} has no members")
        if name in members:
            raise ApiError(f"group {name!r} cannot be a member of itself")

        config = self.config()
        groups = dict(config.groups)
        groups[name] = tuple(dict.fromkeys(members))
        config_module.write_config(self.config_dir, config.with_groups(groups))
        return self.groups()

    def delete_group(self, name: str) -> dict:
        config = self.config()
        groups = dict(config.groups)
        if name not in groups:
            raise ApiError(f"no group named {name!r}", status=404)
        used = [a.name for a in config.albums if name in a.match.people]
        if used:
            raise ApiError(f"group {name!r} is still used by: {', '.join(used)}")
        del groups[name]
        config_module.write_config(self.config_dir, config.with_groups(groups))
        return self.groups()

    # -- albums -----------------------------------------------------------

    def albums(self) -> dict:
        config = self.config()
        state = State.load(self.state_dir)
        butler = Butler(self.client, config, state)
        rows = []
        for album in config.albums:
            record = state.albums.get(album.slug)
            # The cover the album has in Immich now; the list is a nicety, so
            # an unreachable server leaves it out rather than failing the page.
            try:
                info = butler.find_album(album)
            except ImmichError:
                info = None
            rows.append({
                "slug": album.slug, "name": album.name,
                # What Immich calls it: the name plus the fixed/updating suffix.
                "immich_name": butler.marked_name(album),
                "cover_asset": info.cover_asset_id if info else None,
                "enabled": album.enabled, "sync": album.sync,
                "cover": album.cover,
                "share_with": list(album.share_with),
                "share_role": album.share_role,
                "schedule": str(album.schedule),
                "schedule_inherited": album.schedule_inherited,
                "match": rule_to_json(album.match),
                "last_run": getattr(record, "last_run", None),
                "last_result": getattr(record, "last_result", None),
                "last_error": getattr(record, "last_error", None),
            })
        return {"albums": rows,
                "default_schedule": str(config.settings.schedule),
                "errors": config.errors}

    def accounts(self) -> dict:
        """The other accounts on this server, for the share picker.

        A key without `user.read` gets an empty list and a note rather than an
        error: sharing is one optional feature, and design mode has to stay
        usable without it.
        """
        try:
            users = self.client.users()
        except ImmichError as exc:
            return {"accounts": [], "unavailable": str(exc)}
        return {"accounts": [{"name": user.name, "email": user.email}
                             for user in users]}

    def save_album(self, payload: dict) -> dict:
        album = self._album_from(payload)
        config = self.config()
        existing = config.album(album.slug)
        if existing is not None and existing.name != album.name:
            # The name is how a lost state file re-finds the Immich album, so
            # renaming quietly would orphan it.
            log.info("album %s renamed from %r to %r",
                     album.slug, existing.name, album.name)
        path = config_module.write_config(self.config_dir, config.with_album(album))
        return {"saved": album.slug, "path": str(path),
                "renamed_from": existing.name if existing and
                existing.name != album.name else None}

    def delete_album(self, slug: str) -> dict:
        """Removes the rule. The Immich album itself is never touched."""
        config = self.config()
        if config.album(slug) is None:
            raise ApiError(f"no album config {slug!r}", status=404)
        config_module.write_config(self.config_dir, config.without_album(slug))
        return {"deleted": slug,
                "note": "the rule is gone; the Immich album is untouched"}

    # -- preview, analyze, run --------------------------------------------

    def preview(self, payload: dict) -> dict:
        """Exactly what a run would do, computed without writing anything."""
        album = self._album_from(payload, require_name=False)
        config = self.config()
        butler = self._butler(config)
        try:
            plan = butler.plan(album)
        except (MatchError, ImmichError) as exc:
            raise ApiError(str(exc)) from None

        edges = _edges(plan.matched)
        shown = edges["thumbnails"] + edges["thumbnails_last"]
        return {
            "immich_name": butler.marked_name(album) if album.name != "(draft)" else None,
            # When each shown picture was taken, to the second: picking one as
            # the album's first or last photo sets the rule's bound to it.
            "taken": {i: plan.taken[i].replace(microsecond=0).isoformat()
                      for i in shown if plan.taken.get(i)},
            "matched": len(plan.matched),
            "to_add": len(plan.to_add),
            "to_remove": len(plan.to_remove),
            "already_in_album": plan.existing,
            "creates_album": plan.creates_album,
            "extends": self._extends(butler, plan),
            "summary": plan.summary(),
            "warnings": plan.warnings,
            **edges,
            # The cover the rule picks, so the builder can show which picture
            # would end up on the front before anything is saved.
            "cover": album.cover,
            "cover_asset": plan.cover_asset_id,
            # How many accounts would gain access, so the builder can say so
            # before anything is saved.
            "to_share": len(plan.to_share),
            "next_run": self._next_run(album, config),
        }

    @staticmethod
    def _extends(butler: Butler, plan) -> dict | None:
        """The Immich album a run would take over, or None.

        An album found by name that the butler has never kept -- no remembered
        id, no marker -- is somebody else's, an import say: a run renames it
        and adds to it. The builder says so loudly rather than in a count.
        """
        info = plan.album_info
        if info is None:
            return None
        if butler.state.for_album(plan.album.slug).album_id == info.id:
            return None
        if butler.unmarked(info.name) != info.name:
            return None
        return {"name": info.name, "asset_count": plan.existing,
                "cover_asset": info.cover_asset_id,
                "rename_to": plan.rename_to}

    def existing_albums(self) -> dict:
        """The user's own Immich albums no rule keeps, for the name picker.

        Picking one makes the rule extend that album instead of building a new
        one; typing its name exactly by hand is too easy to get wrong. Only
        owned albums: the butler renames what it adopts, and only the owner
        may. An unreadable album list is an empty picker, not an error.
        """
        config = self.config()
        butler = self._butler(config)
        try:
            # Without `user.read` there is no telling whose an album is; the
            # picker then offers them all rather than none.
            me_id = self.client.me().id
        except ImmichError:
            me_id = None
        try:
            everything = butler.albums()
            claimed = set()
            for album in config.albums:
                info = butler.find_album(album)
                if info is not None:
                    claimed.add(info.id)
        except ImmichError as exc:
            return {"albums": [], "unavailable": str(exc)}
        rows = [{"name": info.name, "asset_count": info.asset_count,
                 "cover_asset": info.cover_asset_id}
                for info in everything
                if info.id not in claimed
                and (me_id is None or info.owner_id in (None, me_id))]
        rows.sort(key=lambda row: row["name"].casefold())
        return {"albums": rows}

    def analyze(self, payload: dict) -> dict:
        """Near-misses for a rule: what it almost, but does not, include."""
        album = self._album_from(payload, require_name=False)
        config = self.config()
        try:
            plan = self._butler(config).plan(album)
        except (MatchError, ImmichError) as exc:
            raise ApiError(str(exc)) from None

        points, scanned_at = self._points()
        if not points:
            return {"suggestions": [], "scanned_at": None,
                    "note": "no library scan yet -- open the Trips tab to build one"}

        found = analyze_module.analyze(points, plan.matched, album.match)
        people = analyze_module.people_elsewhere(self.client, album.match,
                                                 plan.matched)
        if people is not None:
            found.append(people)
        return {"suggestions": [s.to_json() for s in found],
                "matched": len(plan.matched),
                "scanned_at": scanned_at.isoformat() if scanned_at else None}

    def add_assets(self, payload: dict) -> dict:
        """A one-off additive add, for an analyze group the rule cannot express.

        Not recorded anywhere: the config holds rules, never asset ids. A later
        scheduled run will leave these in place, since `add` never removes.
        """
        asset_ids = [str(i) for i in (payload.get("asset_ids") or [])]
        if not asset_ids:
            raise ApiError("no media given")
        album = self._album_from(payload, require_name=False)
        config = self.config()
        butler = self._butler(config)
        info = butler.find_album(album)
        if info is None:
            raise ApiError(f"album {butler.marked_name(album)!r} does not exist "
                           f"in Immich yet; save and run it first")
        added = self.client.add_assets(info.id, asset_ids)
        return {"added": added, "requested": len(asset_ids), "album": info.name}

    def run(self, payload: dict, dry_run: bool = False) -> dict:
        """Run one saved album now. Only saved albums: a run writes to Immich,
        and it should write what the config says, not an unsaved draft."""
        slug = str(payload.get("slug") or "").strip()
        config = self.config()
        album = config.album(slug)
        if album is None:
            raise ApiError(f"no saved album {slug!r} -- save it first", status=404)

        state = State.load(self.state_dir)
        butler = Butler(self.client, config, state)
        report = butler.run_album(album, dry_run=dry_run)
        if not dry_run:
            state.save()
        return {"album": butler.marked_name(album), "added": report.added,
                "removed": report.removed, "created": report.created,
                "error": report.error, "dry_run": dry_run}

    # -- duplicates -------------------------------------------------------

    def duplicates(self, rescan: bool = False) -> dict:
        """The overview: every owned album holding duplicates, most first.

        Read from a cached scan (see _duplicate_scan), like the Trips tab.
        """
        albums, scanned_at = self._duplicate_scan(rescan=rescan)
        managed = self._rule_managed_ids()
        rows = [{"album_id": a["album_id"], "name": a["name"],
                 "groups": len(a["groups"]), "removable": a["removable"],
                 "cover": a["groups"][0]["keep"],
                 "rule_managed": a["album_id"] in managed}
                for a in albums]
        rows.sort(key=lambda r: (-r["removable"], r["name"].casefold()))
        return {"albums": rows,
                "scanned_at": scanned_at.isoformat() if scanned_at else None}

    def duplicate_album(self, album_id: str) -> dict:
        """One album's duplicate groups, from the cached scan."""
        if not ASSET_ID.fullmatch(album_id or ""):
            raise ApiError("bad album id")
        albums, scanned_at = self._duplicate_scan()
        for album in albums:
            if album["album_id"] == album_id:
                return {**album,
                        "rule_managed": album_id in self._rule_managed_ids(),
                        "scanned_at": scanned_at.isoformat() if scanned_at else None}
        raise ApiError("that album has no duplicates (any more)", status=404)

    def _duplicate_scan(self, rescan: bool = False):
        """Albums with duplicates, cached in the state dir.

        Scanned when there is no cache, when asked, and on the first use each
        day -- the same rhythm as the Trips tab's library scan. Unlike that one
        it takes seconds, so a missing cache is scanned without asking.
        """
        if self._dups is None and not rescan:
            self._dups, self._dups_at = _load_duplicates(self.state_dir)
        stale = (self._dups_at is not None
                 and self._dups_at.date() < dt.date.today())
        if rescan or stale or self._dups is None:
            try:
                albums = self._duplicates_by_album(self.client.duplicates())
            except ImmichError as exc:
                raise ApiError(str(exc), status=502) from None
            self._dups, self._dups_at = albums, dt.datetime.now().replace(microsecond=0)
            _save_duplicates(self.state_dir, self._dups, self._dups_at)
        return self._dups, self._dups_at

    def _rule_managed_ids(self) -> set[str]:
        """Albums a scheduled rule keeps filling: removed copies come back."""
        try:
            config = self.config()
            butler = self._butler(config)
            ids = set()
            for album in config.albums:
                if album.schedule.automatic:
                    info = butler.find_album(album)
                    if info is not None:
                        ids.add(info.id)
            return ids
        except (ImmichError, config_module.ConfigError):
            return set()

    def _owned_albums(self):
        try:
            me_id = self.client.me().id
        except ImmichError:
            me_id = None
        return [a for a in self.client.albums()
                if me_id is None or a.owner_id in (None, me_id)]

    def _duplicates_by_album(self, groups, only_album: str | None = None) -> list[dict]:
        multi = [(gid, ids) for gid, ids in groups if len(ids) > 1]
        rows = []
        if not multi:
            return rows
        for album in self._owned_albums():
            if only_album is not None and album.id != only_album:
                continue
            assets = {a.id: a for a in self.client.album_assets(album.id)}
            shown = []
            for gid, ids in multi:
                members = [assets[i] for i in ids if i in assets]
                if len(members) < 2:
                    continue
                members.sort(key=lambda a: (a.taken_at is None,
                                            a.taken_at or dt.datetime.min, a.id))
                shown.append({
                    "duplicate_id": gid, "keep": members[0].id,
                    "assets": [{"id": a.id, "file_name": a.file_name,
                                "taken_at": a.taken_at.isoformat() if a.taken_at else None}
                               for a in members]})
            if shown:
                rows.append({"album_id": album.id, "name": album.name,
                             "removable": sum(len(g["assets"]) - 1 for g in shown),
                             "groups": shown})
        rows.sort(key=lambda r: r["name"].casefold())
        return rows

    def remove_duplicates(self, payload: dict) -> dict:
        """Take the chosen extras out of one album. Never deletes from the library.

        The request is checked against Immich's groups afresh, not the cache:
        every id must be in a group with two or more members in that album, and
        one member of each group must be left.
        """
        album_id = str(payload.get("album_id") or "")
        asset_ids = [str(i) for i in (payload.get("asset_ids") or [])]
        if not ASSET_ID.fullmatch(album_id) or not asset_ids:
            raise ApiError("no album or no media given")
        if not all(ASSET_ID.fullmatch(i) for i in asset_ids):
            raise ApiError("bad media id")
        try:
            rows = self._duplicates_by_album(self.client.duplicates(),
                                             only_album=album_id)
            if not rows:
                raise ApiError("that album has no duplicates (any more)", status=404)
            wanted = set(asset_ids)
            allowed = set()
            for group in rows[0]["groups"]:
                members = {a["id"] for a in group["assets"]}
                doomed = members & wanted
                if len(doomed) >= len(members):
                    raise ApiError("at least one copy of each group must stay")
                allowed |= doomed
            if wanted - allowed:
                raise ApiError("some media are not duplicates in that album")
            removed = self.client.remove_assets(album_id, sorted(wanted))
        except ImmichError as exc:
            raise ApiError(str(exc), status=502) from None
        self._albums = None
        self._forget_removed(album_id, wanted)
        return {"removed": removed, "requested": len(wanted)}

    def _forget_removed(self, album_id: str, removed: set[str]) -> None:
        """Update the cached scan after a removal, rather than rescanning."""
        albums, scanned_at = self._duplicate_scan()
        kept = []
        for album in albums:
            if album["album_id"] == album_id:
                groups = []
                for group in album["groups"]:
                    assets = [a for a in group["assets"] if a["id"] not in removed]
                    if len(assets) > 1:
                        keep = (group["keep"] if group["keep"] not in removed
                                else assets[0]["id"])
                        groups.append({**group, "assets": assets, "keep": keep})
                if not groups:
                    continue
                album = {**album, "groups": groups,
                         "removable": sum(len(g["assets"]) - 1 for g in groups)}
            kept.append(album)
        self._dups = kept
        _save_duplicates(self.state_dir, kept, scanned_at)

    # -- browse -----------------------------------------------------------

    def browse_albums(self) -> dict:
        """Every Immich album this key can see, the butler's or not.

        Albums shared with this account are listed too: looking is all the
        Browse tab does, and anyone who may see an album may look into it.
        """
        try:
            everything = self.client.albums()
        except ImmichError as exc:
            raise ApiError(str(exc), status=502) from None
        try:
            me_id = self.client.me().id
        except ImmichError:
            me_id = None
        managed = self._rule_ids()
        rows = [{"album_id": info.id, "name": info.name,
                 "asset_count": info.asset_count, "cover": info.cover_asset_id,
                 "shared": (me_id is not None and info.owner_id is not None
                            and info.owner_id != me_id),
                 "butler": info.id in managed}
                for info in everything]
        rows.sort(key=lambda row: row["name"].casefold())
        return {"albums": rows}

    def browse_album(self, album_id: str) -> dict:
        """One album's media with what the Browse tab groups them by.

        The grouping itself happens in the browser, so switching between
        folder, date, camera and place needs no second trip to Immich.
        """
        if not ASSET_ID.fullmatch(album_id or ""):
            raise ApiError("bad album id")
        try:
            info = next((a for a in self.client.albums() if a.id == album_id), None)
            if info is None:
                raise ApiError("no such album", status=404)
            assets = list(self.client.search_metadata(album_ids=[album_id]))
        except ImmichError as exc:
            raise ApiError(str(exc), status=502) from None
        assets.sort(key=lambda a: (a.taken_at is None,
                                   a.taken_at or dt.datetime.min, a.id))
        return {
            "album_id": album_id, "name": info.name,
            "assets": [{
                "id": a.id,
                "taken_at": a.taken_at.isoformat() if a.taken_at else None,
                "kind": a.kind, "file_name": a.file_name,
                "folder": (a.original_path.rsplit("/", 1)[0]
                           if "/" in a.original_path else ""),
                "camera": a.camera, "city": a.city, "country": a.country,
            } for a in assets],
        }

    def _rule_ids(self) -> set[str]:
        """Albums some rule of the butler keeps, scheduled or not."""
        try:
            config = self.config()
            butler = self._butler(config)
            ids = set()
            for album in config.albums:
                info = butler.find_album(album)
                if info is not None:
                    ids.add(info.id)
            return ids
        except (ImmichError, config_module.ConfigError):
            return set()

    # -- trips ------------------------------------------------------------

    def trips(self, rescan: bool = False, away_km: float = trips_module.AWAY_KM,
              min_assets: int = trips_module.MIN_ASSETS,
              min_days: int = trips_module.MIN_DAYS) -> dict:
        points, scanned_at = self._points(rescan=rescan)
        if not points:
            return {"trips": [], "scanned_at": None, "assets": 0}

        config = self.config()
        covered = self._covered_windows(config)
        found = trips_module.detect(points, away_km=away_km,
                                    min_assets=min_assets, min_days=min_days)
        taken = {p.id: p.taken_at for p in points}
        albums_error = None
        try:
            index = self._album_index(refresh=rescan)
        except ImmichError as exc:
            index, albums_error = None, str(exc)
        rows = []
        for trip in found:
            rows.append({
                "start": trip.start.isoformat(), "end": trip.end.isoformat(),
                "days": trip.days, "located": trip.located,
                "unlocated": trip.unlocated, "total": trip.total,
                "countries": trip.countries, "states": trip.states,
                "cities": trip.cities,
                "name": trip.suggested_name(),
                "slug": slugify(trip.suggested_name()),
                "covered_by": _covering(trip, covered),
                "in_album": (_in_album(trip, index, taken)
                             if index is not None else None),
                "thumbnails": _sample(trip.asset_ids, taken,
                                      seed=f"{trip.start}{trip.end}"),
            })
        return {"trips": rows, "assets": len(points),
                "albums_error": albums_error,
                "scanned_at": scanned_at.isoformat() if scanned_at else None}

    def _album_index(self, refresh: bool = False) -> dict[str, set[str]]:
        """Every album in Immich and the media it holds, by album name.

        One search per album, so it is kept for a while rather than read on
        every visit to the Trips tab; a rescan reads it afresh.
        """
        stale = time.monotonic() - self._albums_read > ALBUM_INDEX_TTL
        if self._albums is None or refresh or stale:
            self._albums = {album.name: self.client.album_asset_ids(album.id)
                            for album in self.client.albums()}
            self._albums_read = time.monotonic()
        return self._albums

    def _points(self, rescan: bool = False):
        if self._scan is None and not rescan:
            self._scan, self._scanned_at = trips_module.load_scan(self.state_dir)
        # The first use each day refreshes an existing scan, so media deleted
        # or added since do not linger as broken thumbnails or missing trips.
        # With no scan at all it stays a button press: the first one is long.
        stale = (self._scanned_at is not None
                 and self._scanned_at.date() < dt.date.today())
        if rescan or stale:
            if stale and not rescan:
                log.info("scan from %s is out of date; rescanning",
                         self._scanned_at.isoformat(timespec="minutes"))
            points = trips_module.scan(self.client)
            trips_module.save_scan(self.state_dir, points)
            self._scan, self._scanned_at = points, dt.datetime.now()
            self._albums = None         # read the albums afresh as well
        return self._scan, self._scanned_at

    def _covered_windows(self, config) -> list[tuple[str, dt.date, dt.date]]:
        butler = self._butler(config)
        windows = []
        for album in config.albums:
            rule = album.match
            if rule.from_date and rule.to_date:
                windows.append((butler.marked_name(album), rule.from_date,
                                rule.to_date))
        return windows

    # -- building an Album from the UI's JSON ------------------------------

    def _album_from(self, payload: dict, require_name: bool = True) -> Album:
        name = str(payload.get("name") or "").strip()
        if not name and require_name:
            raise ApiError("the album needs a name")
        slug = str(payload.get("slug") or "").strip() or slugify(name or "draft")

        raw = payload.get("auto-update-schedule", payload.get("schedule"))
        inherited = raw in (None, "", "inherit")
        settings = self.config().settings
        if inherited:
            schedule = settings.schedule
        else:
            try:
                schedule = parse_schedule(str(raw))
            except ScheduleError as exc:
                raise ApiError(f"auto-update-schedule: {exc}") from None

        sync = str(payload.get("sync") or "add").lower()
        if sync not in config_module.SYNC_MODES:
            raise ApiError(f"sync must be add or mirror, got {sync!r}")

        rule = self._rule_from(payload.get("match") or {})
        cover = str(payload.get("cover") or cover_module.AUTO).strip() or cover_module.AUTO
        if cover == cover_module.EVERYONE and not rule.people:
            raise ApiError('a cover of "everyone" needs the rule to name people')

        share_with, share_role = self._sharing_from(payload)

        return Album(slug=slug, name=name or "(draft)", match=rule,
                     schedule=schedule, schedule_inherited=inherited,
                     enabled=bool(payload.get("enabled", True)), sync=sync,
                     cover=cover, share_with=share_with, share_role=share_role)

    def _sharing_from(self, payload: dict) -> tuple[tuple[str, ...], str]:
        """Read the builder's share picker.

        Saving rewrites the whole config file, so a payload that simply omits
        `share_with` would silently drop sharing from a rule that had it --
        the picker always sends the field, and an absent one means nobody.
        """
        raw = payload.get("share_with") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise ApiError("share_with must be a list of account names")
        names = [str(entry).strip() for entry in raw if str(entry).strip()]

        role = str(payload.get("share_role") or immich_module.VIEWER).lower()
        if role not in immich_module.SHARE_ROLES:
            raise ApiError(f"share_role must be one of "
                           f"{', '.join(immich_module.SHARE_ROLES)}, got {role!r}")
        return tuple(dict.fromkeys(names)), role

    def _rule_from(self, data: dict) -> MatchRule:
        from_date, from_time = _as_bound(data.get("from"), "from")
        to_date, to_time = _as_bound(data.get("to"), "to")
        rule = MatchRule(
            from_date=from_date, to_date=to_date,
            from_time=from_time, to_time=to_time,
            countries=_as_names(data.get("countries")),
            states=_as_names(data.get("states")),
            cities=_as_names(data.get("cities")),
            people=_as_names(data.get("people")),
            people_mode=str(data.get("people_mode") or "any").lower(),
            include_unlocated=bool(data.get("include_unlocated", True)))

        if rule.people_mode not in config_module.PEOPLE_MODES:
            raise ApiError(f"people_mode must be any or all, "
                           f"got {rule.people_mode!r}")
        first = rule.from_time or rule.from_date
        last = rule.to_time or rule.to_date
        if rule.from_date and rule.to_date and (
                rule.from_date > rule.to_date
                or (rule.from_time and rule.to_time and rule.from_time > rule.to_time)):
            raise ApiError(f"the start ({first}) is after the end ({last})")
        if rule.is_empty:
            raise ApiError("this rule is empty, which would match the whole "
                           "library. Pick a date range, a place or a person.")
        return rule

    def _next_run(self, album: Album, config) -> str | None:
        now = dt.datetime.now(timezone_of(config))
        following = album.schedule.next_after(now)
        return following.isoformat(timespec="minutes") if following else None


# --------------------------------------------------------------------------
# JSON <-> rule
# --------------------------------------------------------------------------

def rule_to_json(rule: MatchRule) -> dict:
    return {
        "from": _bound_json(rule.from_date, rule.from_time),
        "to": _bound_json(rule.to_date, rule.to_time),
        "countries": list(rule.countries), "states": list(rule.states),
        "cities": list(rule.cities), "people": list(rule.people),
        "people_mode": rule.people_mode,
        "include_unlocated": rule.include_unlocated,
    }


def _bound_json(day: dt.date | None, exact: dt.datetime | None) -> str | None:
    if exact:
        return exact.isoformat()
    return day.isoformat() if day else None


def _as_bound(value, key: str) -> tuple[dt.date | None, dt.datetime | None]:
    """A day, or the moment a picked first or last photo was taken."""
    if value in (None, ""):
        return None, None
    if isinstance(value, dt.datetime):
        exact = value.replace(microsecond=0, tzinfo=None)
        return exact.date(), exact
    if isinstance(value, dt.date):
        return value, None
    text = str(value)
    try:
        if "T" in text:
            exact = dt.datetime.fromisoformat(text).replace(microsecond=0, tzinfo=None)
            return exact.date(), exact
        return dt.date.fromisoformat(text), None
    except ValueError:
        raise ApiError(f"{key} is not a date: {value!r} (use 2019-07-01)") from None


def _as_names(value) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ApiError("expected a list of names")
    cleaned = [str(v).strip() for v in value if str(v).strip()]
    return tuple(dict.fromkeys(cleaned))


def _covering(trip, windows) -> str | None:
    """Whether a saved album already covers most of this trip."""
    for name, start, end in windows:
        latest_start = max(trip.start, start)
        earliest_end = min(trip.end, end)
        overlap = (earliest_end - latest_start).days + 1
        if overlap > 0 and overlap >= trip.days * 0.6:
            return name
    return None


def _in_album(trip, index: dict[str, set[str]], taken: dict) -> dict | None:
    """The Immich album that already holds this trip, if any.

    Asset-based, so it finds albums made by hand or imported as well as the
    butler's own. Media the scan has no date for count as outside the trip.
    """
    ids = set(trip.asset_ids)
    if not ids:
        return None
    first = dt.datetime.combine(trip.start, dt.time.min) - ALBUM_SLACK
    last = dt.datetime.combine(trip.end, dt.time.max) + ALBUM_SLACK
    best = None
    for name, members in index.items():
        if not members:
            continue
        held = len(ids & members) / len(ids)
        if held < ALBUM_HOLDS:
            continue
        within = sum(1 for i in members
                     if (when := taken.get(i)) is not None and first <= when <= last)
        if within / len(members) <= ALBUM_WITHIN:
            continue
        if best is None or held > best["share"]:
            best = {"name": name, "share": held}
    if best:
        best["share"] = round(best["share"] * 100)
    return best
