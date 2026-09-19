# immich-album-butler

A butler that keeps your [Immich](https://immich.app) albums in order: by
people, places and dates.

> Unofficial third-party tool. Not affiliated with or endorsed by the Immich
> project.

Albums in Immich are manual. This tool makes them **rules** instead: you
describe an album once — who is in it, where it was, when it was — and the
butler keeps it filled as new photos arrive.

It has two modes:

- **Design mode** — a small web UI to explore your library, detect trips,
  build album rules with live previews, and save each rule as a config file.
- **Runtime mode** — a headless daemon with no UI and no open port. It reads
  those config files and updates the albums on a schedule.

Design mode writes the configuration; runtime mode consumes it. You can run
runtime mode alone, and start design mode only when you want to change
something.

## Status

Early development. The core (config, scheduling, matching, runtime) comes
first; design mode follows.

## Requirements

- An Immich server and an API key
- Python **3.11+**, standard library only — no pip packages, no Docker

## Configuration

Everything lives in one directory (`/etc/immich-album-butler` by default), in
TOML, in terms you can read. People are referenced by **name**, never by UUID.

```toml
# config.toml
server = "http://immich.example.lan:2283"   # the API key comes from the environment
auto-update-schedule = "daily 03:30"        # default for albums that set none
timezone = "Europe/Rome"
```

```toml
# albums.d/italy-2019.toml
name = "Italy 2019"
auto-update-schedule = "weekly sun 04:00"   # optional; omitted = the global default

[match]
from = 2019-07-01
to   = 2019-07-21
countries = ["Italy"]
people = ["Family Example"]                 # person names and/or group names
include_unlocated = true                    # photos in the window that have no GPS
```

```toml
# albums.d/photos-of-alex.toml — a person album
name = "Photos of Alex"
sync = "mirror"                             # keep the album exactly in sync

[match]
people = ["Alex"]
```

```toml
# groups.toml
["Family Example"]
members = ["Alex", "Sam", "Robin"]
```

Schedules read as English, not cron: `daily 03:30`, `weekly sun 04:00`,
`monthly 1 05:00`, `every 6h`, or `manual`.

See [`examples/`](examples/) for complete files.

## Usage

```sh
immich-album-butler run                     # daemon: follow every album's schedule
immich-album-butler run --once --dry-run    # show what would change, change nothing
immich-album-butler run --once italy-2019   # update one album now
immich-album-butler design                  # web UI on http://127.0.0.1:8081
```

Configuration directory: `--config-dir`. The API key comes from `IMMICH_KEY`
(the design UI's password from `UI_PASSWORD`), so no secret is ever stored in
a config file or visible in `ps`.

## Safety

The butler writes **albums only**. It never touches your originals, never
deletes assets, and never deletes an album. It only *adds* assets to albums —
unless an album is explicitly set to `sync = "mirror"`, which also removes
assets from **that album** when they stop matching. Nothing ever leaves your
library.

## License

MIT — see [LICENSE](LICENSE).
