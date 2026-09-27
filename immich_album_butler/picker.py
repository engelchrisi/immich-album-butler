"""Capping and rotating a matched set, per calendar year.

A second stage between the matcher and the plan (N29). The matcher decides
*which* assets qualify; the picker decides *how many* of each calendar year the
album keeps, and which ones.

Pure and HTTP-free on purpose: it takes plain lists and dicts and a seeded
`random.Random`, so `Butler.plan()` -- which is read-only and shared with the
design preview -- can show exactly what a real run would do without touching
Immich or the clock. The rotation bag is state, but this module only computes
the next bag; `runtime.apply()` is the only thing that writes it.
"""

from __future__ import annotations

import datetime as dt
import random

from .immich import Asset

# The `pick` modes, mirrored in config.PICK_MODES.
ALL = "all"
ROTATE = "rotate"
RANDOM = "random"
BEST = "best"

_EPOCH = dt.datetime.min


def year_of(asset: Asset) -> str:
    """The calendar year an asset is bucketed under; "unknown" without a date."""
    return str(asset.taken_at.year) if asset.taken_at else "unknown"


def pick(mode: str, pics_per_year: int | None, matched: list[Asset],
         bag: dict[str, tuple[str, ...]], last: dict[str, tuple[str, ...]],
         rng: random.Random,
         ) -> tuple[list[Asset], dict[str, tuple[str, ...]],
                    dict[str, tuple[str, ...]]]:
    """Cap `matched` to `pics_per_year` per year, per `mode`.

    Returns the chosen assets (in capture-time order) and the next `bag` and
    `last`, both keyed by year. For every mode but `rotate` the two dicts come
    back empty: only `rotate` carries state between runs.
    """
    if mode == ALL or not pics_per_year:
        return list(matched), {}, {}

    by_year: dict[str, list[Asset]] = {}
    for asset in matched:
        by_year.setdefault(year_of(asset), []).append(asset)

    chosen: list[Asset] = []
    new_bag: dict[str, tuple[str, ...]] = {}
    new_last: dict[str, tuple[str, ...]] = {}

    for year in sorted(by_year):
        assets = by_year[year]
        if len(assets) <= pics_per_year:
            # A year with few enough photos keeps them all and rotates nothing.
            chosen.extend(assets)
            continue
        if mode == BEST:
            chosen.extend(_best(assets, pics_per_year))
        elif mode == RANDOM:
            chosen.extend(_sample(assets, pics_per_year, rng))
        elif mode == ROTATE:
            picked, remaining = _rotate(assets, pics_per_year,
                                        bag.get(year, ()), last.get(year, ()), rng)
            chosen.extend(picked)
            new_bag[year] = remaining
            new_last[year] = tuple(a.id for a in picked)
        else:
            raise ValueError(f"unknown pick mode {mode!r}")

    chosen.sort(key=lambda a: (a.taken_at or _EPOCH, a.id))
    return chosen, new_bag, new_last


def _best(assets: list[Asset], n: int) -> list[Asset]:
    """Favourites first, then rating high to low, then oldest first."""
    ordered = sorted(assets, key=lambda a: (
        0 if a.is_favorite else 1, -(a.rating or 0),
        a.taken_at or _EPOCH, a.id))
    return ordered[:n]


def _sample(assets: list[Asset], n: int, rng: random.Random) -> list[Asset]:
    """A fresh sample, no memory; the seed makes a dry run reproduce the run."""
    pool = sorted(assets, key=lambda a: a.id)      # deterministic before shuffle
    rng.shuffle(pool)
    return pool[:n]


def _rotate(assets: list[Asset], n: int, bag: tuple[str, ...],
            last: tuple[str, ...], rng: random.Random,
            ) -> tuple[list[Asset], tuple[str, ...]]:
    """Draw `n` from the bag, refilling from `assets` when it runs dry.

    Ids that no longer match drop out of the bag; new ones join at the next
    refill, so the cycle carries on rather than restarting. The refill avoids
    the previous round's picks (`last`) at the seam, so two runs in a row do
    not repeat the same photos.
    """
    by_id = {a.id: a for a in assets}
    queue = [i for i in bag if i in by_id]          # surviving bag
    picked: list[Asset] = []
    taken: set[str] = set()
    while len(picked) < n:
        if not queue:
            queue = _refill(list(by_id), last, rng)
            queue = [i for i in queue if i not in taken]
        asset_id = queue.pop(0)
        picked.append(by_id[asset_id])
        taken.add(asset_id)
    return picked, tuple(queue)


def _refill(ids: list[str], last: tuple[str, ...], rng: random.Random) -> list[str]:
    """A shuffled fresh pool, with the previous round's picks pushed to the end."""
    ids = sorted(ids)                               # deterministic before shuffle
    rng.shuffle(ids)
    recent = set(last)
    ids.sort(key=lambda i: i in recent)             # stable: unused ones first
    return ids
