"""The play-hint line the butler keeps in an album's Immich description (N31).

A player needs to know how to play an album, and the butler is the only thing
that knows. It publishes that as one line in the album's Immich description --
any client can read it, it needs no shared filesystem, and it works if the
player runs on another host.

The line says two things and nothing else:

    [butler v1] chunk=3/year chunk_order=chronological

`chunk=<n>/<span>` -- n photos from one year, month or day, then on to the next;
`chunk_order=` -- whether the years/months/days follow each other in time order
or at random. An album without a `chunk` has no line at all and is shuffled.

The butler owns exactly one line: the last one matching `[butler vN]`. Every
other line of a hand-written description is kept verbatim.

Every value the line can carry is a **closed vocabulary**: the tables below
and the range `MIN_CHUNK`..`MAX_CHUNK` are the one place that decides what may
ever be written. `config.py` validates every album against them before it is
turned into a line, and `hint_line()` asserts its own output against them
again as a second line of defence -- this module never echoes a raw string
into a description. The full meaning is in `docs/hints.md`, the contract a
player such as PyImmichFrame implements against; that file is checked against
this registry by `tests/test_describe.py` so a value cannot be added here
without being documented there.
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

# chunk=<n>/<span> -- the time span a chunk is taken from.
CHUNK_SPANS = ("year", "month", "day")

# chunk_order=... -- how the chunks follow each other. "chronological" is the
# default: a chunk without a `chunk_order` means that.
CHUNK_ORDER_VALUES = ("chronological", "random")
DEFAULT_CHUNK_ORDER = "chronological"

# n photos per chunk. The range exists to catch typos, not to limit taste.
MIN_CHUNK, MAX_CHUNK = 1, 50

_CHUNK = re.compile(r"^(\d+)/([a-z]+)$")


def parse_chunk(value: str) -> tuple[int, str] | None:
    """`"3/year"` -> `(3, "year")`; anything outside the vocabulary -> None."""
    match = _CHUNK.match(value)
    if not match:
        return None
    count, span = int(match.group(1)), match.group(2)
    if span not in CHUNK_SPANS or not MIN_CHUNK <= count <= MAX_CHUNK:
        return None
    return count, span


def hint_line(album: Album) -> str:
    """The `[butler vN] chunk=… chunk_order=…` line for this album, or ""
    when the album has no `chunk` and so no line.

    config.py has already checked both values against this module's
    vocabulary; this function asserts it again, as a second line of defence
    against a value slipping in some other way (e.g. a future caller that
    builds an Album by hand).
    """
    if not album.chunk:
        return ""
    order = album.chunk_order or DEFAULT_CHUNK_ORDER
    assert parse_chunk(album.chunk) is not None, f"unknown chunk {album.chunk!r}"
    assert order in CHUNK_ORDER_VALUES, f"unknown chunk_order {order!r}"
    return f"[butler v{VERSION}] chunk={album.chunk} chunk_order={order}"


def apply_hint(description: str, line: str) -> str:
    """Return `description` with the butler's line set, replacing or adding it.

    The last marker line is replaced in place, or the line is appended after
    a blank line; an empty description becomes the hint alone. Replacing the
    line always substitutes the whole thing, so a key the butler no longer
    writes -- an old field, or one from a future version this build does not
    know -- never survives a rewrite; only a hand-written description around
    the marker line is kept.

    An empty `line` means the album has no hint: the marker line, if there is
    one, is taken out, together with the blank line that separated it from
    the hand-written text around it.
    """
    lines = description.split("\n")
    marker_at = _last_marker(lines)

    if not line:
        if marker_at is None:
            return description
        del lines[marker_at]
        if (0 < marker_at < len(lines) and not lines[marker_at].strip()
                and not lines[marker_at - 1].strip()):
            del lines[marker_at]
        text = "\n".join(lines)
        return text.rstrip("\n") if marker_at >= len(lines) else text

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
