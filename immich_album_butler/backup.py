"""Backup and restore of albums: metadata only, never a photo.

A backup is one JSON file holding, for every album the account owns, enough to
build it again -- name, description, cover, who it is shared with and, per
asset, the id, checksum, original path and a few EXIF fields -- plus the
butler's own albums, kept as the raw text of config.toml (so comments survive)
together with the slug -> Immich album id map from state.json.

Restore is additive. A missing album is created; an existing one only gains the
assets it lacks. Nothing is removed, nothing is deleted (N22), and config.toml is
replaced only when asked for, after saving the current one as config.toml.bak.

The file holds the login hashes from config.toml: it is written 0600 and is as
secret as config.toml itself.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_module
from .config import CONFIG_NAME, ConfigError, _write_atomic
from .immich import ImmichClient, ImmichError
from .state import State

log = logging.getLogger(__name__)

BACKUP_VERSION = 1
BACKUP_DIR = "backups"
PREFIX = "backup-"

# The EXIF fields worth keeping: enough to recognise a photo and to find it
# again by place and camera, without carrying the whole maker-note.
EXIF_KEYS = ("make", "model", "dateTimeOriginal", "latitude", "longitude",
             "city", "state", "country", "rating")


class BackupError(RuntimeError):
    """A backup cannot be made, read or restored."""


def backups_dir(state_dir: Path, override: Path | None = None) -> Path:
    return Path(override) if override else Path(state_dir) / BACKUP_DIR


# -- creating ---------------------------------------------------------------

def create_backup(client: ImmichClient, config_dir: Path, state_dir: Path,
                  directory: Path | None = None,
                  now: dt.datetime | None = None) -> Path:
    """Write a new backup file and return its path."""
    now = now or dt.datetime.now(dt.timezone.utc)
    data = {
        "version": BACKUP_VERSION,
        "created": now.isoformat(timespec="seconds"),
        "server": client.base_url,
        "immich_albums": _snapshot_albums(client),
        "butler": _snapshot_butler(config_dir, state_dir),
    }
    target = backups_dir(state_dir, directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{PREFIX}{now.strftime('%Y%m%d-%H%M%S')}.json"
    _write_private(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return path


def _snapshot_albums(client: ImmichClient) -> list[dict]:
    try:
        me = client.me().id
    except ImmichError:
        me = None                       # no user.read: keep every album listed
    try:
        users = {u.id: u for u in client.users()}
    except ImmichError:
        users = {}

    found = []
    for album in sorted(client.albums(), key=lambda a: a.name):
        if me and album.owner_id and album.owner_id != me:
            continue                    # shared *with* us: not ours to rebuild
        assets = [_snapshot_asset(item) for item in client.search_metadata_raw(
            album_ids=[album.id], with_exif=True)]
        found.append({
            "id": album.id,
            "name": album.name,
            "description": album.description,
            "cover_asset_id": album.cover_asset_id,
            "shared_with": [
                {"user_id": uid, "role": role,
                 "name": users[uid].name if uid in users else "",
                 "email": users[uid].email if uid in users else ""}
                for uid, role in sorted(album.shared_with.items())],
            "assets": assets,
        })
    return found


def _snapshot_asset(item: dict) -> dict:
    exif = item.get("exifInfo") or {}
    return {
        "id": item.get("id"),
        "checksum": item.get("checksum"),
        "file_name": item.get("originalFileName"),
        "path": item.get("originalPath"),
        "type": item.get("type"),
        "taken_at": item.get("localDateTime") or item.get("fileCreatedAt"),
        "favorite": bool(item.get("isFavorite")),
        "exif": {k: exif.get(k) for k in EXIF_KEYS if exif.get(k) is not None},
    }


def _snapshot_butler(config_dir: Path, state_dir: Path) -> dict:
    path = Path(config_dir) / CONFIG_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = None
    state = State.load(state_dir)
    return {"config_toml": text,
            "state_albums": {slug: s.album_id for slug, s in
                             sorted(state.albums.items()) if s.album_id}}


def _write_private(path: Path, text: str) -> None:
    temp = path.with_suffix(".json.tmp")
    temp.write_text(text, encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass                            # e.g. a filesystem without modes
    temp.replace(path)


# -- reading ----------------------------------------------------------------

@dataclass(frozen=True)
class BackupInfo:
    name: str
    created: str
    albums: int
    assets: int
    size: int


def list_backups(directory: Path) -> list[BackupInfo]:
    """Backups in `directory`, newest first. Unreadable files are skipped."""
    found = []
    for path in sorted(Path(directory).glob(f"{PREFIX}*.json"), reverse=True):
        try:
            data = load_backup(path)
        except BackupError as exc:
            log.warning("skipping %s: %s", path.name, exc)
            continue
        albums = data["immich_albums"]
        found.append(BackupInfo(
            name=path.name, created=str(data.get("created") or ""),
            albums=len(albums), assets=sum(len(a["assets"]) for a in albums),
            size=path.stat().st_size))
    return found


def resolve_backup(directory: Path, ref: str) -> Path:
    """`latest`, a file name inside `directory`, or a path."""
    directory = Path(directory)
    if ref == "latest":
        files = sorted(directory.glob(f"{PREFIX}*.json"))
        if not files:
            raise BackupError(f"no backups in {directory}")
        return files[-1]
    candidate = Path(ref)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate
    # A bare name must stay inside the backup directory.
    return directory / candidate.name


def load_backup(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise BackupError(f"cannot read {path}: {exc.strerror or exc}") from None
    except json.JSONDecodeError as exc:
        raise BackupError(f"{Path(path).name} is not valid JSON: {exc}") from None
    if not isinstance(data, dict) or data.get("version") != BACKUP_VERSION \
            or not isinstance(data.get("immich_albums"), list):
        raise BackupError(f"{Path(path).name} is not a version-{BACKUP_VERSION} "
                          f"album backup")
    return data


# -- restoring --------------------------------------------------------------

@dataclass
class AlbumRestore:
    name: str
    action: str                         # "create", "extend" or "skip"
    to_add: int = 0
    added: int = 0
    remapped: int = 0                   # found again by checksum
    unmatched: list[str] = field(default_factory=list)   # file names
    warnings: list[str] = field(default_factory=list)
    new_id: str | None = None


@dataclass
class RestoreReport:
    dry_run: bool
    albums: list[AlbumRestore] = field(default_factory=list)
    config_restored: bool = False
    config_backup: str | None = None
    warnings: list[str] = field(default_factory=list)


def restore(client: ImmichClient, data: dict, config_dir: Path, state_dir: Path,
            *, dry_run: bool = False, restore_config: bool = False,
            only: str | None = None) -> RestoreReport:
    """Rebuild albums from a loaded backup. Never removes or deletes anything."""
    report = RestoreReport(dry_run=dry_run)
    existing = {a.name: a for a in client.albums()}
    try:
        users = client.users()
    except ImmichError:
        users = []
    id_map: dict[str, str] = {}         # backed-up album id -> id now

    for entry in data["immich_albums"]:
        if only and entry["name"] != only:
            continue
        current = existing.get(entry["name"])
        if current is None:
            outcome = AlbumRestore(entry["name"], "create",
                                   to_add=len(entry["assets"]))
        else:
            have = client.album_asset_ids(current.id)
            missing = [a for a in entry["assets"] if a["id"] not in have]
            outcome = AlbumRestore(entry["name"], "extend" if missing else "skip",
                                   to_add=len(missing))
            id_map[entry["id"]] = current.id
        report.albums.append(outcome)
        if dry_run or outcome.action == "skip":
            continue
        try:
            _apply_album(client, entry, current, outcome, users, id_map)
        except ImmichError as exc:
            outcome.warnings.append(f"failed: {exc}")

    if not dry_run:
        _remap_state(state_dir, data, id_map)
    if restore_config:
        _restore_config(report, data, config_dir, dry_run)
    return report


def _apply_album(client: ImmichClient, entry: dict, current, outcome: AlbumRestore,
                 users: list, id_map: dict[str, str]) -> None:
    created = current is None
    if created:
        album_id = client.create_album(entry["name"], entry.get("description") or "").id
        outcome.new_id = album_id
        id_map[entry["id"]] = album_id
        wanted = list(entry["assets"])
    else:
        album_id = current.id
        have = client.album_asset_ids(album_id)
        wanted = [a for a in entry["assets"] if a["id"] not in have]

    ids = [a["id"] for a in wanted]
    added, refused = client.add_assets_detailed(album_id, ids)
    outcome.added = len(added)

    # An id Immich no longer knows may still be the same file under a new id
    # (the library was re-imported): the checksum finds it.
    by_id = {a["id"]: a for a in wanted}
    renamed: dict[str, str] = {}        # old asset id -> new asset id
    for old_id in refused:
        asset = by_id.get(old_id) or {}
        new_id = _find_by_checksum(client, asset.get("checksum"))
        if new_id and new_id != old_id:
            renamed[old_id] = new_id
        else:
            outcome.unmatched.append(str(asset.get("file_name") or old_id))
    if renamed:
        again, still = client.add_assets_detailed(album_id, list(renamed.values()))
        outcome.added += len(again)
        outcome.remapped = len(again)
        back = {new: old for old, new in renamed.items()}
        for new_id in still:
            old = by_id.get(back.get(new_id, ""), {})
            outcome.unmatched.append(str(old.get("file_name") or new_id))

    if created:
        _restore_extras(client, entry, album_id, renamed, outcome, users)


def _find_by_checksum(client: ImmichClient, checksum: str | None) -> str | None:
    if not checksum:
        return None
    for item in client.search_metadata_raw(checksum=checksum, with_exif=False,
                                           page_size=1):
        return item.get("id")
    return None


def _restore_extras(client: ImmichClient, entry: dict, album_id: str,
                    renamed: dict[str, str], outcome: AlbumRestore,
                    users: list) -> None:
    """Cover and sharing, only for an album this restore created.

    Both need permissions the other album writes do not (`album.update`,
    `albumUser.create`); a key without them costs a warning, not the restore.
    """
    cover = entry.get("cover_asset_id")
    if cover:
        try:
            client.set_album_cover(album_id, renamed.get(cover, cover))
        except ImmichError as exc:
            outcome.warnings.append(f"cover not restored: {exc}")

    grants = []
    for share in entry.get("shared_with") or []:
        user = _find_user(users, share)
        if user is None:
            who = share.get("email") or share.get("name") or share.get("user_id")
            outcome.warnings.append(f"not shared with {who}: no such account")
        elif share.get("role") in ("viewer", "editor"):
            grants.append((user, share["role"]))
    if grants:
        try:
            client.share_album(album_id, grants)
        except ImmichError as exc:
            outcome.warnings.append(f"sharing not restored: {exc}")


def _find_user(users: list, share: dict) -> str | None:
    for user in users:
        if user.id == share.get("user_id"):
            return user.id
    for key in ("email", "name"):
        if share.get(key):
            for user in users:
                if user.answers_to(share[key]):
                    return user.id
    return None


def _remap_state(state_dir: Path, data: dict, id_map: dict[str, str]) -> None:
    """Point state.json at the albums as they are now.

    A recreated album has a new id; without this the butler would look the
    album up by name -- which works, but only until somebody renames it.
    """
    old_ids = (data.get("butler") or {}).get("state_albums") or {}
    changed = False
    state = State.load(state_dir)
    for slug, old in old_ids.items():
        new = id_map.get(old)
        if new and new != old:
            state.for_album(slug).album_id = new
            changed = True
    if changed:
        state.save()


def _restore_config(report: RestoreReport, data: dict, config_dir: Path,
                    dry_run: bool) -> None:
    text = (data.get("butler") or {}).get("config_toml")
    if not text:
        report.warnings.append("the backup holds no config.toml")
        return
    # Parse it first: restoring a file the butler cannot load would turn a
    # working install into a broken one.
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / CONFIG_NAME).write_text(text, encoding="utf-8")
        try:
            config_module.load(Path(scratch))
        except ConfigError as exc:
            report.warnings.append(f"config.toml not restored: {exc}")
            return
    if dry_run:
        report.config_restored = True
        return
    path = Path(config_dir) / CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        saved = path.with_suffix(".toml.bak")
        _write_atomic(saved, path.read_text(encoding="utf-8"))
        try:
            saved.chmod(path.stat().st_mode & 0o7777)   # it holds the hashes too
        except OSError:
            pass
        report.config_backup = saved.name
    _write_atomic(path, text)
    report.config_restored = True
