# Design

## 1. Shape

One package, `immich_album_butler`, no dependencies. Two entry modes over one configuration:

```
design mode  --writes-->  config.toml  --read by-->  runtime mode  --writes albums-->  Immich
   (web UI)                                            (daemon)
```

Design mode is the only writer of `config.toml`; runtime mode is the only code that changes
albums in Immich (`runtime.apply`). Design mode can also make the explicit asset metadata changes
in N44-N45. `plan()` is read-only and is shared by `--dry-run` and design
mode's preview, so the preview cannot drift from what a run does.

## 2. Modules

| Module | Role |
|---|---|
| `cli.py` | `run`, `design`, `trips`, `backup`, `backups`, `restore`, `passwd`, `check`; API key read from the environment |
| `config.py` | Read and write `config.toml` (settings, design users, groups, album rules); people by name |
| `immich.py` | Narrow Immich API client: read assets/people/albums, create albums, add assets |
| `matcher.py` | Album rule → asset set; places OR'd client-side, `people_mode` independent of server semantics; a recurring day (N28) is one windowed query per year |
| `picker.py` | Pure, HTTP-free: caps a matched set to `pics_per_year` per calendar year and picks which — `all`/`best`/`random`/`rotate` (N29) |
| `describe.py` | The `[butler vN]` play-hint line kept in an album's Immich description (N31) |
| `runtime.py` | `plan()` (read-only) and `apply()` (the only writer); per-album failure isolation |
| `schedule.py` | The `auto-update-schedule` grammar |
| `state.py` | Album ids and last run per album; atomic rewrite |
| `backup.py` | Album backup and additive restore (N37-N39): one 0600 JSON file per backup; restore by id, then checksum |
| `cover.py` | Cover choice by rule or file name |
| `trips.py` | Trip detection from geotagged photos away from home |
| `analyze.py` | Near-miss suggestions for a rule |
| `design/api.py` | The whole design feature as plain Python (pickers, preview, analyze, save, run, trip and album GPS backfill preview/apply) |
| `design/server.py` | HTTP layer: login, routing, static files, thumbnail proxy |
| `design/auth.py` | scrypt hashes, constant-time compare, sessions |
| `design/static/` | `index.html`, `login.html`, `app.js`, `style.css`, `icon.svg` |

The Trips page reuses the full library scan when a search field changes. Its
distance, minimum media and minimum days are passed to trip detection; maximum
days and the optional year/month overlap are applied to whole detected trips.
The defaults remain 100 km, 30 media and two days, while a one-day search is
available by setting both duration bounds to one.

## 3. Configuration and state

`config.toml` in the config directory (see `examples/config.toml`): global settings, design users,
groups, albums. State (`state.json`) lives in the state directory and holds Immich album ids and
last-run times; it is rewritten atomically because a half-written file would make the butler
forget which album it owns and create a duplicate.

New keys: `[match]` gains `on_from`/`on_to`/`since_year` (N28); an album gains `pics_per_year`
and `pick` (N29); the top level gains `describe` (N31). State gains `pick_bag`, `pick_last` (both
keyed by calendar year) and `pick_cycle`, all additive so `state.json` stays `version: 1` and an
older file loads unchanged. Asset UUIDs stay in `state.json`, never in `config.toml`.

## 4. Album description hints (N31)

`describe.py` builds the hint line from two album keys, `chunk` and `chunk_order`; nothing is
derived from the rule shape any more. An album with a `chunk` gets one line in its Immich
description, no setting required; an album without one gets none, and a line the butler wrote
earlier is taken out (`apply_hint(description, "")`):

```
[butler v1] chunk=3/year chunk_order=chronological
```

