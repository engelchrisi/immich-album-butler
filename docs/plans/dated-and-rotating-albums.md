# Recurring-date rules and rotating samples

Design for N28–N31. Read [requirements.md](../requirements.md) and [design.md](../design.md)
first.

Companion plan on the playback side:
`PyImmichFrame/docs/plans/playback-order.md`. The boundary is in §1.

---

## 1. Why, and why here

The motivating album: **every photo taken on my birthday, any year, and a fresh handful of them
each week.** Today `MatchRule` has one contiguous `from`/`to` window (`config.py:64-107`,
`matcher._fetch()` at `matcher.py:179-195` pushes exactly `takenAfter`/`takenBefore`), so
"17 May of every year" cannot be expressed at all, and neither can "five of each year, different ones
next week".

**Why the butler and not the frame.** Membership is a query over the library, so it belongs in an
album: the result is visible in every Immich client, survives the frame being reinstalled, is
playable by any rule the frame already has, and needs no new concept on the playback side. The
frame keeps what only it can do — the order photos appear in, and the cursor through them. Two
things that were drafted for the frame are dropped because this plan covers them: a
`day-of-year` bucket and anchoring an order to today's date.

Two separable features, useful apart:

- **N28 a recurring-date rule** — a calendar day (or a window around it) in every year.
- **N29 a per-year cap with a rotating sample** — keep `pics_per_year` assets from each calendar year,
  and swap in ones not used recently on each run. Independent of N28: it is equally the answer to
  "five a year from a whole life's worth of one person".

## 2. Config

```toml
[albums.birthday]
name = "Geburtstag"
sync = "mirror"                     # required for rotation -- see §4
auto-update-schedule = "weekly sunday 04:00"
pics_per_year = 5                        # N29: keep at most this many per calendar year
pick = "rotate"                     # all (default) | rotate | random | best
cover = "newest"

[albums.birthday.match]
on = "05-17"                        # N28: this calendar day, every year
offset_days = 0                     # +/- days around it; 1 = 16th to 18th
since_year = 2005                   # earliest year to look in
```

`on` is `MM-DD`. `29-02` is accepted and simply finds nothing in most years; that is honest and
needs no leap-year rule.

Placement, and why: `on`/`offset_days`/`since_year` go in `[match]`, because they decide **which
assets qualify**. `pics_per_year`/`pick` go on the **album**, because they decide how many of the
qualifying assets of each year the album holds — a second stage, reusable with every existing rule. Keeping
them apart is what makes N29 useful on any rule: the chunk is the calendar year of the capture date,
so "five a year of the children" needs no `on` at all.

| `pick` | keeps |
|---|---|
| `all` | everything the rule matched — today's behaviour, the default |
| `rotate` | `pics_per_year` assets per year, preferring ones not used in recent runs (§4) |
| `random` | `pics_per_year` assets per year drawn fresh each run, no memory — repeats between weeks are possible |
| `best` | the `pics_per_year` highest-rated of each year, favourites first — stable between runs |

`config.py` changes:

- `MatchRule` (`config.py:64`) gains `on: str = ""`, `offset_days: int = 0`, `since_year: int | None = None`;
  `has_dates` stays about `from`/`to`, and a new `has_recurring` covers `on`.
- **`MatchRule.is_empty` (`config.py:106`) must count `on`**, or the loader rejects the rule as
  "that would match the whole library" (`config.py:569`).
- `Album` (`config.py:110`) gains `pics_per_year: int | None = None`, `pick: str = "all"`, with
  `PICK_MODES = ("all", "rotate", "random", "best")` beside `SYNC_MODES` (`config.py:56`).
- `_load_match()` (`config.py:533`) and `_load_album()` (`config.py:442`) parse and validate;
  `dump_album()` (`config.py:708-731`) writes them back, or design mode silently drops them on the
  next save.

Refused at load, each naming the reason:

- `on` that is not `MM-DD`, or a month/day out of range;
- `on` together with `from`/`to` — one window or a recurring day, not both. (`since_year` is the
  recurring rule's own lower bound.)
- `offset_days` negative, or wide enough to cover the year (`> 182`);
- `pick = "rotate"` with `sync = "add"` — nothing would ever be removed, so the album would grow
  every week and "rotate" would be a lie. This is the important one.
- `pick = "rotate"`, `"random"` or `"best"` without `pics_per_year`, and `pics_per_year` below 1;
- `pick = "rotate"` with `auto-update-schedule = "manual"` — a rotation nobody runs.
- `pick = "random"` with `sync = "add"` — same reason as `rotate`: nothing would be removed.

### Birthday albums, one per person

Each person gets one album: their photos on their own birthday, five per year, a new five every
Sunday. `since_year` is the birth year (nothing earlier can exist), `people` narrows every yearly
window to that person, `offset_days = 1` catches the party held the day after.

```toml
[albums.alex-birthday]
name = "Alex birthday"
sync = "mirror"
auto-update-schedule = "weekly sunday 04:00"
pics_per_year = 5
pick = "rotate"
[albums.alex-birthday.match]
people = ["Alex"]
on = "06-21"
offset_days = 1
since_year = 1985
```

Four albums are four such blocks (fictional names above; the real people and dates go only in the
live config on CT113, never in a tracked file). A year in which the person has five photos or fewer
that day keeps them all and has nothing to rotate.

## 3. Matching a recurring day

`matcher.match()` (`matcher.py:116`) grows one branch. A recurring day cannot be one
`takenAfter`/`takenBefore` pair, so it becomes **one windowed query per year**, from `since_year`
to the current year in the configured `timezone`:

```
for year in range(since_year, today.year + 1):
    day = date(year, month, dom)          # skipped if it does not exist
    fetch(takenAfter = day - offset_days at 00:00:00,
          takenBefore = day + offset_days at 23:59:59)
```

Twenty-odd paged searches once a week is nothing, and it keeps the filtering server-side. The
alternative — fetch the whole library and filter client-side, the way `countries`/`cities` are
OR'd in `matcher.py` — would page tens of thousands of assets to find a handful. Rejected, and worth a
line in design.md §6.

`since_year` is required rather than derived: asking Immich for the oldest asset is an extra
concept (and `ImmichClient.search_metadata` at `immich.py:288` does not offer an ascending order),
while a year in the config is one line and self-documenting. Default when omitted: ten years back,
logged once so a missing year is visible rather than silent.

Other `[match]` keys compose as they do now — `people`, `countries`, `cities` all narrow each
per-year window, so "the children, on my birthday, every year" is one rule.

## 4. The rotating sample

A second stage between the matcher and the plan, in a new module `pyimmichframe`-free
`immich_album_butler/picker.py`:

```python
def pick(mode: str, pics_per_year: int, matched: list[Asset],
         bag: dict[str, tuple[str, ...]], last: dict[str, tuple[str, ...]],
         rng: random.Random) -> tuple[list[Asset], dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]
```

`rotate` is a bag, the same shape as the frame's album-of-the-day cycle and for the same reason:
a plain random sample repeats itself, so over a year you would see the same photos of 17 May again
and again and never the others.

- group `matched` by the year of its capture date; each year has its own bag and is drawn on its
  own, so a year with 3 photos never starves a year with 300, and a year with `pics_per_year` or fewer
  photos simply keeps them all;
- draw `pics_per_year` ids from that year's `bag`; refill it from the year's matched set — shuffled, and avoiding the ids in
  `last` at the seam — when it runs dry;
- ids that no longer match (deleted, moved, the rule narrowed) drop out of the bag; new ones join
  it, so the cycle carries on rather than restarting;
- `rng` seeded from `f"{slug}|{cycle}"`, not from the clock, so `--dry-run` shows what the real run
  would do. This matters: `Butler.plan()` (`runtime.py:208-252`) is read-only and shared with the
  design preview, and a preview that shows a different selection than the run is worse than no preview.

`random` is the plain sample: per year, `pics_per_year` ids drawn from the matched set with no bag,
so it needs no state and can repeat last week's picks. It is seeded from `f"{slug}|{run number}"`
like `rotate`, so a dry run still shows what the real run will do. Use it when repeats do not
matter and a bag would only add state.

`best` needs no state: sort by `isFavorite` then `exifInfo.rating` descending, then oldest first,
take `pics_per_year` of each year. Note in the docs that it is only as good as the library's stars.

State (`state.py`, `AlbumState` at `state.py:24-40`): two new fields, `pick_bag: dict[str, list[str]]` and
`pick_last: dict[str, list[str]]`, both keyed by year. Additive, so `state.json` stays `version: 1` and an older file loads
unchanged — a first run with no bag simply fills one. Asset UUIDs in `state.json` are fine; the
album id is already there and the file is not tracked. They must never reach `config.toml`
(`config.py`'s "no UUIDs in the config").

`plan()` runs the picker and puts the result in `Plan.to_add`/`to_remove` as now; `apply()`
(`runtime.py:315-360`) is still the only writer, and the bag is written to state **only by
`apply()`**, so a dry run spends nothing.

**Mirror churn is the real cost.** Each rotation removes and adds up to `pics_per_year` assets per year matched,
via `DELETE albums/{id}/assets` and `PUT albums/{id}/assets`. Consequences to document rather than
hide: the album's Immich activity feed shows the swap; a client caching the album sees stale
members for a while (PyImmichFrame's pool cache holds an album for `refresh_hours`, 12 by default,
so a removed photo can still appear on the frame that long); and a weekly cadence is right —
`every 1h` with `rotate` would be churn for its own sake, so warn when a rotating album's schedule
is shorter than a day.

Nothing about the marker changes: a rotating album has an automatic schedule, so
`Butler.suffix_for()` (`runtime.py:161-166`) already gives it `album_suffix_updating` (`↻`), which
is exactly right — it *is* an updating album.

## 4a. The hint line in the album description (N31)

The playback side needs to know what kind of album this is, and the butler is the only thing that
knows. It publishes that as one line in the Immich album's description, which any client can read —
no shared filesystem, no config parsing, and it works if the two services run on different hosts.

```toml
describe = "hint"        # top level: off (default) | hint
```

What a run writes:

```
Our two weeks in Tuscany.

[butler v1] kind=trip order=trip
```

```
[butler v1] kind=recurring-day order=one-per-year rotating=yes
```

| rule | `kind` | `order` | `rotating` |
|---|---|---|---|
| `from`/`to`, no `people` | `trip` | `trip` | |
| `from`/`to` **and** `people` | `trip` | `trip` | |
| `people`, no dates | `person` | `person` | |
| `on = "MM-DD"` (N28) | `recurring-day` | `one-per-year` | |
| `countries`/`states`/`cities` only | `place` | — | |
| `pick = "rotate"` or `"random"` (N29) | as above | — | `yes` |

`rotating=yes` is set whatever else the rule says: the contents change every run, so a player
walking through them in order would be walking through a different album each week. It is the one
field that overrides the others, and it is why this belongs in the butler rather than being guessed
from outside.

### Writing it safely

The description is a field a person edits, so the butler owns exactly one line of it:

- its line is the **last** line matching `^\[butler v\d+\]`; it is replaced in place and every other
  line is kept verbatim. No such line → append a blank line and the hint. An empty description → the
  hint is the whole description.
- `describe = "off"` (the default) makes the next run **remove** the line — a clean uninstall, not a
  leftover.
- `PATCH albums/{id}` (`immich.py:371` already does this for renames) only when the resulting
  description differs from what Immich holds, so an ordinary run writes nothing. The comparison
  needs the description in the album listing, which `GET albums` already returns.
- the line goes in `Plan` (`runtime.py:36-90`) as `describe_to`, so `--dry-run` and the design
  preview show it and `apply()` (`runtime.py:315-360`) stays the only writer.

`album.update` moves from optional to **required** in the key scopes when `describe = "hint"` —
update `deploy/immich-album-butler.env.example` and the README accordingly. A 403 from Immich is a
warning naming the missing scope, never a failed run: the album's contents matter, the hint does not.

`v1` is the format version. Adding a field is not a version bump; changing the meaning of one is. A
reader that does not know a version must ignore the line, which is what the frame plan specifies.

## 5. Design mode

`design/api._rule_from()` (`api.py:841`) and `rule_to_json()` (`api.py:878`) must learn `on`,
`offset_days`, `since_year`, `pics_per_year` and `pick`, or the UI drops them on the next save — the same
trap the marker work hit. Then `design/static/index.html` and `app.js`: a date-pattern field
beside the existing `year`/`month`/`pad` presets (`app.js:428-449`), and a `pics_per_year`/`pick` pair in
the album form. The preview already calls `plan()`, so it shows the rotation for free once the
picker is seeded deterministically.

Lower priority than the runtime path; the config file alone is enough to use the feature.

## 6. Requirements to add

`docs/requirements.md` is at N27, so these are **N28–N31**, under `### 1.1 Rules and albums`.

| ID | Requirement | Prio |
|---|---|---|
| N28 | **A rule may name a recurring calendar day.** `on = "MM-DD"` matches that day in every year from `since_year` to now, `offset_days` widening it by a number of days either side. It is queried as one windowed search per year, not by filtering the library client-side, and it composes with `people`, `countries`, `states` and `cities`. `on` together with `from`/`to` is refused at load, and a rule with only `on` is not "empty". | P2 |
| N29 | **An album may be capped per year, and its contents may rotate.** `pics_per_year` caps how many of each calendar year's matched assets the album holds; `pick` chooses which — `all` (today's behaviour), `best` (favourites and ratings first, stable between runs), `random` (a fresh sample each run, repeats possible), or `rotate`, which prefers assets not used in recent runs so that repeated runs work through the matched set instead of repeating one sample. The rotation bag lives in `state.json` (additively; the file stays version 1), is advanced only by `apply()` so a dry run spends nothing, and is seeded deterministically so the design preview shows what the run will do. `rotate` requires `sync = "mirror"`, a `pics_per_year`, and a non-manual schedule; a cadence under a day is warned about. | P2 |
| N30 | **Rotation is honest about what it costs.** Each run removes and re-adds album members, which shows in Immich's activity feed and can leave a client that caches album contents up to its own refresh interval behind. Assets that stop matching leave the bag and new ones join it, so the cycle carries on rather than restarting. Nothing is ever deleted from the library (N22). | P2 |

| N31 | **An album's kind is published in its description.** With `describe = "hint"` each run keeps one `[butler v1] kind=… order=… [rotating=yes]` line in the Immich album description, derived from the rule: a dated window is a `trip`, people a `person`, a recurring date a `recurring-day`, and `pick = "rotate"` or `"random"` sets `rotating=yes` whatever else applies. The butler owns only that line — the last one matching the marker — and leaves every other line of a hand-written description verbatim; `describe = "off"` removes it again. It is written only when it would change, it rides in the plan so a dry run shows it, and a missing `album.update` scope is a warning rather than a failed run. | P2 |

`docs/design.md`: a `picker.py` row in the `## 2. Modules` table; the new `[match]` and album keys
plus the two state fields in `## 3. Configuration and state`; the new design-mode fields in
`## 4`; and in `## 6. Decisions and risks` — one query per year rather than a client-side sweep,
the bag rather than a plain sample, and the mirror-churn cost.

## 7. Order of work

1. **`picker.py`, pure**, plus `tests/test_picker.py`: `all`, `best`, `random`, `rotate` over plain lists,
   the bag carried over a changed matched set, the seam, and determinism under two different
   seeds. No HTTP.
2. **Config.** `MatchRule.on`/`offset_days`/`since_year`, `Album.pics_per_year`/`pick`, `PICK_MODES`,
   `is_empty`, `_load_match`, `_load_album`, `dump_album`, every refusal. → `tests/test_config.py`.
3. **Matcher.** The per-year windows in `matcher.match()`/`_fetch()`. → `tests/test_matcher.py`
   against `StubImmich` with assets on 17 May of four years plus decoys either side, `offset_days`,
   a leap day, and composition with `people`.
4. **Runtime.** `plan()` calls the picker; `apply()` writes the bag; the schedule warning.
   → `tests/test_runtime.py` (its `Fixture` helper): two consecutive runs pick disjoint sets, a
   third wraps, `--dry-run` twice shows the same set and leaves `state.json` untouched.
5. **State.** `pick_bag`/`pick_last` (per year), and an old file loading without them.
5a. **The hint line (N31).** `describe`, the derivation, the one-line replace, `Plan.describe_to`,
   the 403 warning, and the scope note in the env example and README. → `tests/test_describe.py`:
   each rule shape → its line, a hand-written description preserved around it, `describe = "off"`
   removing it, no PATCH when nothing changed, and `rotating=yes` overriding the rest.
6. **Design mode.** `_rule_from`/`rule_to_json`, then the form. → `tests/test_design.py` round-trips
   a rule through save and reload with the new keys.
7. **Docs.** N28–N30, the design.md sections, and `examples/config.toml` — a `[albums.birthday]`
   block with fictional data only (`name = "Alex's birthday"`, `on = "05-17"`).

## 8. How it is verified

`python scripts/run-tests.py` — the only way tests are run.

Beyond the per-step tests above:

- `python -m immich_album_butler --config-dir … --state-dir … run --once --dry-run birthday`
  against the live server → the plan names the per-year windows it searched, how many matched, and
  the five per year it would add, and writes nothing. Run it twice: identical output.
- then `--once birthday` → the album exists with the `↻` suffix and five members per year; run it again a
  week later (or with the state's `last_run` cleared) → different members, and the bag in
  `state.json` visibly shorter.
- `check` resolves the rule without touching anything.
- On the frame: with `describe = "hint"` set here and `[album_butler] descriptions = true` there,
  the album plays with `one-per-year` without an `order` being configured on the frame at all — one
  photo per year, oldest first, and the frame's decision line naming album butler as the source.
- In the Immich app: the album description shows the hint line, and a sentence typed above it
  survives the next run.
