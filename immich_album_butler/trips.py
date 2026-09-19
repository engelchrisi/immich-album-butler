"""Finding the trips hiding in a photo library.

A holiday looks like this in the data: for a couple of weeks, the photos stop
being near home. That is the whole heuristic -- a run of geotagged photos far
from home, without a long gap in the middle, big enough to be worth an album.

Two details matter in practice:

* Most photos carry no GPS at all. They are pulled in by the trip's **date
  window** rather than by coordinates, which is why a detected trip reports its
  located and unlocated counts separately.

* The scan is cached. Paging the whole library takes a while, and design mode
  should feel instant on the second visit.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .immich import Asset, ImmichClient

log = logging.getLogger(__name__)

SCAN_FILE = "scan.json"
SCAN_VERSION = 1

# Defaults chosen to find holidays, not day trips.
AWAY_KM = 100.0
MAX_GAP_DAYS = 3.0
MIN_DAYS = 2
MIN_ASSETS = 30
HOME_CELL = 0.1                 # degrees; ~11 km of latitude


@dataclass(frozen=True)
class Point:
    """The few fields trip detection needs, small enough to cache by the 100k."""

    id: str
    taken_at: dt.datetime
    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    is_video: bool = False

    @property
    def located(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @classmethod
    def from_asset(cls, asset: Asset) -> "Point | None":
        if asset.taken_at is None:
            return None
        return cls(id=asset.id, taken_at=asset.taken_at,
                   latitude=asset.latitude, longitude=asset.longitude,
                   city=asset.city, state=asset.state, country=asset.country,
                   is_video=asset.is_video)

    def to_json(self) -> list:
        return [self.id, self.taken_at.isoformat(), self.latitude, self.longitude,
                self.city, self.state, self.country, self.is_video]

    @classmethod
    def from_json(cls, row: Sequence) -> "Point":
        return cls(id=row[0], taken_at=dt.datetime.fromisoformat(row[1]),
                   latitude=row[2], longitude=row[3], city=row[4], state=row[5],
                   country=row[6], is_video=bool(row[7]))


@dataclass
class Trip:
    start: dt.date
    end: dt.date
    located: int = 0
    unlocated: int = 0
    countries: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def total(self) -> int:
        return self.located + self.unlocated

    def suggested_name(self) -> str:
        """Readable and specific: the place people would call it, plus the year."""
        year = str(self.start.year)
        if self.end.year != self.start.year:
            year = f"{self.start.year}/{self.end.year}"
        if self.countries:
            where = ", ".join(self.countries[:2])
            detail = self.states[:2] or self.cities[:2]
            if len(self.countries) == 1 and detail:
                where = f"{self.countries[0]} – {', '.join(detail)}"
        elif self.cities:
            where = ", ".join(self.cities[:2])
        else:
            where = "Trip"
        return f"{where} {year}"


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------

def scan(client: ImmichClient, since: dt.date | None = None) -> list[Point]:
    """Page the whole library down to the fields trip detection needs."""
    points: list[Point] = []
    for asset in client.search_metadata(taken_after=since):
        point = Point.from_asset(asset)
        if point is not None:
            points.append(point)
    points.sort(key=lambda p: (p.taken_at, p.id))
    log.info("scanned %d dated assets, %d with coordinates",
             len(points), sum(1 for p in points if p.located))
    return points


def save_scan(directory: Path, points: Sequence[Point]) -> Path:
    path = Path(directory) / SCAN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": SCAN_VERSION,
               "scanned_at": dt.datetime.now().isoformat(timespec="seconds"),
               "points": [p.to_json() for p in points]}
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    temp.replace(path)
    return path


def load_scan(directory: Path) -> tuple[list[Point], dt.datetime | None]:
    path = Path(directory) / SCAN_FILE
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("ignoring unreadable scan cache: %s", exc)
        return [], None
    if data.get("version") != SCAN_VERSION:
        return [], None
    points = [Point.from_json(row) for row in data.get("points", [])]
    when = None
    if stamp := data.get("scanned_at"):
        try:
            when = dt.datetime.fromisoformat(stamp)
        except ValueError:
            when = None
    return points, when


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def find_home(points: Iterable[Point], cell: float = HOME_CELL) -> tuple[float, float] | None:
    """The densest cluster of located photos: where the camera usually is."""
    cells: Counter = Counter()
    for point in points:
        if point.located:
            cells[(round(point.latitude / cell), round(point.longitude / cell))] += 1
    if not cells:
        return None
    (lat_cell, lon_cell), _ = cells.most_common(1)[0]
    members = [p for p in points if p.located
               and round(p.latitude / cell) == lat_cell
               and round(p.longitude / cell) == lon_cell]
    return (sum(p.latitude for p in members) / len(members),
            sum(p.longitude for p in members) / len(members))


def detect(points: Sequence[Point], *, home: tuple[float, float] | None = None,
           away_km: float = AWAY_KM, max_gap_days: float = MAX_GAP_DAYS,
           min_days: int = MIN_DAYS, min_assets: int = MIN_ASSETS,
           include_unlocated: bool = True) -> list[Trip]:
    """Detect trips: runs of photos taken far from home, close together in time."""
    ordered = sorted(points, key=lambda p: (p.taken_at, p.id))
    if home is None:
        home = find_home(ordered)
    if home is None:
        return []

    away = [p for p in ordered
            if p.located and haversine_km(p.latitude, p.longitude, *home) >= away_km]
    if not away:
        return []

    gap = dt.timedelta(days=max_gap_days)
    runs: list[list[Point]] = [[away[0]]]
    for point in away[1:]:
        if point.taken_at - runs[-1][-1].taken_at <= gap:
            runs[-1].append(point)
        else:
            runs.append([point])

    trips: list[Trip] = []
    for run in runs:
        trip = _build(run, ordered, include_unlocated)
        if trip.days >= min_days and trip.total >= min_assets:
            trips.append(trip)
    trips.sort(key=lambda t: t.start)
    return trips


def _build(run: Sequence[Point], everything: Sequence[Point],
           include_unlocated: bool) -> Trip:
    start = run[0].taken_at.date()
    end = run[-1].taken_at.date()

    ids = [p.id for p in run]
    unlocated = 0
    if include_unlocated:
        # Photos with no GPS taken inside the window belong to the same trip:
        # they are usually the majority of a holiday's pictures.
        for point in everything:
            if point.located:
                continue
            if start <= point.taken_at.date() <= end:
                ids.append(point.id)
                unlocated += 1

    return Trip(start=start, end=end, located=len(run), unlocated=unlocated,
                countries=_ranked(p.country for p in run),
                states=_ranked(p.state for p in run),
                cities=_ranked(p.city for p in run),
                asset_ids=ids)


def _ranked(values: Iterable[str | None], limit: int = 6) -> list[str]:
    counts = Counter(v for v in values if v)
    return [name for name, _ in counts.most_common(limit)]