`chunk` is `<n>/<span>` (n from 1 to 50, span `year`, `month` or `day`); `chunk_order` is
`chronological` or `random` and defaults to `chronological` when only `chunk` is set. Both are a
**closed vocabulary**: checked against the tables in `describe.py` (`CHUNK_SPANS`,
`CHUNK_ORDER_VALUES`, `MIN_CHUNK`/`MAX_CHUNK`, `parse_chunk()`) at three points: `config.py` on
load and on a design-mode save (`_load_chunk`, the same function both call), and `hint_line()`
itself, which asserts its output against the same tables as a second line of defence. A
description line is therefore never built from a free-form string; an unrecognised value is
refused at the point it was written, naming what is allowed. `chunk_order` without `chunk` is
refused, and so are the keys the line used to carry (`hint_kind`, `hint_order`, `slot`, `dwell`,
`active`, `caption`, `activity`: `REMOVED_HINT_KEYS`) -- an unmigrated config fails loudly
instead of silently losing its hints.

The design UI's album builder, on its Player sub-tab, has a count, a span and an order, with a
live preview of the resulting line from the `hint` field of `/api/preview` and
`/api/hint-preview`. The album templates fill them (trip 3/day random, birthday 3/year
chronological, everything else 5/year random).

The full field reference -- every value, its meaning, and what a player such as PyImmichFrame
must do with an unknown or absent field -- is `docs/hints.md`; `tests/test_describe.py` checks
that every value in the registry is documented there. The player side is PyImmichFrame's
`docs/design.md` ("Play hints") and requirements N39-N40.

## 5. Design-mode HTTP

Login in front of everything. Read: `/api/whoami`, `/api/people`, `/api/places`, `/api/albums`,
`/api/groups`, `/api/accounts`, `/api/immich-albums`, `/api/trips`, `/api/duplicates[/album]`,
`/api/browse/album`, `/api/backups`, `/api/thumb/…`, `/api/asset/…`. Write: `/api/backups` (create), `/api/backups/restore` (`dry_run`, `config`, `album`), `/api/backups/delete` (`names`, bare `backup-*.json` names only), `/api/preview`, `/api/analyze`,
`/api/albums`, `/api/groups`, `/api/run`, `/api/add-assets`, `/api/duplicates/remove`,
`/api/cover`, `/api/trips/gps-preview`, `/api/trips/gps-apply`,
`/api/albums/gps-preview`, `/api/albums/gps-apply`,
`/api/gps-donors`, `/api/gps-donors/remove`,
`/api/asset/{id}/favorite`, `/api/asset/{id}/reject`.

The one global hover card handles every image served by `/api/thumb/{id}`. The thumbnail proxy
requests Immich's edited rendition (`edited=true`), so rotations, crops, and other editor actions
are visible. The favourite and reject controls apply in the builder, album browser, trips,
duplicates and any later view that uses the thumbnail proxy. The card takes pointer events and
delays closing between thumbnail and card.
The two write routes accept booleans and keep the Immich key server-side. Reject is rating `-1`;
its prior rating is kept in process memory for undo and falls back to JSON `null` (unrated) after a
restart. Immich 3 rejects the older rating `0` representation. Rating writes use `PUT
/api/assets/{id}`, then read the asset back; when the stored rating differs, they retry the v3
`PATCH` route and read it back again. This matters for writable shared albums, where Immich can
acknowledge a write without changing an unowned asset. The API reports failure unless the stored
rating matches, so a page reload cannot turn a claimed reject back into an ordinary photo. The
pressed reject button uses a solid warning colour and exposes `aria-pressed`, making its current
state clear visually and to assistive technology.

