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
from .immich import AlbumInfo, ImmichClient, ImmichError, Person, User
from .matcher import MatchError, match
from .state import State

log = logging.getLogger(__name__)

TICK_SECONDS = 60
# How often the daemon re-checks the [[shares]] rules. They cost one album
# listing and one user listing, and the albums they name change rarely.
SHARE_SECONDS = 3600


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
    # Access to grant: (user id, role) for each account named in `share_with`
    # that cannot see the album yet, or sees it in the other role.
    to_share: list[tuple[str, str]] = field(default_factory=list)
    # Who can see the album now (user id -> role), so applying can tell a new
    # grant from a role change: Immich refuses to re-add an existing member.
    current_shares: dict[str, str] = field(default_factory=dict)

    @property
    def changes(self) -> bool:
        return bool(self.to_add or self.to_remove or self.creates_album
                    or self.cover_asset_id or self.rename_to or self.to_share)

    def summary(self) -> str:
        if self.creates_album:
            return (f"create album {self.album.name!r} with "
                    f"{len(self.to_add)} media")
        parts = [f"{len(self.matched)} match"]
        if self.to_add:
            parts.append(f"+{len(self.to_add)}")
        if self.to_remove:
            parts.append(f"-{len(self.to_remove)}")
        if self.cover_asset_id:
            parts.append("new cover")
        if self.rename_to:
            parts.append(f"rename to {self.rename_to!r}")
        if self.to_share:
            parts.append(f"share with {len(self.to_share)}")
        if not (self.to_add or self.to_remove or self.cover_asset_id
                or self.rename_to or self.to_share):
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
    shared: int = 0
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
        self._users: list[User] | None = None

    # -- caches, refreshed once per pass ----------------------------------

    def people(self) -> list[Person]:
        if self._people is None:
            self._people = self.client.people()
        return self._people

    def albums(self) -> list[AlbumInfo]:
        if self._albums is None:
            self._albums = self.client.albums()
        return self._albums

    def users(self) -> list[User]:
        """The other accounts, fetched once per pass and only if one is named.

        Nothing else needs `user.read`, so an installation that shares no album
        never makes this call and never needs the permission.
        """
        if self._users is None:
            self._users = self.client.users()
        return self._users

    def invalidate(self) -> None:
        self._people = None
        self._albums = None
        self._users = None

    # -- planning ---------------------------------------------------------

    def marked_name(self, album: Album) -> str:
        """The album's name in Immich, with the marker suffix if one is set."""
        suffix = self.suffix_for(album)
        if not suffix or album.name.endswith(suffix):
            return album.name
        # A name that carries the other kind's suffix gets it swapped, not
        # stacked.
        return f"{self.unmarked(album.name)} {suffix}"

    def suffix_for(self, album: Album) -> str:
        """The suffix this album should carry: fixed, updating, or the common one."""
        settings = self.config.settings
        specific = (settings.album_suffix_updating if album.schedule.automatic
                    else settings.album_suffix_fixed)
        return specific or settings.album_suffix

    def unmarked(self, name: str) -> str:
        """A name without any configured suffix or a trailing marker token."""
        settings = self.config.settings
        for suffix in (settings.album_suffix, settings.album_suffix_fixed,
                       settings.album_suffix_updating):
            if suffix and name.endswith(suffix) and len(name) > len(suffix):
                return name[:-len(suffix)].rstrip()
        return _without_marker(name)

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
            if self.unmarked(info.name) == album.name:
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
            plan.to_share = self._sharing(album, {}, plan)
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
        plan.current_shares = dict(info.shared_with)
        plan.to_share = self._sharing(album, plan.current_shares, plan)
        return plan

    def _sharing(self, album: Album, current: dict[str, str],
                 plan: Plan) -> list[tuple[str, str]]:
        """Who still needs access to this album, and in which role.

        Only additions and role changes: an account that already sees the album
        in the role the rule asks for costs nothing, and an account the rule no
        longer names keeps its access -- see `ImmichClient.share_album` for why
        the butler never takes access away.

        A name nobody answers to is a warning on this album, like an unknown
        person, because one mistyped account must not stop the album filling.
        """
        if not album.shares:
            return []
        try:
            accounts = self.users()
        except ImmichError as exc:
            plan.warnings.append(_share_problem(exc, album.share_with))
            return []

        grants: list[tuple[str, str]] = []
        for wanted in album.share_with:
            matches = [user for user in accounts if user.answers_to(wanted)]
            if not matches:
                plan.warnings.append(
                    f"not shared with {wanted!r}: no account of that name or "
                    f"address on this server")
                continue
            if len(matches) > 1:
                plan.warnings.append(
                    f"not shared with {wanted!r}: {len(matches)} accounts "
                    f"answer to it; use the e-mail address instead")
                continue
            user = matches[0]
            if current.get(user.id) != album.share_role:
                grants.append((user.id, album.share_role))
        return grants

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
        report = RunReport(slug=album.slug, name=album.name, dry_run=dry_run,
                           # Everything planning noticed belongs in the report
                           # too: the CLI prints the report's warnings, so a
                           # problem found while planning -- an unfindable
                           # cover, an account nobody answers to -- would
                           # otherwise only ever reach the log.
                           warnings=list(plan.warnings))
        if dry_run:
            report.added = len(plan.to_add)
            report.removed = len(plan.to_remove)
            report.created = plan.creates_album
            report.cover_set = bool(plan.cover_asset_id)
            report.renamed = bool(plan.rename_to)
            report.shared = len(plan.to_share)
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
        if plan.to_share and plan.album_id:
            report.shared = self._share(plan, report)

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

    def _share(self, plan: Plan, report: RunReport) -> int:
        """Grant the access the plan worked out. Returns how many accounts got it.

        Two calls, because Immich distinguishes them: somebody new is added to
        the album (`albumUser.create`), somebody already on it has their role
        changed (`albumUser.update`). A key without either permission means the
        album is filled and simply not shared -- a warning, never a failed run,
        for the same reason a missing `album.update` only costs the cover.
        """
        assert plan.album_id is not None
        fresh = [(user_id, role) for user_id, role in plan.to_share
                 if user_id not in plan.current_shares]
        changed = [(user_id, role) for user_id, role in plan.to_share
                   if user_id in plan.current_shares]

        shared = 0
        if fresh:
            try:
                self.client.share_album(plan.album_id, fresh)
                shared += len(fresh)
            except ImmichError as exc:
                report.warnings.append(
                    _share_problem(exc, [self._name_of(user_id)
                                         for user_id, _ in fresh]))
        for user_id, role in changed:
            try:
                self.client.set_album_user_role(plan.album_id, user_id, role)
                shared += 1
            except ImmichError as exc:
                report.warnings.append(
                    f"the role of {self._name_of(user_id)!r} was not changed "
                    f"to {role!r}: " + (
                        "the API key needs the 'albumUser.update' permission"
                        if exc.status == 403 else str(exc)) +
                    ". Everything else worked.")
        if shared:
            self._albums = None
        return shared

    # -- sharing albums that have no rule ----------------------------------

    def apply_shares(self, dry_run: bool = False) -> list[RunReport]:
        """Carry out the `[[shares]]` rules: one report per album touched.

        These reach albums the butler has no rule for -- the hand-made ones,
        which in most libraries are the majority. Nothing here creates, fills
        or renames an album: a share rule only ever hands out access to an
        album that already exists, so naming the wrong one costs a warning and
        no pictures move.
        """
        rules = self.config.settings.shares
        if not rules:
            return []

        reports: list[RunReport] = []
        for index, rule in enumerate(rules):
            report = RunReport(slug=f"shares[{index}]",
                               name=_share_rule_name(rule), dry_run=dry_run)
            try:
                self._apply_share_rule(rule, report, dry_run)
            except ImmichError as exc:
                report.error = str(exc)
            for warning in report.warnings:
                log.warning("%s: %s", report.name, warning)
            reports.append(report)
        return reports

    def _apply_share_rule(self, rule, report: RunReport, dry_run: bool) -> None:
        accounts = self._resolve_accounts(rule.accounts, rule.role, report)
        if not accounts:
            return
        targets = self._albums_for(rule, report)
        for info in targets:
            grants = [(user_id, role) for user_id, role in accounts
                      if info.shared_with.get(user_id) != role]
            if not grants:
                continue
            if dry_run:
                report.shared += len(grants)
                continue
            plan = Plan(album=_placeholder_album(info.name), album_id=info.id,
                        to_share=grants, current_shares=dict(info.shared_with))
            report.shared += self._share(plan, report)

    def _resolve_accounts(self, names, role: str,
                          report: RunReport) -> list[tuple[str, str]]:
        """Names to (user id, role), warning about each one that does not resolve."""
        try:
            accounts = self.users()
        except ImmichError as exc:
            report.warnings.append(_share_problem(exc, names))
            return []
        resolved: list[tuple[str, str]] = []
        for wanted in names:
            matches = [user for user in accounts if user.answers_to(wanted)]
            if len(matches) == 1:
                resolved.append((matches[0].id, role))
            elif not matches:
                report.warnings.append(
                    f"no account named {wanted!r} on this server")
            else:
                report.warnings.append(
                    f"{len(matches)} accounts answer to {wanted!r}; "
                    f"use the e-mail address instead")
        return resolved

    def _albums_for(self, rule, report: RunReport) -> list[AlbumInfo]:
        """The albums a share rule names, by the name they carry in Immich.

        A name matches with or without the marker suffix, so a rule can say
        "Italy 2019" whether or not the butler has since renamed it to
        "Italy 2019 [AB]".
        """
        existing = self.albums()
        if rule.every_album:
            mine = self._own_albums(existing, report)
            if mine is None:
                return []
            return mine
        found: list[AlbumInfo] = []
        for wanted in rule.albums:
            matches = [info for info in existing
                       if info.name == wanted
                       or self.unmarked(info.name) == wanted]
            if not matches:
                report.warnings.append(f"no album named {wanted!r} in Immich")
                continue
            found.extend(matches)
        return found

    def _own_albums(self, existing: list[AlbumInfo],
                    report: RunReport) -> list[AlbumInfo] | None:
        """Every album this account owns.

        The listing also returns albums other people shared *with* us, and only
        an owner can share an album on, so "*" has to mean "mine" -- otherwise
        one rule would produce a warning for every album somebody else owns.
        """
        try:
            me = self.client.me()
        except ImmichError as exc:
            report.warnings.append(
                f"cannot tell which albums are this account's own: {exc}")
            return None
        return [info for info in existing
                if info.owner_id is None or info.owner_id == me.id]

    def _name_of(self, user_id: str) -> str:
        """A readable name for an account id, for a message. Never fails."""
        for user in self._users or []:
            if user.id == user_id:
                return user.label()
        return user_id

    # -- one pass ----------------------------------------------------------

    def run_album(self, album: Album, dry_run: bool = False,
                  now: dt.datetime | None = None) -> RunReport:
        record = self.state.for_album(album.slug)
        try:
            plan = self.plan(album)
            report = self.apply(plan, dry_run=dry_run)
            for warning in report.warnings:
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


