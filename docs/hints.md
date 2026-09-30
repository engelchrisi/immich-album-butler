# Description hints (N31/N35/N36)

The contract a playback client -- PyImmichFrame or anything else -- implements against when
reading an album the butler manages. The butler is the only thing that knows what kind of album
it built and how it was meant to be played; it publishes that as one line in the album's Immich
`description`, so any client can read it without a shared filesystem or a second connection to
the butler. This file is the full reference for that line: every field, every value it may hold,
what each one means, and what a player should do when a field or the whole line is missing.

This is documentation for a *reader*; see `describe.py` for the butler's own code, and
`docs/design.md` §4 for where it fits among the other modules. The source of truth for which
values are legal is the registry at the top of `describe.py` (`KIND_VALUES`, `ORDER_VALUES`,
`CAPTION_VALUES`, `ACTIVITY_VALUES`, and the numeric ranges) -- a test compares this file against
that registry, so nothing here can drift from what the butler actually writes.

## 1. Line grammar

```
[butler v1] kind=trip order=trip slot=2-3 dwell=8 active=12-01..12-31 caption=year activity=kenburns
```

- One line, anywhere in the description. If the description has other lines (a hand-written
  note), the butler leaves them untouched and keeps its own line separate.
- `key=value` pairs separated by single spaces. No spaces inside a value, no quoting.
- If more than one line matches `[butler vN]` (for example a description edited by hand to
  contain two), **the last one is the one that counts** -- readers should do the same, since
  that is the one the butler itself treats as authoritative and will keep rewriting.
- **Unknown keys must be ignored.** A future version of the butler may add a field this document
  does not yet describe; a reader must skip a `key=value` pair it does not recognise rather than
  fail or discard the rest of the line.
- **Unknown versions must be ignored.** `[butler v2]`, `v3`, etc. may one day change what a key
  means. A reader that only understands v1 must not try to interpret a line whose version it does
  not know -- treat the album as if it had no hint at all (§6).
- Only `describe.py` ever writes this line. A user can still hand-edit an album's description
  around it; the butler preserves everything else in the description verbatim and only ever
  replaces or removes its own single line (see `apply_hint` in `describe.py`).

## 2. Field reference

| Key | Always present? | Values | Set by |
|---|---|---|---|
| `kind` | always | see §3 | derived from the rule, or `hint_kind` |
| `order` | usually (every `kind` except `place`/`album` unless overridden) | see §3/§4 | derived from the rule, or `hint_order` |
| `rotating` | only when `yes` | `yes` | `pick = "rotate"` or `"random"` (N29) |
| `slot` | only when set | `N` or `N-M`, `1`-`10` | the `slot` album key |
| `dwell` | only when set | whole seconds, `1`-`3600` | the `dwell` album key |
| `active` | only when set | `MM-DD..MM-DD` | the `active` album key |
| `caption` | only when set (and not `none`) | see §5 | the `caption` album key |
| `activity` | only when set (and not `none`) | see §5 | the `activity` album key |

A field that is "only when set" is simply absent from the line otherwise -- its absence means
the player's own default applies, not that the album has no opinion.

## 3. `kind` and `order`: derived from the rule, or overridden

`describe.hint_line()` looks at the album's rule shape and picks a `kind` and `order` from it.
The rule is checked in this order -- the first match wins:

| Rule shape | `kind` | `order` |
|---|---|---|
| has a date window (`from`/`to`) | `trip` | `trip` |
| recurring calendar window (`on_from`/`on_to`, N28) | `recurring-day` | `one-per-year` |
| has `people` | `person` | `person` |
| places only (`countries`/`states`/`cities`) | `place` | *(none)* |
| none of the above | `album` | *(none)* |

**An album's own config can override either one independently**, with `hint_kind` and
`hint_order`. There is no config to invent a new value -- both are still checked against the
same closed lists as the derived ones (§4), so a typo is refused at load, naming the allowed
list, the same way a bad `pick` value already is.

Every value in the `kind` column above (`trip`, `recurring-day`, `person`, `place`, `album`) is
legal in `hint_kind` too; a `kind` never appears in a line that isn't one of those five.

## 4. `order` values and what a player should do

