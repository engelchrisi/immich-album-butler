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
| `cli.py` | `run`, `design`, `trips`, `passwd`, `check`; API key read from the environment |
| `config.py` | Read and write `config.toml` (settings, design users, groups, album rules); people by name |
| `immich.py` | Narrow Immich API client: read assets/people/albums, create albums, add assets |
| `matcher.py` | Album rule → asset set; places OR'd client-side, `people_mode` independent of server semantics; a recurring day (N28) is one windowed query per year |
| `picker.py` | Pure, HTTP-free: caps a matched set to `pics_per_year` per calendar year and picks which — `all`/`best`/`random`/`rotate` (N29) |
| `describe.py` | The `[butler vN]` hint line kept in an album's Immich description (N31) |
| `runtime.py` | `plan()` (read-only) and `apply()` (the only writer); per-album failure isolation |
| `schedule.py` | The `auto-update-schedule` grammar |
| `state.py` | Album ids and last run per album; atomic rewrite |
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

## 4. Design-mode HTTP

Login in front of everything. Read: `/api/whoami`, `/api/people`, `/api/places`, `/api/albums`,
`/api/groups`, `/api/accounts`, `/api/immich-albums`, `/api/trips`, `/api/duplicates[/album]`,
`/api/browse/album`, `/api/thumb/…`, `/api/asset/…`. Write: `/api/preview`, `/api/analyze`,
`/api/albums`, `/api/groups`, `/api/run`, `/api/add-assets`, `/api/duplicates/remove`,
`/api/cover`.

`/api/albums` lists every Immich album this key can see, not only the ones with a rule: a row's
`type` is `"manual"` or `"scheduled"` (the album's schedule kind, N33) or `"normal"` (no rule at
all -- `slug` is `null`, most fields are `null`/empty). The Albums page's "View" button opens
`/api/browse/album` for any of the three; `/api/cover` writes a chosen asset as an album's cover
the same way, butler-managed or not (N34).

## 5. Deployment

`deploy/install.sh` copies the package to `/opt/immich-album-butler`, seeds config and env file,
installs `immich-album-butler.service` (daemon) and `immich-album-butler-design.service`
(not enabled; `ReadWritePaths` on the config directory because design mode writes it).

## 6. Decisions and risks

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

## 7. Versioning

`__version__` is read at import time from `pyproject.toml`'s `[project] version` field via
`tomllib`, not hardcoded. `deploy/install.sh` copies `pyproject.toml` into `$PREFIX` alongside
the package so this file exists at runtime; if missing or unreadable, `__version__` falls back
to `"0.0.0+unknown"` rather than crashing. It feeds the design-mode HTTP `Server` header and a
label next to "Album Butler" on the design UI's main heading. See CLAUDE.md for the bump/branch
convention.