Trip GPS backfill (N44) is design-mode-only and explicit. `/api/trips/gps-preview`
computes candidate coordinates for trip media without GPS from same-day, geotagged
trip media and reports confidence (`high`/`medium`/`low`), donor count, nearest
time delta and donor spread (km). One donor is sufficient for high confidence
when it is within 90 minutes and 2 km; additional donors are not required.
`/api/trips/gps-apply` updates only selected
media, skips media that already has GPS, and reports partial failures per item.
The preview includes each missing asset's camera make/model from the versioned
trip-scan cache, allowing the UI to group both suggested and unresolved fixes
by camera. Assets without those EXIF fields appear under `Unknown camera`.
It also returns the matched album's complete set of located media as
`available_donors` (or the trip's located media when there is no matched album).
The shared source picker can assign one of these donors to every checked row in
one action; this only changes the preview until the normal confirmed Apply.
GPS donor favorites are a separate browser-local list of asset IDs, not Immich's
`isFavorite` heart. The UI intersects that list with `available_donors`, so a
favorite source is offered only while reviewing the same album.
Named GPS donors are different: `/api/gps-donors` persists a user-supplied
name, asset ID and its checked coordinates in `gps-donors.json` under the
design state directory. The API validates the source is a located photo when
it is saved. These donors appear in every GPS review and may assign their
location to all checked photos; the normal confirmed Apply is still required.
The API key needs Immich's `asset.update` permission; `album.update` alone does
not authorize asset coordinate changes. After applying a day, the UI reloads the
trip and GPS suggestions in place, keeping the selected day when candidates
remain or advancing to the next day with candidates. Donor thumbnails are
selectable buttons, and the selected source is highlighted. The review uses
capture times and donor time gaps instead of raw coordinates or asset IDs;
unresolved fixes use the same checkboxes and Apply action. Runtime mode never
calls this path. Checkbox selection supports the conventional inclusive range:
check one visible row, then Shift-click another to apply its checked state to
both endpoints and every displayed row between them.

GPS backfill writes `PUT /assets/{id}` (`latitude`/`longitude` only); an
earlier version wrongly sent `PATCH`, which Immich silently ignores -- no
error, no write, so the fix looked applied until the next load.

That endpoint is also what Immich's own manual location editor calls, and
persisting the value is Immich's job, not this project's: Immich writes
location (and date) edits into an XMP sidecar next to the original via
`exiftool`, and its metadata-extraction job always re-derives GPS from the
file plus sidecar, overwriting the database on every scan. If that sidecar
write fails -- the library's mount is read-only, or the Immich service
account lacks filesystem permission even on a writable mount -- the API call
still reports success and the database value is set immediately, but the
next metadata scan finds no GPS in the file and wipes it again, with nothing
visible to the API caller or the UI (upstream: immich-app/immich#10538,
open). A GPS backfill through this project is only as durable as Immich's own
editor for the same asset: nothing in this project can detect, retry or work
around a silently failed sidecar write, since Immich gives no signal that it
happened. The fix is always on the serving environment, not this project --
the library needs to be writable, by both mount and filesystem permissions,
for whichever account Immich runs as (confirmed working end-to-end in the
maintainer's own deployment, 2026-10-12).

Album GPS backfill (N46) uses the same review and apply safeguards, but reads
the selected album's current media directly instead of depending on the trip
scan cache. `/api/albums/gps-summary` returns only counts grouped by affected
day, including HIGH confidence counts; `/albums/<id>/gps` presents those days
under collapsible years without loading thumbnails. The separate
`/api/albums/gps-high-preview` returns one 50-item page of HIGH suggestions
across all days, newest first, with no whole-album donor list. Its review route
is `/albums/<id>/gps/high/<page>`; the user may deselect rows, switch donors,
review an individual day, and explicitly apply the selected page. A successful
apply opens the first page of the remaining HIGH matches. Alternative donor
thumbnails load only when their picker is expanded. The normal
`/api/albums/gps-preview` requires a day and returns only
that day's targets and located donors to `/albums/<id>/gps/<yyyy-mm-dd>`. The
optional `/api/albums/gps-donors` picker pages through 50 located photos from
that same album, newest first. Its choices exist only in the open review and
are not saved unless the user explicitly makes one a named donor. The album
scan is cached in memory for five minutes while moving between these
pages and invalidated after an apply. `/api/albums/gps-apply` verifies that
every selected target still belongs to that album before it writes. A day
review returns to the GPS overview, and its thumbnails are 30% larger than
their Trips counterparts while the strips still wrap on narrow screens.
Each day row keeps its Review button directly beside its counts, rather than
at the far edge of a wide display.
Within a focused day review, Previous day and Next day move among affected
days in that same calendar year. The overview's compact day list is reused;
after an apply it refreshes before rebuilding the navigation.

GPS review filters out assets with Immich rating `-1` (the design UI's
not-wanted action) before producing targets or donors. The scan records the
rating so Trip reviews retain that filtering from cache; toggling not wanted
patches the cache and clears album GPS caches immediately. Saved donor entries
are retained on disk but hidden while their source asset is rejected.

`POST /api/albums` saves a rule under its slug, replacing one of the same slug. With `new: true`
("Save as…", N41) the slug comes from the new name only, and a name another rule already has
(case-insensitive, or one slugging the same) is refused with 409, so the copy never overwrites
the original. No state is copied: the copy's first run creates its own Immich album.

`/api/albums` lists every Immich album this key can see, not only the ones with a rule: a row's
`type` is `"manual"` or `"scheduled"` (the album's schedule kind, N33) or `"normal"` (no rule at
all -- `slug` is `null`, most fields are `null`/empty). Each Albums card shows a status dot (OK /
failed / disabled / not run), labelled rows (Rule, Schedule, Last run or Error), chips for extras,
and a footer with the actions. The Albums page's "View" button opens
`/api/browse/album` for any of the three; `/api/cover` writes a chosen asset as an album's cover
the same way, butler-managed or not (N34). Runtime cover rules are creation-time defaults only;
later scheduled and manual runs preserve that chosen cover (N6).

## 6. Deployment

`deploy/install.sh` copies the package to `/opt/immich-album-butler`, seeds config and env file,
installs `immich-album-butler.service` (daemon) and `immich-album-butler-design.service`
(not enabled; `ReadWritePaths` on the config directory because design mode writes it).
`deploy/deploy.py` is the shared deployment engine used by the shell and
PowerShell entry points in all three client projects. It packages committed
`HEAD` by default; `--working-tree` packages tracked local changes, staged
additions and deletions. A manifest binds every file to its SHA-256 checksum,
project, commit and version. The remote side validates it before installation,
serialises deploys with a lock, preserves config and state, and verifies the
installed version plus stable service readiness. It restores the previous code
and systemd units on failure.

A normal deploy restarts the runtime and resumes design mode only when it was
already active. `--design-only` explicitly starts design mode and resumes an
active runtime after the shared package is replaced.

## 7. Decisions and risks

- Backup (N37-N39) is a metadata file, not an export: ids and checksums are enough to rebuild an
  album on the same library, and the checksum finds a photo again if the library was re-imported.
  Restore is additive on purpose -- a restore that removed things could damage a library that has
  moved on since the backup. Cover and sharing are restored only for albums the restore creates.
  The file includes config.toml (login hashes), hence 0600; the butler's albums are in the Immich
  list too, and `state.json` ids are remapped so recreated albums stay owned by their rule.

- Names, not UUIDs, in the config: a UUID is meaningless to a reader and breaks if an album is recreated.
- Fake servers over mocks (`tests/stub_immich.py`, `tests/fake_immich.py`): the HTTP layer is under test.
- Risk: design mode shows the library to anyone who reaches its port, so the login is the boundary.
- A recurring day (N28) is one windowed search per year, not a client-side sweep: twenty-odd paged
  searches once a week keep the filtering server-side, where fetching the whole library to filter it
  would page tens of thousands of assets to find a handful.
- Rotation (N29) draws from a bag rather than a plain random sample: a plain sample repeats itself,
  so over a year you would see the same few photos again and never the others.
- Trip GPS backfill (N44) stays conservative: same-day donors only, with wide
  donor clusters rejected, so a one-click convenience does not become silent,
  broad geotag drift.
- Mirror churn is rotation's real cost (N30): each run removes and re-adds up to `pics_per_year`
  assets per year, which shows in Immich's activity feed and can leave a caching client (e.g.
  PyImmichFrame's pool cache) behind for its refresh interval — so a weekly cadence is right and a
  sub-day one is warned about.

## 8. Versioning

`__version__` is read at import time from `pyproject.toml`'s `[project] version` field via
`tomllib`, not hardcoded. `deploy/install.sh` copies `pyproject.toml` into `$PREFIX` alongside
the package so this file exists at runtime; if missing or unreadable, `__version__` falls back
to `"0.0.0+unknown"` rather than crashing. It feeds the design-mode HTTP `Server` header and a
label next to "Album Butler" on the design UI's main heading. See CLAUDE.md for the bump/branch
convention.
