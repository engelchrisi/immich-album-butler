"""Choosing an album's cover picture from the assets that matched.

The configuration holds no UUIDs, so a cover cannot be named by asset id. It is
named by a *rule* instead -- "the one with everyone in it", "the newest" -- or
by the original file name, which is the one handle on a picture a person can
read and type.

`"everyone"` is the reason this module exists. For an album built from a group
of people, the picture worth putting on the front is the one that shows the
whole group; the matcher already knows which assets each person appears in, so
this is counting, not another trip to the server.
"""

from __future__ import annotations

import datetime as dt

from .immich import Asset

AUTO = "auto"
EVERYONE = "everyone"
NEWEST = "newest"
OLDEST = "oldest"

RULES = (AUTO, EVERYONE, NEWEST, OLDEST)

_EPOCH = dt.datetime.min


class CoverError(ValueError):
    """The cover rule cannot be satisfied -- e.g. no such file name."""


def describe(spec: str) -> str:
    """One line of English for a cover value, for logs and the UI."""
    if spec == AUTO:
        return "whatever Immich picked"
    if spec == EVERYONE:
        return "the picture showing the most of the album's people"
    if spec == NEWEST:
        return "the newest picture in the album"
    if spec == OLDEST:
        return "the oldest picture in the album"
    return f"the picture named {spec!r}"


def choose(spec: str, assets: list[Asset],
           by_person: dict[str, set[str]] | None = None) -> str | None:
    """The asset id the cover should point at, or None to leave it alone.

    `assets` are the rule's matched assets, oldest first. `by_person` maps a
    person id to the assets they appear in, as the matcher recorded it.

    Videos are skipped: Immich will show a frame of one, but a still is a
    better front page, and an album is rarely all video.
    """
    if spec == AUTO or not assets:
        return None

    pictures = [a for a in assets if not a.is_video] or assets

    if spec == NEWEST:
        return _by_time(pictures, newest=True).id
    if spec == OLDEST:
        return _by_time(pictures, newest=False).id
    if spec == EVERYONE:
        return _everyone(pictures, by_person or {}).id
    return _named(pictures, spec).id


def _by_time(pictures: list[Asset], newest: bool) -> Asset:
    ordered = sorted(pictures, key=lambda a: (a.taken_at or _EPOCH, a.id))
    return ordered[-1] if newest else ordered[0]


def _everyone(pictures: list[Asset], by_person: dict[str, set[str]]) -> Asset:
    """The picture showing the most of the album's people, newest of those.

    With nobody to count -- a rule with no people, or a `people_mode = "all"`
    album where every asset shows everyone anyway -- this degrades to the
    newest picture rather than failing, because "everyone" is then trivially
    true of all of them.
    """
    if not by_person:
        raise CoverError(
            'cover = "everyone" needs the rule to name people; this album '
            'matches on dates or places only')

    best: Asset | None = None
    best_score: tuple[int, dt.datetime, str] | None = None
    for asset in pictures:
        count = sum(1 for ids in by_person.values() if asset.id in ids)
        if not count:
            continue
        score = (count, asset.taken_at or _EPOCH, asset.id)
        if best_score is None or score > best_score:
            best, best_score = asset, score
    if best is None:
        raise CoverError("no matched picture shows any of the album's people")
    return best


def _named(pictures: list[Asset], name: str) -> Asset:
    folded = name.casefold()
    hits = [a for a in pictures if a.file_name.casefold() == folded]
    if not hits:
        raise CoverError(
            f"no picture named {name!r} is in this album. A cover is either a "
            f"rule ({', '.join(RULES)}) or the original file name of one of "
            f"the album's own pictures.")
    # Several files can share a name across folders; the newest is the least
    # surprising choice, and the run says which one it picked.
    return _by_time(hits, newest=True)
