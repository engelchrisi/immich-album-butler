# immich-album-butler

A butler that keeps your [Immich](https://immich.app) albums in order: by
people, places and dates.

> **Built entirely by [Claude](https://claude.com/claude-code).** Every line of
> code, test and document in this repository was written by Claude, working
> from a human's requirements and review. Worth knowing before you trust it
> with your library — read it as you would any code from a stranger.

> Unofficial third-party tool. Not affiliated with or endorsed by the Immich
> project.

Albums in Immich are manual. This tool makes them **rules** instead: you
describe an album once — who is in it, where it was, when it was — and the
butler keeps it filled as new photos arrive.

It has two modes:

- **Design mode** — a small web UI to explore your library, detect trips,
  build album rules with live previews, and save them to the config file.
- **Runtime mode** — a headless daemon with no UI and no open port. It reads
  that file and updates the albums on a schedule.

Design mode writes the configuration; runtime mode consumes it. You can run
runtime mode alone, and start design mode only when you want to change
something.

## Status

Early development, but usable: the core (config, scheduling, matching,
runtime) and design mode are both in place.

## Requirements

- An Immich server and an API key
- Python **3.11+**, standard library only — no pip packages, no Docker

## Installing

There is nothing to build and nothing to download at install time — the whole
package is standard library, so installing it is copying it into place.

```sh
git clone https://github.com/engelchrisi/immich-album-butler
cd immich-album-butler
sudo deploy/install.sh
```

That creates a system user, `/etc/immich-album-butler`,
`/var/lib/immich-album-butler`, and two systemd units. Then set `server` in
`config.toml`, put your API key in `immich-album-butler.env`, and:

```sh
sudo systemctl enable --now immich-album-butler
```

The design-mode unit is installed but deliberately **not** enabled: start it
only while you are using it.

## Configuration

**One file**, `/etc/immich-album-butler/config.toml` by default: the global
settings, the design-mode logins, the person groups and every album rule. TOML,
in terms you can read. People are referenced by **name**, never by UUID.

```toml
server = "http://immich.example.lan:2283"   # the API key comes from the environment
auto-update-schedule = "manual"             # default for albums that set none
timezone = "Europe/Rome"

[groups."Family Example"]
members = ["Alex", "Sam", "Robin"]

# A finished trip. No schedule of its own, so it inherits "manual" -- the trip
# is over, and nothing new will ever match it.
[albums.italy-2019]
name = "Italy 2019"

  [albums.italy-2019.match]
  from = 2019-07-01
  to   = 2019-07-21
  countries = ["Italy"]
  people = ["Family Example"]               # person names and/or group names
  include_unlocated = true                  # photos in the window with no GPS

# A person album, never finished.
[albums.photos-of-alex]
name = "Photos of Alex"
auto-update-schedule = "weekly sun 04:00"   # photos of Alex keep arriving
sync = "mirror"                             # keep the album exactly in sync
cover = "IMG_0042.jpg"                      # the front picture, by file name

  [albums.photos-of-alex.match]
  people = ["Alex"]
```

The key after `albums.` is the album's id on the command line; `name` is what
Immich shows. Schedules read as English, not cron: `daily 03:30`,
`weekly sun 04:00`, `monthly 1 05:00`, `every 6h`, or `manual`.

### Album covers

The picture on the front of an album is a rule too, so the config still holds
no UUIDs:

| `cover` | The front picture becomes |
|---|---|
| `"auto"` (default) | whatever Immich picked — the butler does not touch it |
| `"everyone"` | the matching picture showing **most of the album's people**, and where several show them all, the newest |
| `"newest"` / `"oldest"` | the ends of the album |
| anything else | the original file name of one of the album's own pictures |

`"everyone"` is what a group album wants: one picture with the whole family in
it. It costs nothing extra — the butler already knows which pictures each
person appears in from matching them.

Setting a cover is the **only** thing the butler does that needs the
`album.update` permission on the API key. Without it the album is still filled
correctly and the run reports that the cover could not be set.

Design mode rewrites this file when it saves. Values survive — the login hashes
included — but comments you add do not.

See [`examples/config.toml`](examples/config.toml) for a complete file.

## Usage

```sh
immich-album-butler run                     # daemon: follow every album's schedule
immich-album-butler run --once --dry-run    # show what would change, change nothing
immich-album-butler run --once italy-2019   # update one album now
immich-album-butler design                  # web UI on http://127.0.0.1:8081
immich-album-butler passwd alex             # make a design-mode login
```

Configuration directory: `--config-dir`. The API key comes from `IMMICH_KEY`,
so it is never stored in a config file and never visible in `ps`.

## Design mode

A small web UI for building album rules, started when you want it and stopped
again by itself after `design_idle_minutes` of nobody using it.

- **Builder** — Who / When / Where pickers over one live preview: how many
  assets match, how many are already in the album, a thumbnail strip, and when
  the album would next run. The preview is produced by the same code a real run
  uses, so it cannot show you one thing and then do another.
- **Trips** — scans the library for stretches spent away from home and offers
  each as a ready-made album, marking those an album already covers.
- **Analyze** — the near-misses a rule leaves out: assets shot within hours of
  the album, ones inside the date range without GPS, ones inside the range but
  elsewhere. Each says which rule change would include it, or can be added once
  without changing the rule at all.

### Signing in

Design mode can show thumbnails from your library, so it sits behind a login.
Accounts live in `config.toml` as scrypt hashes — never as passwords:

```sh
immich-album-butler passwd alex     # prompts, then prints the block to paste
```

```toml
[[design.users]]
name     = "alex"
password = "scrypt$32768$8$1$..."   # a hash; a plaintext here is refused
```

The session is an HttpOnly, SameSite=Strict cookie, so you sign in once rather
than on every visit — for the working day, or a month with "stay signed in".
Repeated wrong guesses are locked out. With no accounts configured, design mode
refuses to listen on anything but `127.0.0.1`.

## Safety

The butler writes **albums only**. It never touches your originals, never
deletes assets, and never deletes an album. It only *adds* assets to albums —
unless an album is explicitly set to `sync = "mirror"`, which also removes
assets from **that album** when they stop matching. Nothing ever leaves your
library.

## License

MIT — see [LICENSE](LICENSE).
