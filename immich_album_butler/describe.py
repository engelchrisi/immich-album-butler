"""The hint line the butler keeps in an album's Immich description (N31).

The playback side needs to know what kind of album this is, and the butler is
the only thing that knows. It publishes that as one line in the album's Immich
description -- any client can read it, it needs no shared filesystem, and it
works if the frame runs on another host.

The butler owns exactly one line: the last one matching `[butler vN]`. Every
other line of a hand-written description is kept verbatim.
"""

from __future__ import annotations

import re

from .config import Album

# The format version. Adding a field is not a bump; changing a field's meaning
# is. A reader that does not know a version must ignore the line.
VERSION = 1

_MARKER = re.compile(r"^\[butler v\d+\]")


def hint_line(album: Album) -> str:
    """The `[butler vN] kind=… order=… [rotating=yes]` line for this album."""
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

    parts = [f"[butler v{VERSION}]", f"kind={kind}"]
    if order:
        parts.append(f"order={order}")
    if album.rotating:
        # Set whatever else the rule says: the contents change every run, so a
        # player walking them in order walks a different album each week.
        parts.append("rotating=yes")
    return " ".join(parts)


def apply_hint(description: str, line: str, enabled: bool) -> str:
    """Return `description` with the butler's line set, replaced or removed.

    `enabled` false removes the line (a clean uninstall). Otherwise the last
    marker line is replaced in place, or the line is appended after a blank
    line; an empty description becomes the hint alone.
    """
    lines = description.split("\n")
    marker_at = _last_marker(lines)

    if not enabled:
        if marker_at is None:
            return description
        del lines[marker_at]
        # Drop a blank separator left dangling before the removed line.
        if marker_at > 0 and not lines[marker_at - 1].strip() and (
                marker_at == len(lines) or marker_at - 1 == len(lines) - 1):
            del lines[marker_at - 1]
        return "\n".join(lines).rstrip("\n")

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
