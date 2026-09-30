"""The hint line the butler keeps in an album's Immich description (N31).

The playback side needs to know what kind of album this is and how to play
it, and the butler is the only thing that knows. It publishes that as one
line in the album's Immich description -- any client can read it, it needs
no shared filesystem, and it works if the frame runs on another host.

The butler owns exactly one line: the last one matching `[butler vN]`. Every
other line of a hand-written description is kept verbatim.

Every value the line can carry is a **closed vocabulary**: the tables below
(`KIND_VALUES`, `ORDER_VALUES`, `CAPTION_VALUES`, `ACTIVITY_VALUES`) and the
numeric ranges (`MIN_DWELL`..`MAX_DWELL`, `MIN_SLOT`..`MAX_SLOT`) are the one
place that decides what may ever be written. `config.py` validates every
album against these same tables before it is ever turned into a line, and
`hint_line()` asserts its own output against them again as a second line of
defence -- this module never echoes a raw string into a description. The
full meaning of every field is documented in `docs/hints.md`, the contract
a player such as PyImmichFrame implements against; that file is checked
against this registry by `tests/test_describe.py` so a value cannot be added
here without being documented there.
"""

from __future__ import annotations

import re

from .config import Album

# The format version. Adding a field is not a bump; changing a field's meaning
# is. A reader that does not know a version must ignore the line. Adding a
# field is also not a break for a reader that knows the version: an unknown
# key in a line of a known version must be ignored too, not treated as an
# error -- see docs/hints.md.
VERSION = 1

_MARKER = re.compile(r"^\[butler v\d+\]")

# kind=... -- either derived from the rule shape or set by `hint_kind`.
KIND_VALUES = ("trip", "recurring-day", "person", "place", "album")

# order=... -- either derived alongside `kind`, or set by `hint_order`.
ORDER_VALUES = ("trip", "one-per-year", "person",
                "time-asc", "time-desc", "random", "slots")

# caption=... -- what the frame should overlay. "none" is the default and is
# never written (its absence means the same thing).
CAPTION_VALUES = ("none", "year", "place", "title")

# activity=... -- a moving-photo effect. "none" is the default and is never
# written.
ACTIVITY_VALUES = ("none", "kenburns", "face-zoom", "map-fly")

# dwell=... -- whole seconds per photo. The range exists only to catch typos
# (a value of 36000 is surely a mistake, not an intentional ten-hour dwell).
MIN_DWELL, MAX_DWELL = 1, 3600

# slot=N or slot=N-M -- how many time-neighbours an order="slots" jump shows.
MIN_SLOT, MAX_SLOT = 1, 10


def hint_line(album: Album) -> str:
    """The `[butler vN] kind=… order=… …` line for this album.

    `kind`/`order` are derived from the rule shape, then `album.hint_kind` /
    `album.hint_order` override them if set -- config.py has already checked
    both against `KIND_VALUES`/`ORDER_VALUES`, so this function only asserts
    it, as a second line of defence against a value slipping in some other
    way (e.g. a future caller that builds an Album by hand).
    """
    rule = album.match
    order: str | None = None
    if rule.has_dates:
        kind, order = "trip", "trip"
    elif rule.has_recurring:
        kind, order = "recurring-day", "one-per-year"
    elif rule.people:
        kind, order = "person", "person"
    elif rule.has_places:
        kind = "place"
    else:
        kind = "album"

    if album.hint_kind:
        kind = album.hint_kind
    if album.hint_order:
        order = album.hint_order

    assert kind in KIND_VALUES, f"unknown kind {kind!r}"
    assert order is None or order in ORDER_VALUES, f"unknown order {order!r}"

    parts = [f"[butler v{VERSION}]", f"kind={kind}"]
    if order:
        parts.append(f"order={order}")
    if album.rotating:
        # Set whatever else the rule says: the contents change every run, so a
        # player walking them in order walks a different album each week.
        parts.append("rotating=yes")
    if album.slot:
        assert re.match(r"^\d+(-\d+)?$", album.slot), f"unknown slot {album.slot!r}"
        parts.append(f"slot={album.slot}")
    if album.dwell is not None:
        assert MIN_DWELL <= album.dwell <= MAX_DWELL, f"dwell out of range {album.dwell!r}"
        parts.append(f"dwell={album.dwell}")
    if album.active:
        assert re.match(r"^\d{2}-\d{2}\.\.\d{2}-\d{2}$", album.active), \
            f"unknown active {album.active!r}"
        parts.append(f"active={album.active}")
    if album.caption:
        assert album.caption in CAPTION_VALUES, f"unknown caption {album.caption!r}"
        parts.append(f"caption={album.caption}")
    if album.activity:
        assert album.activity in ACTIVITY_VALUES, f"unknown activity {album.activity!r}"
        parts.append(f"activity={album.activity}")
    return " ".join(parts)


def apply_hint(description: str, line: str) -> str:
    """Return `description` with the butler's line set, replacing or adding it.

    The last marker line is replaced in place, or the line is appended after
    a blank line; an empty description becomes the hint alone. Replacing the
    line always substitutes the whole thing, so a key the butler no longer
    writes -- an old field, or one from a future version this build does not
    know -- never survives a rewrite; only a hand-written description around
    the marker line is kept.
    """
    lines = description.split("\n")
    marker_at = _last_marker(lines)

    if marker_at is not None:
        lines[marker_at] = line
        return "\n".join(lines)
    if not description.strip():
        return line
    return description.rstrip("\n") + "\n\n" + line


def _last_marker(lines: list[str]) -> int | None:
    for index in range(len(lines) - 1, -1, -1):
        if _MARKER.match(lines[index]):
            return index
    return None
