#!/usr/bin/env python3
"""Migrate a config.toml from the pre-1.0 hint keys to chunk / chunk_order.

    python scripts/migrate-hints.py /etc/immich-album-butler/config.toml
    python scripts/migrate-hints.py /etc/immich-album-butler/config.toml --write

Without --write it only prints what it would change. With --write it keeps the
old file next to the new one as config.toml.bak. Running it again changes
nothing.

Per album it drops hint_kind, hint_order, slot, dwell, active, caption and
activity, and -- where the album has no chunk yet -- adds one guessed from the
shape of the rule (docs/hints.md):

    recurring window (on_from)   3/year  chronological   a birthday
    date window (from / to)      3/day   random          a trip
    people                       5/year  random
    places only                  5/year  random
    nothing                      no chunk: the player shuffles it

The rule shape cannot tell a birthday from Christmas, or a trip from a year in
review. Those albums are listed so you can set 5/year random by hand.

The file is edited as text, so comments and layout survive.
"""

import argparse
import re
import shutil
import sys
import tomllib
from pathlib import Path

REMOVED_KEYS = ("hint_kind", "hint_order", "slot", "dwell", "active",
                "caption", "activity")

HEADER = re.compile(r"^\s*(\[\[?)\s*(.+?)\s*\]\]?\s*(#.*)?$")
LONG_WINDOW_DAYS = 31


def guess(match: dict) -> tuple[str, str] | None:
    """The (chunk, chunk_order) for a rule, or None to leave the album shuffled."""
    if match.get("on_from"):
        return "3/year", "chronological"
    if match.get("from") or match.get("to"):
        return "3/day", "random"
    if match.get("people"):
        return "5/year", "random"
    if any(match.get(key) for key in ("countries", "states", "cities")):
        return "5/year", "random"
    return None


def _days(match: dict) -> int | None:
    first, last = match.get("from"), match.get("to")
    if not (first and last and hasattr(first, "toordinal")
            and hasattr(last, "toordinal")):
        return None
    return last.toordinal() - first.toordinal() + 1


def _ambiguous(match: dict) -> str:
    """Why a guess may be wrong, or "" when the rule shape is conclusive."""
    if match.get("on_from"):
        return "recurring window: a birthday, or Christmas / Summer / Anniversary?"
    days = _days(match)
    if days is not None and days > LONG_WINDOW_DAYS:
        return f"date window of {days} days: a trip, or a year in review?"
    return ""


def _album_slug(header: str) -> str | None:
    """The slug of an `albums.<slug>` table header, None for any other table."""
    try:
        table = tomllib.loads(f"[{header}]")
    except tomllib.TOMLDecodeError:
        return None
    albums = table.get("albums")
    if not isinstance(albums, dict) or len(albums) != 1:
        return None
    (slug, rest), = albums.items()
    return slug if rest == {} else None


def migrate(text: str) -> tuple[str, list[str], list[str]]:
    """Return (new text, what changed, albums to look at by hand)."""
    albums = tomllib.loads(text).get("albums", {})
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    changes: list[str] = []
    by_hand: list[str] = []

    slug: str | None = None
    last_key = -1          # index in `out` of the album table's last key line
    seen: set[str] = set()

    def close_table() -> None:
        """Add the chunk keys at the end of the album table being left."""
        nonlocal last_key
        if slug is None or last_key < 0:
            return
        album = albums.get(slug, {})
        if "chunk" in album:
            return
        chunk = guess(album.get("match", {}))
        if chunk is None:
            return
        new = [f'chunk = "{chunk[0]}"{eol}', f'chunk_order = "{chunk[1]}"{eol}']
        out[last_key + 1:last_key + 1] = new
        changes.append(f'{slug}: add chunk = "{chunk[0]}", '
                       f'chunk_order = "{chunk[1]}"')
        reason = _ambiguous(album.get("match", {}))
        if reason:
            by_hand.append(f"{slug}: {reason}")

    for line in lines:
        header = HEADER.match(line)
        if header:
            close_table()
            slug = _album_slug(header.group(2)) if header.group(1) == "[" else None
            last_key = -1
            if slug is not None:
                seen.add(slug)
            out.append(line)
            continue
        if slug is not None:
            key = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line)
            if key and key.group(1) in REMOVED_KEYS:
                changes.append(f"{slug}: drop {line.strip()}")
                continue
            if key:
                last_key = len(out)
        out.append(line)
    close_table()

    for name in albums:
        if name not in seen:      # defined in an inline or dotted form: not edited
            by_hand.append(f"{name}: not a [albums.{name}] table, edit by hand")
    return "".join(out), changes, by_hand


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--write", action="store_true",
                        help="write the result, keeping CONFIG.bak")
    args = parser.parse_args(argv)

    with open(args.config, encoding="utf-8", newline="") as f:
        text = f.read()
    try:
        new, changes, by_hand = migrate(text)
    except tomllib.TOMLDecodeError as err:
        print(f"{args.config}: not valid TOML: {err}", file=sys.stderr)
        return 1

    for change in changes:
        print(change)
    if not changes:
        print("nothing to change")
    if by_hand:
        print("\nLook at these by hand (5/year random fits Christmas, Summer, "
              "Anniversary and Year in review):")
        for item in by_hand:
            print(f"  {item}")

    if not args.write:
        if changes:
            print("\ndry run: nothing written, add --write to apply")
        return 0
    if new == text:
        return 0
    backup = args.config.with_name(args.config.name + ".bak")
    shutil.copy2(args.config, backup)
    with open(args.config, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    print(f"\nwritten; previous file kept as {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
