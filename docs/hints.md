# Description hints (N31)

The contract a playback client -- PyImmichFrame or anything else -- implements against when
reading an album the butler manages. The butler is the only thing that knows how an album was
meant to be played; it publishes that as one line in the album's Immich `description`, so any
client can read it without a shared filesystem or a second connection to the butler. This file
is the full reference for that line: both fields, every value they may hold, what each one
means, and what a player should do when a field or the whole line is missing.

This is documentation for a *reader*; see `describe.py` for the butler's own code, and
`docs/design.md` §4 for where it fits among the other modules. The source of truth for which
values are legal is the registry at the top of `describe.py` (`CHUNK_SPANS`,
`CHUNK_ORDER_VALUES`, `MIN_CHUNK`..`MAX_CHUNK`) -- a test compares this file against that
registry, so nothing here can drift from what the butler actually writes.

The line says **how to play an album, and nothing else.** It does not say what kind of album it
is, and it does not say *when* to show it: which days of the year an album covers belongs to
the album's definition in the butler, not to the hint.

## 1. Line grammar

```
[butler v1] chunk=3/year chunk_order=chronological
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
  not know -- treat the album as if it had no hint at all (§4).
- Only `describe.py` ever writes this line. A user can still hand-edit an album's description
  around it; the butler preserves everything else in the description verbatim and only ever
  replaces or removes its own single line (see `apply_hint` in `describe.py`).

## 2. Field reference

| Key | Present? | Values | Set by |
|---|---|---|---|
| `chunk` | whenever the line is | `<n>/<span>`: `n` a whole number `1`-`50`, `span` one of `year`, `month`, `day` | the `chunk` album key |
| `chunk_order` | whenever the line is | `chronological` or `random` | the `chunk_order` album key (default `chronological`) |

An album without a `chunk` has **no line at all**: the butler writes none, and takes out one it
wrote earlier. A line always carries both keys, so a reader never has to guess the default.

### `chunk=<n>/<span>`

The album is played in *chunks*: `n` photos taken from one `year`, `month` or `day`, then on to
the next one. `chunk=3/year` is three photos of one year, then three of another; `chunk=5/day`
is five photos of one day. Photos of a chunk are shown in time order, oldest first, so a chunk
reads as one moment. When a year, month or day has fewer than `n` photos left, it gives what it
has. The player keeps cycling through the spans -- taking the next `n` of each -- until every
photo of the album has been shown once; that is one pass.

Which `n` photos of a span come out is the player's choice (a random `n`, a different `n` on
each cycle, is the suggestion).

### `chunk_order`

How the years, months or days follow each other.

| `chunk_order` | Meaning |
|---|---|
| `chronological` | oldest span first, then the next, and so on |
| `random` | the spans in a shuffled order, none repeated until all have had a turn |

## 3. What the butler writes

The defaults of the design UI's templates -- every one of them can be changed on the album's
Player sub-tab or in `config.toml`:

| Template | Line |
|---|---|
| Trip | `[butler v1] chunk=3/day chunk_order=random` |
| Birthday over the years | `[butler v1] chunk=3/year chunk_order=chronological` |
| Christmas, New Year's Eve, Summer, Anniversary, Year in review | `[butler v1] chunk=5/year chunk_order=random` |
| Person album, A person over the years, Two people together | `[butler v1] chunk=5/year chunk_order=random` |
| A place | `[butler v1] chunk=5/year chunk_order=random` |
| no template, no `chunk` | no line |

## 4. What a player must do with no hint, or one it cannot use

- **No `[butler vN]` line at all** (the album has no `chunk`, was not built by
  immich-album-butler, or predates this format and has not been re-run since): treat the album
  with whatever default behaviour the player already has for an unlabelled album.
- **A line with an unknown version**: treat it exactly like "no hint line" (previous bullet).
  Never guess at a future version's field meanings.
- **A recognised version, with an unrecognised key or value on some field**: ignore that one
  field (fall back to the player's own default for it) and use every field that *is*
  recognised. A single unknown field must not invalidate the whole line.
- **`chunk_order` without a usable `chunk`**: nothing to order -- play the album as unlabelled.

## 5. Worked examples

```
[butler v1] chunk=3/day chunk_order=random
```
A trip: three photos of one day, then three of another day, the days in no particular order.

```
[butler v1] chunk=3/year chunk_order=chronological
```
A birthday album: three photos of the oldest year, three of the next, up to the newest -- and
round again for the photos not yet shown.

```
[butler v1] chunk=5/year chunk_order=random
```
A person album: five photos of one year, then five of another.

## 6. History

Until 1.0.0 the line also carried `kind`, `order`, `rotating`, `slot`, `dwell`, `active`,
`caption` and `activity`. All of them are gone: no player read them, and a hint should say how
to play, not describe the album. A description still holding such a line is rewritten on the
album's next run; an album without a `chunk` loses the line. The matching album keys
(`hint_kind`, `hint_order`, `slot`, `dwell`, `active`, `caption`, `activity`) are refused at
load, naming the key.
