"""Runtime mode: keep the albums matching their rules, on a schedule.

Planning and applying are separate on purpose. `plan()` is read-only and is
what `--dry-run` and design mode's preview both use; `apply()` is the only code
in the project that writes to Immich.

One album's failure never stops the others: a missing person or an unreachable
moment should not mean every other album silently stops updating.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config as config_module
from . import cover
from .config import Album, Config
from .immich import AlbumInfo, ImmichClient, ImmichError, Person
from .matcher import MatchError, match
from .state import State

log = logging.getLogger(__name__)

TICK_SECONDS = 60


@dataclass
class Plan:
    """What one album run would do. Read-only; produced without writing."""

    album: Album
    matched: list[str] = field(default_factory=list)
    to_add: list[str] = field(default_factory=list)
    to_remove: list[str] = field(default_factory=list)
    existing: int = 0
    album_id: str | None = None
    creates_album: bool = False
    warnings: list[str] = field(default_factory=list)
    # The asset the cover should point at, set only when the album asks for a
    # cover and the one it has now is a different picture.
    cover_asset_id: str | None = None
    # The name the album should carry in Immich, set only when the marker
    # suffix is on and the album does not carry it yet.
    rename_to: str | None = None

    @property
    def changes(self) -> bool:
        return bool(self.to_add or self.to_remove or self.creates_album
                    or self.cover_asset_id or self.rename_to)

    def summary(self) -> str:
        if self.creates_album:
            return (f"create album {self.album.name!r} with "
                    f"{len(self.to_add)} asset(s)")
        parts = [f"{len(self.matched)} match"]
        if self.to_add:
            parts.append(f"+{len(self.to_add)}")
        if self.to_remove:
            parts.append(f"-{len(self.to_remove)}")
        if self.cover_asset_id:
            parts.append("new cover")
        if self.rename_to:
            parts.append(f"rename to {self.rename_to!r}")
        if not (self.to_add or self.to_remove or self.cover_asset_id
                or self.rename_to):
            parts.append("already up to date")
        return f"{self.album.name!r}: " + ", ".join(parts)


@dataclass
class RunReport:
    slug: str
    name: str
    added: int = 0
    removed: int = 0
    created: bool = False
    cover_set: bool = False
    renamed: bool = False
    error: str | None = None
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


class Butler:
    """Ties together the client, the configuration and the remembered state."""

    def __init__(self, client: ImmichClient, config: Config, state: State) -> None:
        self.client = client
        self.config = config
        self.state = state
        self._people: list[Person] | None = None
        self._albums: list[AlbumInfo] | None = None

    # -- caches, refreshed once per pass ----------------------------------

    def people(self) -> list[Person]:
        if self._people is None:
            self._people = self.client.people()
        return self._people

    def albums(self) -> list[AlbumInfo]:
        if self._albums is None:
            self._albums = self.client.albums()
        return self._albums

    def invalidate(self) -> None:
        self._people = None
        self._albums = None

    # -- planning ---------------------------------------------------------

    def marked_name(self, album: Album) -> str:
        """The album's name in Immich, with the marker suffix if one is set."""
        suffix = self.config.settings.album_suffix
        if not suffix or album.name.endswith(suffix):
            return album.name
        return f"{album.name} {suffix}"

    def find_album(self, album: Album) -> AlbumInfo | None:
        """By remembered id first, then by name -- so a lost state file recovers.

        Both names count: an album that already carries the marker suffix and
        one that does not. Otherwise turning the suffix on would stop matching
        every album the butler has, and the next run would build a second copy
        of each one beside it.
        """
        remembered = self.state.for_album(album.slug).album_id
        existing = self.albums()
        if remembered:
            for info in existing:
                if info.id == remembered:
                    return info
            log.info("album %r: remembered album is gone, matching by name instead",
                     album.name)
        wanted = {self.marked_name(album), album.name}
        for info in existing:
            if info.name in wanted:
                return info
        # Nothing matched exactly. An album may still be one of ours carrying a
        # marker that is no longer configured -- the suffix was changed, or
        # turned off -- and on a container with no state file the name is all
        # there is to go on. So a trailing bracketed word is allowed to differ.
        for info in existing:
            if _without_marker(info.name) == album.name:
                log.info("album %r: adopting %r, which carries an old marker",
                         album.name, info.name)
                return info
        return None

    def plan(self, album: Album) -> Plan:
        # Names are resolved one by one against Immich (see resolve_people_via),
        # so the whole people list is not fetched here.
        expanded, problems = self.config.expand_people(album.match.people)
        rule = album.match
        if expanded != list(rule.people):
            rule = _with_people(rule, tuple(expanded))

        result = match(self.client, rule)
        matched = result.ids

        info = self.find_album(album)
        plan = Plan(album=album, matched=matched, warnings=problems + result.warnings)

        if info is None:
            plan.creates_album = True
            plan.to_add = matched
            plan.cover_asset_id = self._cover(album, result, None, plan)
            return plan

        plan.album_id = info.id
        wanted_name = self.marked_name(album)
        if info.name != wanted_name:
            plan.rename_to = wanted_name
        current = self.client.album_asset_ids(info.id)
        plan.existing = len(current)
        plan.to_add = [asset_id for asset_id in matched if asset_id not in current]
        if album.mirrors:
            wanted = set(matched)
            plan.to_remove = sorted(current - wanted)
        plan.cover_asset_id = self._cover(album, result, info.cover_asset_id, plan)
        return plan

    def _cover(self, album: Album, result, current: str | None,
               plan: Plan) -> str | None:
        """Which asset the cover should become, or None to leave it as it is.

        The choice is made among the assets the rule matched, which for a
        `sync = "add"` album may be fewer than the album holds -- a cover is a
        statement about the rule, so anything hand-added is deliberately not a
        candidate. A rule that cannot be satisfied (a file name nobody has)
        becomes a warning on this album, never a failed run.
        """
        if not album.sets_cover:
            return None
        try:
            chosen = cover.choose(album.cover, result.assets, result.by_person)
        except cover.CoverError as exc:
            plan.warnings.append(f"cover: {exc}")
            return None
        if chosen is None or chosen == current:
            return None
        return chosen

    # -- applying ---------------------------------------------------------

    def apply(self, plan: Plan, dry_run: bool = False) -> RunReport:
        album = plan.album
        report = RunReport(slug=album.slug, name=album.name, dry_run=dry_run)
        if dry_run:
            report.added = len(plan.to_add)
            report.removed = len(plan.to_remove)
            report.created = plan.creates_album
            report.cover_set = bool(plan.cover_asset_id)
            report.renamed = bool(plan.rename_to)
            return report

        if plan.creates_album:
            # Created with the marker already on it, which needs no permission
            # -- unlike renaming one that exists.
            info = self.client.create_album(self.marked_name(album),
                                            asset_ids=plan.to_add)
            report.created = True
            report.added = len(plan.to_add)
            plan.album_id = info.id
            self._albums = None
        else:
            assert plan.album_id is not None
            if plan.rename_to:
                report.renamed = self._rename(plan, report)
            if plan.to_add:
                report.added = self.client.add_assets(plan.album_id, plan.to_add)
            if plan.to_remove:
                report.removed = self.client.remove_assets(plan.album_id, plan.to_remove)

        if plan.cover_asset_id and plan.album_id:
            report.cover_set = self._set_cover(plan, report)

        record = self.state.for_album(album.slug)
        record.album_id = plan.album_id
        record.assets_added = report.added
        record.assets_removed = report.removed
        return report

    def _rename(self, plan: Plan, report: RunReport) -> bool:
        """Add the marker suffix to an album that has not got it yet.

        Needs `album.update`, like the cover. A key without it means the album
        keeps the name it has and keeps working; only the marker is missing,
        which is cosmetic, so it must not fail the run.
        """
        assert plan.album_id is not None and plan.rename_to is not None
        try:
            self.client.rename_album(plan.album_id, plan.rename_to)
            self._albums = None
            return True
        except ImmichError as exc:
            if exc.status == 403:
                report.warnings.append(
                    f"not renamed to {plan.rename_to!r}: the API key needs the "
                    f"'album.update' permission. Everything else worked.")
            else:
                report.warnings.append(f"not renamed: {exc}")
            return False

    def _set_cover(self, plan: Plan, report: RunReport) -> bool:
        """Set the cover, turning a missing permission into a warning.

        Setting a cover is the only call that needs `album.update` on the key.
        A key without it would otherwise make a run that added photos perfectly
        well look like a failure, so the album keeps its photos and says what
        the key is missing.
        """
        assert plan.album_id is not None
        try:
            self.client.set_album_cover(plan.album_id, plan.cover_asset_id)
            return True
        except ImmichError as exc:
            if exc.status == 403:
                report.warnings.append(
                    "the cover was not set: the API key needs the "
                    "'album.update' permission. Everything else worked.")
            else:
                report.warnings.append(f"the cover was not set: {exc}")
            return False

    # -- one pass ----------------------------------------------------------

    def run_album(self, album: Album, dry_run: bool = False,
                  now: dt.datetime | None = None) -> RunReport:
        record = self.state.for_album(album.slug)
        try:
            plan = self.plan(album)
            report = self.apply(plan, dry_run=dry_run)
            for warning in plan.warnings + report.warnings:
                log.warning("%s: %s", album.name, warning)
            log.info("%s", plan.summary())
            if not dry_run:
                record.last_run = (now or _now()).isoformat()
                record.last_result = plan.summary()
                record.last_error = None
            return report
        except (MatchError, ImmichError) as exc:
            log.error("album %r: %s", album.name, exc)
            if not dry_run:
                record.last_run = (now or _now()).isoformat()
                record.last_error = str(exc)
            return RunReport(slug=album.slug, name=album.name,
                             error=str(exc), dry_run=dry_run)

    def due_albums(self, now: dt.datetime) -> list[Album]:
        due = []
        for album in self.config.albums:
            if not album.enabled:
                continue
            last = self.state.for_album(album.slug).last_run_at
            if last is not None and last.tzinfo != now.tzinfo:
                last = last.astimezone(now.tzinfo)
            if album.schedule.is_due(last, now):
                due.append(album)
        return due


