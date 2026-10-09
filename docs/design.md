# Design

## 1. Shape

One package, `immich_album_butler`, no dependencies. Two entry modes over one configuration:

```
design mode  --writes-->  config.toml  --read by-->  runtime mode  --writes albums-->  Immich
   (web UI)                                            (daemon)
```

Design mode is the only writer of `config.toml`; runtime mode is the only code that changes
albums in Immich (`runtime.apply`). `plan()` is read-only and is shared by `--dry-run` and design
mode's preview, so the preview cannot drift from what a run does.

## 2. Modules

| Module | Role |
|---|---|
| `cli.py` | `run`, `design`, `trips`, `backup`, `backups`, `restore`, `passwd`, `check`; API key read from the environment |
| `config.py` | Read and write `config.toml` (settings, design users, groups, album rules); people by name |
| `immich.py` | Narrow Immich API client: read assets/people/albums, create albums, add assets |
| `matcher.py` | Album rule → asset set; places OR'd client-side, `people_mode` independent of server semantics; a recurring day (N28) is one windowed query per year |
| `picker.py` | Pure, HTTP-free: caps a matched set to `pics_per_year` per calendar year and picks which — `all`/`best`/`random`/`rotate` (N29) |
| `describe.py` | The `[butler vN]` hint line kept in an album's Immich description (N31) |
| `runtime.py` | `plan()` (read-only) and `apply()` (the only writer); per-album failure isolation |
| `schedule.py` | The `auto-update-schedule` grammar |
| `state.py` | Album ids and last run per album; atomic rewrite |
| `backup.py` | Album backup and additive restore (N37-N39): one 0600 JSON file per backup; restore by id, then checksum |
| `cover.py` | Cover choice by rule or file name |
| `trips.py` | Trip detection from geotagged photos away from home |
| `analyze.py` | Near-miss suggestions for a rule |
| `design/api.py` | The whole design feature as plain Python (pickers, preview, analyze, save, run) |
| `design/server.py` | HTTP layer: login, routing, static files, thumbnail proxy |
| `design/auth.py` | scrypt hashes, constant-time compare, sessions |
| `design/static/` | `index.html`, `login.html`, `app.js`, `style.css`, `icon.svg` |

## 3. Configuration and state

`config.toml` in the config directory (see `examples/config.toml`): global settings, design users,
groups, albums. State (`state.json`) lives in the state directory and holds Immich album ids and
last-run times; it is rewritten atomically because a half-written file would make the butler
forget which album it owns and create a duplicate.

New keys: `[match]` gains `on_from`/`on_to`/`since_year` (N28); an album gains `pics_per_year`
and `pick` (N29); the top level gains `describe` (N31). State gains `pick_bag`, `pick_last` (both
keyed by calendar year) and `pick_cycle`, all additive so `state.json` stays `version: 1` and an
older file loads unchanged. Asset UUIDs stay in `state.json`, never in `config.toml`.

## 4. Album description hints (N31/N35/N36)

`describe.py` derives the hint line from an album's rule shape; the line is always kept in every
butler-managed album's Immich description, no setting required. The rule is checked in this
order — the first match wins:

| Rule shape | `kind` | `order` |
|---|---|---|
| has a date window (`from`/`to`) | `trip` | `trip` |
| recurring calendar window (`on_from`/`on_to`, N28) | `recurring-day` | `one-per-year` |
| has `people` | `person` | `person` |
| places only (`countries`/`states`/`cities`) | `place` | — |
| none of the above | `album` | — |

Independently of the row matched above, `rotating=yes` is appended whenever `album.rotating` is
true (`pick = "rotate"` or `"random"`, N29) — it's an extra flag, not a different kind/order.

`hint_kind` / `hint_order` (N35) override either value per album, and `slot`, `dwell`, `active`,
`caption`, `activity` (N36) add optional playback-only fields to the line. All seven are a
**closed vocabulary**: every enum is checked against a table in `describe.py`
(`KIND_VALUES`/`ORDER_VALUES`/`CAPTION_VALUES`/`ACTIVITY_VALUES`) and every number against a
range (`MIN_DWELL`/`MAX_DWELL`, `MIN_SLOT`/`MAX_SLOT`) at three points: `config.py` on load and
on a design-mode save (`design/api.py`'s `_load_hint`, the same function both call),
and `hint_line()` itself asserts its own output against the same tables as a second line of
defence. A description line is therefore never built from a free-form string; an unrecognised
value is refused at the point it was written, naming the allowed list. The design UI's album
builder exposes all seven as bounded `<select>`/number inputs (a "Description hint" fieldset),
with a live preview of the resulting line from `/api/preview`'s `hint` field.

Example line: `[butler v1] kind=recurring-day order=one-per-year rotating=yes`.

The full field reference — every value, its meaning, and what a player such as PyImmichFrame
must do with an unknown or absent field — is `docs/hints.md`; `tests/test_describe.py` checks
that every value in the registry is documented there.

## 5. Design-mode HTTP

Login in front of everything. Read: `/api/whoami`, `/api/people`, `/api/places`, `/api/albums`,
`/api/groups`, `/api/accounts`, `/api/immich-albums`, `/api/trips`, `/api/duplicates[/album]`,
`/api/browse/album`, `/api/backups`, `/api/thumb/…`, `/api/asset/…`. Write: `/api/backups` (create), `/api/backups/restore` (`dry_run`, `config`, `album`), `/api/backups/delete` (`names`, bare `backup-*.json` names only), `/api/preview`, `/api/analyze`,
`/api/albums`, `/api/groups`, `/api/run`, `/api/add-assets`, `/api/duplicates/remove`,
`/api/cover`.

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
the same way, butler-managed or not (N34).

## 6. Deployment

`deploy/install.sh` copies the package to `/opt/immich-album-butler`, seeds config and env file,
installs `immich-album-butler.service` (daemon) and `immich-album-butler-design.service`
(not enabled; `ReadWritePaths` on the config directory because design mode writes it).

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
