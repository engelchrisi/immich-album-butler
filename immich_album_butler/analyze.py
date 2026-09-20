"""What else might belong in this album.

A rule is always a little wrong at the edges. The photos from the taxi to the
airport fall an hour outside the window; the ones from the phone that never had
GPS are dropped by a place filter; a day trip across the border is a country
the rule never mentions.

Analyze looks for exactly those near-misses and, where it can, says what change
to the rule would take them in. It never edits anything itself: each suggestion
carries either an `adjust` -- the rule fields that would include those assets --
or nothing, when the group is only worth knowing about.

It works off the cached library scan rather than fresh queries, so it is cheap
enough to re-run on every keystroke in the builder. The one exception is the
"same people" group, which has to ask Immich.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from .config import MatchRule
from .immich import ImmichClient
from .matcher import MatchError, place_matches, resolve_people_via
from .trips import Point, haversine_km

CLOSE_HOURS = 12
NEARBY_KM = 25.0

# Enough to preview and to add in one go, without shipping a whole library
# through the browser.
MAX_IDS = 2000


@dataclass
class Suggestion:
    """One group of media that are not in the album but arguably could be."""

    key: str
    title: str
    detail: str
    asset_ids: list[str] = field(default_factory=list)
    # The rule fields that would bring this group in, or None when the group is
    # informational and no rule change expresses it.
    adjust: dict | None = None

    @property
    def count(self) -> int:
        return len(self.asset_ids)

    def to_json(self) -> dict:
        return {"key": self.key, "title": self.title, "detail": self.detail,
                "count": self.count, "asset_ids": self.asset_ids[:MAX_IDS],
                "adjust": self.adjust}


def analyze(points: Sequence[Point], matched_ids: Sequence[str], rule: MatchRule,
            *, close_hours: float = CLOSE_HOURS,
            nearby_km: float = NEARBY_KM) -> list[Suggestion]:
    """Near-misses for a rule, given the cached scan and what it matched."""
    matched = set(matched_ids)
    inside = [p for p in points if p.id in matched]
    if not inside:
        return []

    outside = [p for p in points if p.id not in matched]
    found = [
        _close_in_time(inside, outside, rule, close_hours),
        _window_unlocated(outside, rule),
        _window_other_place(outside, rule),
        _nearby_other_time(inside, outside, rule, nearby_km),
    ]
    return [s for s in found if s is not None and s.count]


# --------------------------------------------------------------------------
# the groups
# --------------------------------------------------------------------------

def _close_in_time(inside: Sequence[Point], outside: Sequence[Point],
                   rule: MatchRule, hours: float) -> Suggestion | None:
    """Assets shot within `hours` of something already in the album.

    The arrival and departure photos, usually: the same occasion, just past
    whichever boundary the rule drew.
    """
    if not rule.has_dates:
        return None          # nothing to widen: the rule is not about dates

    stamps = sorted(p.taken_at for p in inside)
    window = dt.timedelta(hours=hours)
    near = [p for p in outside if _nearest_gap(stamps, p.taken_at) <= window]
    if not near:
        return None

    # A rule's dates are whole days, so the widening that takes these in always
    # takes the rest of those two days as well. Report what the change actually
    # does rather than the tighter set that suggested it -- a count the builder
    # then contradicts is worse than no count.
    dates = [p.taken_at.date() for p in near] + [s.date() for s in stamps]
    widened = {"from": min(dates).isoformat(), "to": max(dates).isoformat()}
    first, last = min(dates), max(dates)

    detail = ("Taken just before or after the album's own media -- usually "
              "the journey there and back.")
    if rule.people:
        # Face data is not in the scan, so the people filter cannot be applied
        # here; the honest thing is to show the set we are sure of.
        hits = near
        detail += (" The rule also filters by people, so widening the dates may "
                   "bring in fewer than this.")
    else:
        hits = [p for p in outside
                if not _within_window(p, rule)
                and first <= p.taken_at.date() <= last
                and place_matches(p, rule)]
    if not hits:
        return None

    return Suggestion(
        key="close_in_time",
        title=f"Within {_hours(hours)} of the album",
        detail=detail,
        asset_ids=[p.id for p in hits],
        adjust=widened)


def _window_unlocated(outside: Sequence[Point], rule: MatchRule) -> Suggestion | None:
    """Assets inside the window that a place filter threw out for having no GPS."""
    if not rule.has_places or rule.include_unlocated:
        return None
    hits = [p for p in _in_window(outside, rule) if not p.located]
    if not hits:
        return None
    return Suggestion(
        key="window_unlocated",
        title="In the date range, but no GPS",
        detail=("The place filter drops these because the camera recorded no "
                "coordinates. Most holiday photos look like this."),
        asset_ids=[p.id for p in hits],
        adjust={"include_unlocated": True})


def _window_other_place(outside: Sequence[Point], rule: MatchRule) -> Suggestion | None:
    """Assets inside the window whose location the rule does not name."""
    if not rule.has_places:
        return None
    hits = [p for p in _in_window(outside, rule) if p.located]
    if not hits:
        return None

    countries = _ranked(p.country for p in hits)
    cities = _ranked(p.city for p in hits)
    where = ", ".join(countries[:3]) or ", ".join(cities[:3]) or "elsewhere"
    adjust = None
    if countries:
        adjust = {"countries": sorted({*rule.countries, *countries[:3]})}
    elif cities:
        adjust = {"cities": sorted({*rule.cities, *cities[:3]})}

    return Suggestion(
        key="window_other_place",
        title=f"In the date range, but in {where}",
        detail=("Inside the album's dates, at a place the rule does not list -- "
                "a day trip, or the airport on the way home."),
        asset_ids=[p.id for p in hits], adjust=adjust)


def _nearby_other_time(inside: Sequence[Point], outside: Sequence[Point],
                       rule: MatchRule, radius_km: float) -> Suggestion | None:
    """Assets from the same place at a different time. Informational only.

    Deliberately carries no `adjust`: widening the dates to reach them would
    drag in everything in between, which is never what the album wanted.
    """
    located = [p for p in inside if p.located]
    if not located:
        return None
    centre = (sum(p.latitude for p in located) / len(located),
              sum(p.longitude for p in located) / len(located))

    hits = [p for p in outside if p.located
            and not _within_window(p, rule)
            and haversine_km(p.latitude, p.longitude, *centre) <= radius_km]
    if not hits:
        return None

    years = sorted({p.taken_at.year for p in hits})
    return Suggestion(
        key="nearby_other_time",
        title=f"Same place, other dates ({_years(years)})",
        detail=(f"Within {radius_km:.0f} km of this album's centre but outside "
                f"its dates. Shown so you can tell whether the album should be "
                f"about the place instead of the trip."),
        asset_ids=[p.id for p in hits])


def people_elsewhere(client: ImmichClient, rule: MatchRule,
                     matched_ids: Sequence[str], *,
                     months: int = 3) -> Suggestion | None:
    """Assets of the album's people, just outside its date window.

    This one has to ask Immich: the scan cache holds no face data.
    """
    if not rule.people or not rule.has_dates:
        return None
    try:
        person_ids = resolve_people_via(client, list(rule.people))
    except MatchError:
        return None

    days = months * 31
    after = (rule.from_date - dt.timedelta(days=days)) if rule.from_date else None
    before = (rule.to_date + dt.timedelta(days=days)) if rule.to_date else None

    matched = set(matched_ids)
    found: dict[str, object] = {}
    for person_id in person_ids:
        for asset in client.search_metadata(taken_after=after, taken_before=before,
                                            person_ids=[person_id], with_exif=False):
            if asset.id not in matched:
                found[asset.id] = asset
    if not found:
        return None

    who = ", ".join(rule.people[:3])
    return Suggestion(
        key="same_people",
        title=f"{who} within {months} months of this album",
        detail=("The same people, outside the album's dates. Widen the range "
                "only if the album is about them rather than the trip."),
        asset_ids=list(found))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _in_window(points: Sequence[Point], rule: MatchRule) -> list[Point]:
    """Points the rule's dates accept but its places reject."""
    return [p for p in points
            if _within_window(p, rule) and not place_matches(p, rule)]


def _within_window(point: Point, rule: MatchRule) -> bool:
    day = point.taken_at.date()
    if rule.from_date and day < rule.from_date:
        return False
    if rule.to_date and day > rule.to_date:
        return False
    return True


def _nearest_gap(stamps: Sequence[dt.datetime], moment: dt.datetime) -> dt.timedelta:
    """How far `moment` is from the closest of an ordered list of times."""
    index = bisect_left(stamps, moment)
    gaps = []
    if index < len(stamps):
        gaps.append(stamps[index] - moment)
    if index:
        gaps.append(moment - stamps[index - 1])
    return min(gaps) if gaps else dt.timedelta.max


def _ranked(values, limit: int = 6) -> list[str]:
    counts = Counter(v for v in values if v)
    return [name for name, _ in counts.most_common(limit)]


def _hours(hours: float) -> str:
    if hours >= 24 and hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} day" + ("s" if days > 1 else "")
    return f"{hours:g} hours"


def _years(years: Sequence[int]) -> str:
    if len(years) == 1:
        return str(years[0])
    if len(years) <= 3:
        return ", ".join(str(y) for y in years)
    return f"{years[0]}-{years[-1]}"
