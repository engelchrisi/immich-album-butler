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
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config as config_module
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

    @property
    def changes(self) -> bool:
        return bool(self.to_add or self.to_remove or self.creates_album)

    def summary(self) -> str:
        if self.creates_album:
            return (f"create album {self.album.name!r} with "
                    f"{len(self.to_add)} asset(s)")
        parts = [f"{len(self.matched)} match"]
        if self.to_add:
            parts.append(f"+{len(self.to_add)}")
        if self.to_remove:
            parts.append(f"-{len(self.to_remove)}")
        if not self.to_add and not self.to_remove:
            parts.append("already up to date")
        return f"{self.album.name!r}: " + ", ".join(parts)


@dataclass
class RunReport:
    slug: str
    name: str
    added: int = 0
    removed: int = 0
    created: bool = False
    error: str | None = None
    dry_run: bool = False

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

    def find_album(self, album: Album) -> AlbumInfo | None:
        """By remembered id first, then by name -- so a lost state file recovers."""
        remembered = self.state.for_album(album.slug).album_id
        existing = self.albums()
        if remembered:
            for info in existing:
                if info.id == remembered:
                    return info
            log.info("album %r: remembered album is gone, matching by name instead",
                     album.name)
        for info in existing:
            if info.name == album.name:
                return info
        return None

    def plan(self, album: Album) -> Plan:
        people = self.people() if album.match.people else []
        expanded, problems = self.config.expand_people(album.match.people)
        rule = album.match
        if expanded != list(rule.people):
            rule = _with_people(rule, tuple(expanded))

        result = match(self.client, rule, people)
        matched = result.ids

        info = self.find_album(album)
        plan = Plan(album=album, matched=matched, warnings=problems + result.warnings)

        if info is None:
            plan.creates_album = True
            plan.to_add = matched
            return plan

        plan.album_id = info.id
        current = self.client.album_asset_ids(info.id)
        plan.existing = len(current)
        plan.to_add = [asset_id for asset_id in matched if asset_id not in current]
        if album.mirrors:
            wanted = set(matched)
            plan.to_remove = sorted(current - wanted)
        return plan

    # -- applying ---------------------------------------------------------

    def apply(self, plan: Plan, dry_run: bool = False) -> RunReport:
        album = plan.album
        report = RunReport(slug=album.slug, name=album.name, dry_run=dry_run)
        if dry_run:
            report.added = len(plan.to_add)
            report.removed = len(plan.to_remove)
            report.created = plan.creates_album
            return report

        if plan.creates_album:
            info = self.client.create_album(album.name, asset_ids=plan.to_add)
            report.created = True
            report.added = len(plan.to_add)
            plan.album_id = info.id
            self._albums = None
        else:
            assert plan.album_id is not None
            if plan.to_add:
                report.added = self.client.add_assets(plan.album_id, plan.to_add)
            if plan.to_remove:
                report.removed = self.client.remove_assets(plan.album_id, plan.to_remove)

        record = self.state.for_album(album.slug)
        record.album_id = plan.album_id
        record.assets_added = report.added
        record.assets_removed = report.removed
        return report

    # -- one pass ----------------------------------------------------------

    def run_album(self, album: Album, dry_run: bool = False,
                  now: dt.datetime | None = None) -> RunReport:
        record = self.state.for_album(album.slug)
        try:
            plan = self.plan(album)
            report = self.apply(plan, dry_run=dry_run)
            for warning in plan.warnings:
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