def _share_rule_name(rule) -> str:
    """A readable name for one [[shares]] entry, for the run summary."""
    what = "every album" if rule.every_album else ", ".join(rule.albums)
    return f"{what} -> {', '.join(rule.accounts)}"


def _placeholder_album(name: str):
    """A stand-in Album for an existing album that has no rule.

    `_share` reports against a Plan, and a Plan carries the rule it came from.
    A shared hand-made album has none, so this supplies just enough of one to
    name it in a message -- it is never matched, filled or written back.
    """
    from .config import Album, MatchRule
    from .schedule import parse as parse_schedule
    return Album(slug="", name=name, match=MatchRule(),
                 schedule=parse_schedule("manual"))


def _share_problem(exc: ImmichError, names) -> str:
    """One message for a failed share, naming the permission when that is why.

    Both the lookup and the grant can be refused, and they need different
    permissions, so the message names them together rather than guessing which
    of the two the key is missing.
    """
    who = ", ".join(repr(name) for name in names) or "the named accounts"
    if exc.status == 403:
        return (f"not shared with {who}: the API key needs the 'user.read' and "
                f"'albumUser.create' permissions. Everything else worked.")
    return f"not shared with {who}: {exc}"


_MARKER = re.compile(r"\s*(?:\[[^\[\]]{1,16}\]|[●◆↻])$")


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
    # The [[shares]] rules are about albums, not about any one rule, so they
    # run once after the albums -- and only for a whole pass, never when a
    # single album was asked for by name.
    if not only:
        reports.extend(butler.apply_shares(dry_run=dry_run))
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
    shares_checked: float | None = None
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
            # Share rules are not on any album's schedule -- they are about
            # albums the butler may never touch -- so they get their own slow
            # beat, and one immediately after a start so a config change does
            # not wait an hour to take effect.
            if (shares_checked is None
                    or time.monotonic() - shares_checked >= SHARE_SECONDS):
                butler.apply_shares()
                shares_checked = time.monotonic()
        except config_module.ConfigError as exc:
            log.error("configuration unusable: %s", exc)
        except Exception:                    # noqa: BLE001 - the daemon must survive
            log.exception("unexpected error during this pass; continuing")

        elapsed = time.monotonic() - started
        time.sleep(max(1.0, tick - elapsed))
