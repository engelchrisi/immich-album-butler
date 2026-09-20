"""Command line: `run` for runtime mode, `design` for the UI, `trips` to look.

The API key is read from the environment, never from a flag, so it cannot end
up in shell history or in another user's `ps` output.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import config as config_module
from . import trips as trips_module
from .config import DEFAULT_CONFIG_DIR, DEFAULT_STATE_DIR, ConfigError
from .immich import ImmichClient, ImmichError
from .runtime import run_forever, run_once
from .state import State

KEY_ENV = "IMMICH_KEY"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="immich-album-butler",
        description="Keep Immich albums in order: by people, places and dates.")
    parser.add_argument("--config-dir", type=Path,
                        default=Path(os.environ.get("BUTLER_CONFIG_DIR",
                                                    DEFAULT_CONFIG_DIR)),
                        help="directory holding config.toml")
    parser.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("BUTLER_STATE_DIR",
                                                    DEFAULT_STATE_DIR)),
                        help="directory for state.json and the scan cache")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log at debug level")

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="update albums (daemon, or once)")
    run.add_argument("album", nargs="?", help="only this album (its file name "
                                              "without .toml)")
    run.add_argument("--once", action="store_true",
                     help="one pass over every album, then exit")
    run.add_argument("--dry-run", action="store_true",
                     help="report what would change, change nothing")

    design = sub.add_parser("design", help="web UI for building album rules")
    design.add_argument("--port", type=int, default=None)
    design.add_argument("--host", default="127.0.0.1")

    trips = sub.add_parser("trips", help="list the trips detected in the library")
    trips.add_argument("--rescan", action="store_true",
                       help="re-read the library instead of using the cache")
    trips.add_argument("--away-km", type=float, default=trips_module.AWAY_KM)
    trips.add_argument("--min-assets", type=int, default=trips_module.MIN_ASSETS)
    trips.add_argument("--min-days", type=int, default=trips_module.MIN_DAYS)

    passwd = sub.add_parser("passwd", help="make a design-mode login for config.toml")
    passwd.add_argument("name", help="the user name to sign in with")

    sub.add_parser("check", help="validate the configuration and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s", stream=sys.stderr)

    try:
        if args.command == "check":
            return _check(args)
        if args.command == "run":
            return _run(args)
        if args.command == "trips":
            return _trips(args)
        if args.command == "design":
            return _design(args)
        if args.command == "passwd":
            return _passwd(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except ImmichError as exc:
        print(f"Immich: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    return 1


def _client(config) -> ImmichClient:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ImmichError(f"no API key: set {KEY_ENV} in the environment "
                          f"(systemd: EnvironmentFile)")
    return ImmichClient(config.settings.server, key)


def _check(args) -> int:
    config = config_module.load(args.config_dir)
    print(f"server   {config.settings.server}")
    print(f"schedule {config.settings.schedule} (default)")
    print(f"groups   {len(config.groups)}")
    for album in config.albums:
        mark = " " if album.enabled else "-"
        inherited = " (inherited)" if album.schedule_inherited else ""
        shared = (f"  shared with {', '.join(album.share_with)}"
                  if album.shares else "")
        print(f" {mark} {album.slug:<28} {album.schedule}{inherited}"
              f"  sync={album.sync}{shared}")
    for rule in config.settings.shares:
        what = "every album" if rule.every_album else ", ".join(rule.albums)
        print(f"   share  {what} -> {', '.join(rule.accounts)} "
              f"as {rule.role}")
    for problem in config.errors:
        print(f"   ! {problem}", file=sys.stderr)
    return 1 if config.errors else 0


def _run(args) -> int:
    if not args.once and not args.album:
        config = config_module.load(args.config_dir)
        run_forever(_client(config), args.config_dir, args.state_dir)
        return 0

    config = config_module.load(args.config_dir)
    for problem in config.errors:
        logging.error("config: %s", problem)
    state = State.load(args.state_dir)
    reports = run_once(_client(config), config, state,
                       only=args.album, dry_run=args.dry_run)

    prefix = "would " if args.dry_run else ""
    failed = 0
    for report in reports:
        if report.error:
            failed += 1
            print(f"  !  {report.name}: {report.error}")
        elif report.created:
            print(f"  +  {prefix}create {report.name!r} "
                  f"with {report.added} media")
        else:
            # An album can change without gaining or losing a single asset --
            # a rename or a new cover -- so "up to date" is only for an album
            # where nothing at all happened.
            done = []
            if report.added or report.removed:
                done.append(f"add {report.added}, remove {report.removed}")
            if report.renamed:
                done.append("rename")
            if report.cover_set:
                done.append("set the cover")
            if report.shared:
                done.append(f"share with {report.shared}")
            if done:
                print(f"  ~  {report.name}: {prefix}" + ", ".join(done))
            elif report.warnings:
                # Something was asked for and did not happen -- a missing
                # permission, an account nobody answers to. The warnings below
                # say what; "up to date" would claim the opposite.
                print(f"  ~  {report.name}: nothing changed")
            else:
                print(f"  =  {report.name}: up to date")
        for warning in report.warnings:
            print(f"     {warning}")
    return 1 if failed else 0


def _trips(args) -> int:
    config = config_module.load(args.config_dir)
    points, scanned_at = trips_module.load_scan(args.state_dir)
    if args.rescan or not points:
        points = trips_module.scan(_client(config))
        trips_module.save_scan(args.state_dir, points)
    else:
        print(f"using the scan from {scanned_at} "
              f"({len(points)} assets); --rescan to refresh", file=sys.stderr)

    found = trips_module.detect(points, away_km=args.away_km,
                                min_assets=args.min_assets, min_days=args.min_days)
    if not found:
        print("no trips found; try a smaller --away-km or --min-assets")
        return 0
    for trip in found:
        print(f"{trip.start} .. {trip.end}  {trip.days:>3}d  "
              f"{trip.total:>5} assets ({trip.located} located)  "
              f"{trip.suggested_name()}")
    return 0


def _design(args) -> int:
    from .design.api import DesignApi
    from .design.server import serve

    config = config_module.load(args.config_dir)
    for problem in config.errors:
        logging.error("config: %s", problem)

    api = DesignApi(_client(config), args.config_dir, args.state_dir)
    serve(api, host=args.host, port=args.port or config.settings.design_port,
          users=list(config.settings.design_users),
          idle_minutes=config.settings.design_idle_minutes)
    return 0


def _passwd(args) -> int:
    """Print a `[[design.users]]` block to paste into config.toml.

    Printed rather than written: the config file is the administrator's, and a
    command that edits it behind their back is a command they cannot review.
    The password is read from a prompt, never a flag, so it stays out of shell
    history and out of other users' `ps`.
    """
    from getpass import getpass

    from .design.auth import AuthError, hash_password

    if not sys.stdin.isatty():
        print("passwd needs a terminal to prompt for the password",
              file=sys.stderr)
        return 2

    first = getpass(f"Password for {args.name}: ")
    if first != getpass("Repeat: "):
        print("the two passwords differ", file=sys.stderr)
        return 1
    try:
        hashed = hash_password(first)
    except AuthError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    print("\n# Add this to config.toml. It holds a hash, not the password.")
    print("[[design.users]]")
    print(f'name     = "{args.name}"')
    print(f'password = "{hashed}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