_MARKER = re.compile(r"\s*\[[^\[\]]{1,16}\]$")


def _without_marker(name: str) -> str:
    """A name with one trailing `[...]` token removed, e.g. "Italy 2019 [AB]"."""
    return _MARKER.sub("", name)


def _with_people(rule, people: tuple[str, ...]):
    from dataclasses import replace
    return replace(rule, people=people)


def _now(tz: dt.tzinfo | None = None) -> dt.datetime:
    return dt.datetime.now(tz or dt.timezone.utc).replace(microsecond=0)


def timezone_of(config: Config) -> dt.tzinfo:
    name = config.settings.timezone
    if not name:
        return _local_zone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r, falling back to the system zone", name)
        return _local_zone()


def _local_zone() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


# --------------------------------------------------------------------------
# entry points used by the CLI
# --------------------------------------------------------------------------

def run_once(client: ImmichClient, config: Config, state: State,
             only: str | None = None, dry_run: bool = False) -> list[RunReport]:
    """Update every enabled album now, or just the one named."""
    butler = Butler(client, config, state)
    tz = timezone_of(config)
    now = _now(tz)

    albums = config.albums
    if only:
        album = config.album(only)
        if album is None:
            known = ", ".join(a.slug for a in config.albums) or "none configured"
            raise SystemExit(f"no album {only!r} (known: {known})")
        albums = [album]
    else:
        albums = [a for a in albums if a.enabled]

    reports = [butler.run_album(album, dry_run=dry_run, now=now) for album in albums]
    if not dry_run:
        state.save()
    return reports


def run_forever(client: ImmichClient, config_dir: Path, state_dir: Path,
                tick: int = TICK_SECONDS) -> None:
    """The daemon: re-read the config each tick, run whatever is due.

    Re-reading every tick is what lets design mode save a rule and have it take
    effect without anyone restarting a service.
    """
    log.info("runtime mode started; watching %s", config_dir)
    while True:
        started = time.monotonic()
        try:
            config = config_module.load(config_dir)
            for problem in config.errors:
                log.error("config: %s", problem)
            state = State.load(state_dir)
            butler = Butler(client, config, state)
            tz = timezone_of(config)
            now = _now(tz)
            due = butler.due_albums(now)
            if due:
                log.info("%d album(s) due", len(due))
                for album in due:
                    butler.run_album(album, now=now)
                state.save()
        except config_module.ConfigError as exc:
            log.error("configuration unusable: %s", exc)
        except Exception:                    # noqa: BLE001 - the daemon must survive
            log.exception("unexpected error during this pass; continuing")

        elapsed = time.monotonic() - started
        time.sleep(max(1.0, tick - elapsed))