| `order` | Meaning | Suggested playback |
|---|---|---|
| `trip` | one continuous story across a date window | chronological, start to end |
| `one-per-year` | a recurring day, one photo kept per year | walk by year, oldest to newest (or newest first, at the player's choice); a nice touch is boosting the current year's entry on that actual calendar date |
| `person` | built around one or more people, no inherent order | shuffle |
| `time-asc` | play by capture time, oldest first | chronological |
| `time-desc` | play by capture time, newest first | reverse chronological |
| `random` | no inherent order | fully shuffled, independent of neighbours |
| `slots` | grouped neighbours | see below |

`trip`, `one-per-year` and `person` are also the values `hint_line()` derives automatically;
`time-asc`, `time-desc`, `random` and `slots` only ever appear through `hint_order` override,
since the butler never derives them on its own.

### `slots`

Pick a random photo from the album, then show it together with its next `N` neighbours **in the
album's own time order** (not necessarily consecutive calendar days -- just the next matching
photos), then jump to a new random start and repeat. It groups a moment (a birthday's few shots,
a handful of frames from one visit) instead of showing one photo at a time from a shuffle.

- `N` comes from the album's `slot` key: a single number (`slot = 3`) or a range
  (`slot = "2-3"`, a different count each jump, picked at random within the range). Default
  when `order = "slots"` is set but `slot` is not: the player's own default (not specified here).
- This is a **pure next-N walk**: there is no gap limit built into the hint. If an album is
  sparse (few photos spread across years), a "slot" can span a large real-world time gap between
  its members. This is most useful on an uncapped `person` or `place` album, or a `trip`; it
  fights against a `pics_per_year` cap, since a cap leaves fewer neighbours to group.

## 5. `caption` and `activity`

Both are hints about presentation, not requirements -- a player that does not support a value
should fall back to its own default rather than failing.

| `caption` | Meaning |
|---|---|
| `none` | *(never written -- its absence means the same thing)* |
| `year` | overlay the year the photo was taken |
| `place` | overlay the place name |
| `title` | overlay the album's name |

| `activity` | Meaning |
|---|---|
| `none` | *(never written -- its absence means the same thing)* |
| `kenburns` | a slow pan/zoom across the still photo |
| `face-zoom` | pan/zoom that favours a detected face |
| `map-fly` | a moving fly-through on a map, for a geotagged photo |

## 6. What a player must do with no hint, or one it cannot use

- **No `[butler vN]` line at all** (`describe = "off"`, or the butler has never touched this
  album): treat the album with whatever default behaviour the player already has for an
  unlabelled album. This is the common case for any album not built by immich-album-butler.
- **A line with an unknown version**: treat it exactly like "no hint line" (previous bullet).
  Never guess at a future version's field meanings.
- **A recognised version, with an unrecognised key or value on some field**: ignore that one
  field (fall back to the player's own default for it) and use every field that *is*
  recognised. A single unknown field must not invalidate the whole line.
- **`kind` present but `order` absent** (places and plain albums, unless overridden): the album
  still has a kind worth knowing -- for grouping, iconography, etc. -- it simply expresses no
  preference about play order.

## 7. Worked examples

```
[butler v1] kind=trip order=trip
```
A plain dated trip, no overrides: play chronologically.

```
[butler v1] kind=recurring-day order=one-per-year rotating=yes
```
A rotating recurring-day album (e.g. a yearly birthday window with `pick = "rotate"`): the
member set changes on a schedule, so the player's cache should refresh more eagerly than for a
fixed album (see N30 in `docs/requirements.md`).

```
[butler v1] kind=person order=slots slot=3 caption=year activity=kenburns
```
A person album, overridden to play in grouped threes, captioned with the year, with a
slow pan/zoom on each photo.

```
[butler v1] kind=recurring-day order=one-per-year active=12-01..12-31
```
A Christmas recurring-day album that only wants to be surfaced by the player during December.

## 8. Not yet decided

How a player should *weight* one album against others when several are eligible at once, and
how strictly `active` should be enforced (hidden entirely outside the window, vs. simply
deprioritised) are choices for the player, not something the butler prescribes here. This section
will be filled in once PyImmichFrame's side of this is designed.
