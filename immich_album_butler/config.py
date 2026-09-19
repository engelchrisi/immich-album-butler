"""Reading and writing the butler's configuration.

The configuration is the product of design mode and the input of runtime mode,
so it is optimised for being read by a human a year later: TOML, one file per
album, people by name, schedules in words. No UUID ever appears in it -- the
Immich album id lives in the state file instead, because an id is meaningless
to a reader and breaks if the album is recreated.

Layout of the config directory:

    config.toml          global settings
    groups.toml          named sets of people (optional)
    albums.d/*.toml      one album rule per file
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .schedule import Schedule, ScheduleError
from .schedule import parse as parse_schedule

DEFAULT_CONFIG_DIR = Path("/etc/immich-album-butler")
DEFAULT_STATE_DIR = Path("/var/lib/immich-album-butler")
DEFAULT_PORT = 8081

SYNC_MODES = ("add", "mirror")
PEOPLE_MODES = ("any", "all")


class ConfigError(ValueError):
    """Configuration that cannot be used, with a message naming the file."""


@dataclass(frozen=True)
class MatchRule:
    """Which assets belong in an album."""

    from_date: dt.date | None = None
    to_date: dt.date | None = None
    countries: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    people_mode: str = "any"
    include_unlocated: bool = True

    @property
    def has_places(self) -> bool:
        return bool(self.countries or self.states or self.cities)

    @property
    def has_dates(self) -> bool:
        return self.from_date is not None or self.to_date is not None

    @property
    def is_empty(self) -> bool:
        return not (self.has_places or self.has_dates or self.people)


@dataclass(frozen=True)
class Album:
    """One album rule, as read from one file in albums.d/."""

    slug: str
    name: str
    match: MatchRule
    schedule: Schedule
    schedule_inherited: bool = False
    enabled: bool = True
    sync: str = "add"
    path: Path | None = None

    @property
    def mirrors(self) -> bool:
        return self.sync == "mirror"


@dataclass(frozen=True)
class DesignUser:
    """One design-mode login. Holds a password *hash*, never a password."""

    name: str
    password_hash: str


@dataclass(frozen=True)
class Settings:
    """The global config.toml."""

    server: str
    schedule: Schedule
    timezone: str | None = None
    log_level: str = "info"
    design_idle_minutes: int = 30
    design_port: int = DEFAULT_PORT
    design_users: tuple[DesignUser, ...] = ()


@dataclass
class Config:
    """Everything loaded from the config directory."""

    settings: Settings
    albums: list[Album] = field(default_factory=list)
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Per-file problems. One broken album must not stop the others, so these
    # are collected rather than raised.
    errors: list[str] = field(default_factory=list)
    directory: Path | None = None

    def album(self, slug: str) -> Album | None:
        for album in self.albums:
            if album.slug == slug:
                return album
        return None

    def expand_people(self, names: tuple[str, ...]) -> tuple[list[str], list[str]]:
        """Resolve group names to their members. Returns (people, problems)."""
        people: list[str] = []
        problems: list[str] = []
        for name in names:
            if name in self.groups:
                for member in self.groups[name]:
                    if member not in people:
                        people.append(member)
            else:
                if name not in people:
                    people.append(name)
        return people, problems


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load(config_dir: Path) -> Config:
    """Load the whole config directory, tolerating broken album files."""
    config_dir = Path(config_dir)
    settings_path = config_dir / "config.toml"
    if not settings_path.exists():
        raise ConfigError(f"no config.toml in {config_dir}")

    settings = _load_settings(settings_path)
    groups, group_errors = _load_groups(config_dir / "groups.toml")

    albums: list[Album] = []
    errors: list[str] = list(group_errors)
    albums_dir = config_dir / "albums.d"
    for path in sorted(albums_dir.glob("*.toml")) if albums_dir.is_dir() else []:
        try:
            albums.append(_load_album(path, settings, groups))
        except (ConfigError, ScheduleError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")

    seen: dict[str, str] = {}
    for album in albums:
        if album.name in seen:
            errors.append(
                f"{album.slug}.toml: album name {album.name!r} is already used by "
                f"{seen[album.name]}.toml -- names must be unique")
        seen.setdefault(album.name, album.slug)

    return Config(settings=settings, albums=albums, groups=groups,
                  errors=errors, directory=config_dir)


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from None


def _load_settings(path: Path) -> Settings:
    data = _read_toml(path)
    server = data.get("server")
    if not server or not isinstance(server, str):
        raise ConfigError(f"{path.name}: 'server' is required, e.g. "
                          f'server = "http://immich.example.lan:2283"')
    server = server.rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise ConfigError(f"{path.name}: 'server' must start with http:// or https://")

    raw_schedule = data.get("auto-update-schedule", data.get("auto_update_schedule"))
    try:
        schedule = parse_schedule(raw_schedule) if raw_schedule else parse_schedule("manual")
    except ScheduleError as exc:
        raise ConfigError(f"{path.name}: auto-update-schedule: {exc}") from None

    idle = data.get("design_idle_minutes", 30)
    if not isinstance(idle, int) or idle < 0:
        raise ConfigError(f"{path.name}: design_idle_minutes must be a "
                          f"non-negative integer")
    port = data.get("design_port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError(f"{path.name}: design_port must be a port number")

    timezone = data.get("timezone")
    if timezone is not None and not isinstance(timezone, str):
        raise ConfigError(f"{path.name}: timezone must be a string, "
                          f'e.g. timezone = "Europe/Rome"')

    return Settings(server=server, schedule=schedule, timezone=timezone,
                    log_level=str(data.get("log_level", "info")).lower(),
                    design_idle_minutes=idle, design_port=port,
                    design_users=_load_users(data.get("design"), path.name))


def _load_users(design: object, filename: str) -> tuple[DesignUser, ...]:
    """Read `[[design.users]]`.

    The password is stored as a hash produced by `immich-album-butler passwd`.
    A plaintext `password` is refused rather than accepted quietly: design mode
    can show the photo library, so a config file must not be a password file.
    """
    if design is None:
        return ()
    if not isinstance(design, dict):
        raise ConfigError(f"{filename}: [design] must be a table")

    raw = design.get("users", [])
    if not isinstance(raw, list):
        raise ConfigError(f"{filename}: [[design.users]] must be a list of users")

    users: list[DesignUser] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"{filename}: each [[design.users]] entry is a table "
                              f"with 'name' and 'password'")
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise ConfigError(f"{filename}: a [[design.users]] entry has no 'name'")
        if name.casefold() in seen:
            raise ConfigError(f"{filename}: two design users are named {name!r}")
        seen.add(name.casefold())

        secret = entry.get("password")
        if not secret or not isinstance(secret, str):
            raise ConfigError(f"{filename}: user {name!r} has no 'password'. Run "
                              f"'immich-album-butler passwd {name}' to make one.")
        if not secret.startswith("scrypt$"):
            raise ConfigError(
                f"{filename}: the password for {name!r} is not a hash. Design mode "
                f"never stores plaintext -- run 'immich-album-butler passwd {name}' "
                f"and paste the line it prints.")
        users.append(DesignUser(name=name, password_hash=secret))
    return tuple(users)


def _load_groups(path: Path) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    if not path.exists():
        return {}, []
    data = _read_toml(path)
    groups: dict[str, tuple[str, ...]] = {}
    errors: list[str] = []
    for name, body in data.items():
        if not isinstance(body, dict):
            errors.append(f"groups.toml: [{name}] must be a table with 'members'")
            continue
        members = body.get("members", [])
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            errors.append(f"groups.toml: [{name}] members must be a list of names")
            continue
        if not members:
            errors.append(f"groups.toml: [{name}] has no members")
            continue
        deduped = tuple(dict.fromkeys(members))
        if name in deduped:
            errors.append(f"groups.toml: [{name}] lists itself as a member")
            continue
        groups[name] = deduped
    return groups, errors


def _load_album(path: Path, settings: Settings,
                groups: dict[str, tuple[str, ...]]) -> Album:
    data = _read_toml(path)
    slug = path.stem

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ConfigError("'name' is required (the album name shown in Immich)")

    sync = str(data.get("sync", "add")).lower()
    if sync not in SYNC_MODES:
        raise ConfigError(f"sync must be one of {', '.join(SYNC_MODES)}, got {sync!r}")

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("enabled must be true or false")

    raw_schedule = data.get("auto-update-schedule", data.get("auto_update_schedule"))
    if raw_schedule is None:
        schedule, inherited = settings.schedule, True
    else:
        try:
            schedule, inherited = parse_schedule(raw_schedule), False
        except ScheduleError as exc:
            raise ConfigError(f"auto-update-schedule: {exc}") from None

    match = _load_match(data.get("match", {}), groups)

    return Album(slug=slug, name=name, match=match, schedule=schedule,
                 schedule_inherited=inherited, enabled=enabled, sync=sync, path=path)


def _load_match(data: object, groups: dict[str, tuple[str, ...]]) -> MatchRule:
    if not isinstance(data, dict):
        raise ConfigError("[match] must be a table")

    from_date = _as_date(data.get("from"), "from")
    to_date = _as_date(data.get("to"), "to")
    if from_date and to_date and from_date > to_date:
        raise ConfigError(f"[match] from ({from_date}) is after to ({to_date})")

    people = _as_names(data.get("people"), "people")
    for name in people:
        if name in groups and name in {m for members in groups.values() for m in members}:
            raise ConfigError(f"[match] {name!r} is both a group and a group member; "
                              f"rename one of them")

    mode = str(data.get("people_mode", "any")).lower()
    if mode not in PEOPLE_MODES:
        raise ConfigError(f"[match] people_mode must be one of "
                          f"{', '.join(PEOPLE_MODES)}, got {mode!r}")

    include_unlocated = data.get("include_unlocated", True)
    if not isinstance(include_unlocated, bool):
        raise ConfigError("[match] include_unlocated must be true or false")

    rule = MatchRule(
        from_date=from_date, to_date=to_date,
        countries=_as_names(data.get("countries"), "countries"),
        states=_as_names(data.get("states"), "states"),
        cities=_as_names(data.get("cities"), "cities"),
        people=people, people_mode=mode, include_unlocated=include_unlocated)

    if rule.is_empty:
        raise ConfigError("[match] is empty -- that would match the whole library. "
                          "Give at least a date range, a place or a person.")
    return rule


def _as_date(value: object, key: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            raise ConfigError(f"[match] {key} is not a date: {value!r} "
                              f"(use 2019-07-01)") from None
    raise ConfigError(f"[match] {key} must be a date, got {type(value).__name__}")


def _as_names(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"[match] {key} must be a list of strings")
    cleaned = [v.strip() for v in value if v.strip()]
    return tuple(dict.fromkeys(cleaned))


# --------------------------------------------------------------------------
# writing (design mode is the only writer)
# --------------------------------------------------------------------------

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")

HEADER = ("# Written by immich-album-butler design mode. Values may be edited by\n"
          "# hand; comments are not preserved when design mode saves again.\n")


def dump_album(album: Album) -> str:
    """Render one album rule as TOML, in a fixed, readable order."""
    lines = [HEADER, f"name    = {_toml_str(album.name)}\n"]
    if not album.enabled:
        lines.append("enabled = false\n")
    if album.sync != "add":
        lines.append(f'sync    = "{album.sync}"   '
                     f"# also removes assets from this album when they stop matching\n")
    if album.schedule_inherited:
        lines.append(f"# auto-update-schedule = \"{album.schedule}\"   "
                     f"# inherited from config.toml\n")
    else:
        lines.append(f"auto-update-schedule = {_toml_str(str(album.schedule))}\n")

    match = album.match
    lines.append("\n[match]\n")
    if match.from_date:
        lines.append(f"from = {match.from_date.isoformat()}\n")
    if match.to_date:
        lines.append(f"to   = {match.to_date.isoformat()}\n")
    for key, values in (("countries", match.countries), ("states", match.states),
                        ("cities", match.cities)):
        if values:
            lines.append(f"{key} = {_toml_list(values)}\n")
    if match.people:
        lines.append(f"people = {_toml_list(match.people)}"
                     f"   # person names and/or group names\n")
        if match.people_mode != "any":
            lines.append(f'people_mode = "{match.people_mode}"'
                         f"   # every one of them must appear\n")
    if match.has_places or match.has_dates:
        lines.append(f"include_unlocated = {str(match.include_unlocated).lower()}"
                     f"   # photos in the window that carry no GPS\n")
    return "".join(lines)


def dump_groups(groups: dict[str, tuple[str, ...]]) -> str:
    lines = [HEADER]
    for name in sorted(groups):
        lines.append(f"\n[{_toml_key(name)}]\n")
        lines.append(f"members = {_toml_list(groups[name])}\n")
    return "".join(lines)


def write_album(directory: Path, album: Album) -> Path:
    """Write albums.d/<slug>.toml atomically."""
    albums_dir = Path(directory) / "albums.d"
    albums_dir.mkdir(parents=True, exist_ok=True)
    path = albums_dir / f"{album.slug}.toml"
    _write_atomic(path, dump_album(album))
    return path


def write_groups(directory: Path, groups: dict[str, tuple[str, ...]]) -> Path:
    path = Path(directory) / "groups.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, dump_groups(groups))
    return path


def _write_atomic(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def slugify(name: str) -> str:
    """A file-name-safe slug for an album name, never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "album"


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_key(value: str) -> str:
    return value if _BARE_KEY.match(value) else _toml_str(value)


def _toml_list(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"
