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
| `matcher.py` | Album rule → asset set; places OR'd client-side, `people_mode` independent of server semantics |
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

## 4. Design-mode HTTP

Login in front of everything. Read: `/api/whoami`, `/api/people`, `/api/places`, `/api/albums`,
`/api/groups`, `/api/accounts`, `/api/immich-albums`, `/api/trips`, `/api/duplicates[/album]`,
`/api/browse[/album]`, `/api/thumb/…`, `/api/asset/…`. Write: `/api/preview`, `/api/analyze`,
`/api/albums`, `/api/groups`, `/api/run`, `/api/add-assets`, `/api/duplicates/remove`.

## 5. Deployment

`deploy/install.sh` copies the package to `/opt/immich-album-butler`, seeds config and env file,
installs `immich-album-butler.service` (daemon) and `immich-album-butler-design.service`
(not enabled; `ReadWritePaths` on the config directory because design mode writes it).

## 6. Decisions and risks

- Names, not UUIDs, in the config: a UUID is meaningless to a reader and breaks if an album is recreated.
- Fake servers over mocks (`tests/stub_immich.py`, `tests/fake_immich.py`): the HTTP layer is under test.
- Risk: design mode shows the library to anyone who reaches its port, so the login is the boundary.
