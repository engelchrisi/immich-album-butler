"""Turning an album rule into a set of assets.

Two things make this more than one API call:

* **Places are an OR, and the API only does AND.** `include_unlocated` means
  "this place, *or* no place at all", because most photos carry no GPS and
  would otherwise fall out of a holiday album. So places are filtered here,
  on assets the server narrowed down by date and person.

* **`people_mode` must not depend on how the server reads `personIds`.**
  Rather than guess whether the API ANDs or ORs that list, each person is
  queried separately and the results are combined here: union for `any`,
  intersection for `all`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from .config import MatchRule
from .immich import Asset, ImmichClient, Person

log = logging.getLogger(__name__)


class Located(Protocol):
    """The location fields `place_matches` needs. Asset and Point both fit."""

    located: bool
    city: str | None
    state: str | None
    country: str | None


class MatchError(RuntimeError):
    """The rule cannot be evaluated -- e.g. it names a person Immich has lost."""


@dataclass
class MatchResult:
    assets: list[Asset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Which of the rule's people each matched asset shows, as person id ->
    # asset ids. Only filled when the rule names people, and only because
    # choosing a cover that shows everyone needs it; matching itself does not.
    by_person: dict[str, set[str]] = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return [a.id for a in self.assets]


def resolve_people(names: list[str], people: list[Person]) -> list[str]:
    """Map display names to person ids, case-insensitively.

    Raises MatchError naming the problem, because an album that silently drops
    a person would quietly produce a wrong album.
    """
    by_name: dict[str, list[Person]] = {}
    for person in people:
        by_name.setdefault(person.name.casefold(), []).append(person)

    ids: list[str] = []
    for name in names:
        matches = by_name.get(name.casefold(), [])
        ids.append(_one(name, matches, {p.name for p in people}))
    return ids


def resolve_people_via(client: ImmichClient, names: list[str]) -> list[str]:
    """Resolve names by asking Immich for each one.

    A library has one person per face cluster -- tens of thousands of them,
    nearly all unnamed -- so asking by name beats fetching the list.
    """
    return [_one(name, client.find_people(name)) for name in names]


def _one(name: str, matches: list[Person], known: set[str] | None = None) -> str:
    if not matches:
        hint = ""
        if known:
            hint = f" (known names include: {', '.join(sorted(known)[:8])})"
        raise MatchError(f"no person named {name!r} in Immich{hint}")
    if len(matches) > 1:
        raise MatchError(f"{len(matches)} people in Immich are named {name!r}; "
                         f"rename them so the album is unambiguous")
    return matches[0].id


def place_matches(asset: "Located", rule: MatchRule) -> bool:
    """Whether one asset satisfies the rule's place criteria.

    Takes anything carrying the four location fields, so analyze mode can reuse
    it on the smaller `trips.Point` without converting back to an Asset.
    """
    if not rule.has_places:
        return True
    if not asset.located:
        # No GPS: included only if the rule says unlocated photos belong.
        return rule.include_unlocated
    return (_matches_any(asset.country, rule.countries)
            or _matches_any(asset.state, rule.states)
            or _matches_any(asset.city, rule.cities))


def _matches_any(value: str | None, wanted: tuple[str, ...]) -> bool:
    if not wanted or value is None:
        return False
    folded = value.casefold()
    return any(w.casefold() == folded for w in wanted)


def match(client: ImmichClient, rule: MatchRule,
          people: list[Person] | None = None) -> MatchResult:
    """Evaluate a rule against the library."""
    result = MatchResult()

    person_ids: list[str] = []
    if rule.people:
        person_ids = (resolve_people(list(rule.people), people) if people
                      else resolve_people_via(client, list(rule.people)))

    by_person: dict[str, set[str]] = {}
    if rule.people_mode == "all" and len(person_ids) > 1:
        assets = _assets_for_all(client, rule, person_ids, by_person)
    elif person_ids:
        assets = _assets_for_any(client, rule, person_ids, by_person)
    else:
        assets = _fetch(client, rule, None)

    kept = [a for a in assets.values() if place_matches(a, rule)]
    dropped = len(assets) - len(kept)
    if dropped and rule.has_places:
        log.debug("%d asset(s) fell outside the rule's places", dropped)

    unlocated = sum(1 for a in kept if not a.located)
    if unlocated and rule.has_places and rule.include_unlocated:
        result.warnings.append(
            f"{unlocated} of {len(kept)} matching assets carry no GPS and were "
            f"included because include_unlocated is true")

    result.assets = sorted(kept, key=lambda a: (a.taken_at or _EPOCH, a.id))
    survivors = {a.id for a in kept}
    result.by_person = {person: ids & survivors for person, ids in by_person.items()}
    return result


def _assets_for_any(client: ImmichClient, rule: MatchRule, person_ids: list[str],
                    by_person: dict[str, set[str]]) -> dict[str, Asset]:
    found: dict[str, Asset] = {}
    for person_id in person_ids:
        batch = _fetch(client, rule, [person_id])
        by_person[person_id] = set(batch)
        found.update(batch)
    return found


def _assets_for_all(client: ImmichClient, rule: MatchRule, person_ids: list[str],
                    by_person: dict[str, set[str]]) -> dict[str, Asset]:
    common: dict[str, Asset] | None = None
    for person_id in person_ids:
        batch = _fetch(client, rule, [person_id])
        by_person[person_id] = set(batch)
        if common is None:
            common = batch
        else:
            common = {k: v for k, v in common.items() if k in batch}
        if not common:
            break
    return common or {}


def _fetch(client: ImmichClient, rule: MatchRule,
           person_ids: list[str] | None) -> dict[str, Asset]:
    """One server-side query: dates, person, and a place only when it is safe.

    A single country can be pushed to the server, but only when unlocated
    photos are *not* wanted -- otherwise the server's AND would throw away
    exactly the photos the rule asks to keep.
    """
    country = None
    if (not rule.include_unlocated and len(rule.countries) == 1
            and not rule.states and not rule.cities):
        country = rule.countries[0]

    stream = client.search_metadata(
        taken_after=rule.from_date, taken_before=rule.to_date,
        person_ids=person_ids, country=country)
    return {asset.id: asset for asset in stream}


import datetime as _dt  # noqa: E402 - only for the sort sentinel below

_EPOCH = _dt.datetime.min
