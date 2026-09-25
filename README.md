<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/engelchrisi/immich-album-butler">
    <img src="immich_album_butler/design/static/icon.svg" alt="Logo" width="200">
  </a>

  <h3 align="center">immich-album-butler</h3>

  <p align="center">
    A butler that keeps your Immich albums in order: by people, places and dates
    <br />
    <a href="https://immich.app/"><strong>Explore immich »</strong></a>
  </p>
</div>

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
only while you are using it:

```sh
sudo systemctl start immich-album-butler-design
```

It shuts itself down after `design_idle_minutes` (default 30) of nobody using it.
It listens on the LAN, so make a login first — see [Starting it](#starting-it).

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
  from = 2019-07-01T14:32:10                # the first photo; a plain date = whole day
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

### Marking the butler's albums

Immich albums have no tags, so the only place a marker can show up in its UI is
the name. `album_suffix` appends one to every album the butler owns:

```toml
album_suffix = "[AB]"     # "Italy 2019" becomes "Italy 2019 [AB]" in Immich
```

Off by default. With it on, a new album is *created* with the suffix, which
needs no extra permission; an album that already exists is **renamed**, which
needs `album.update` — without it the album keeps its old name and keeps
working, and the run says so.

Albums are found by remembered id first, then by name **with or without** the
suffix, so turning the marker on or off never produces a second copy of an
album, and a rebuilt machine with no state file still finds them.

Two optional settings mark fixed and self-updating albums differently:

```toml
album_suffix_fixed    = "◆"   # schedule "manual":      "Italy 2019 ◆"
album_suffix_updating = "↻"   # any automatic schedule: "Photos of Alex ↻"
```

Each falls back to `album_suffix` when unset. Changing an album's schedule
renames it to the other marker; it is never duplicated.

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

### Sharing an album with another account

Another account on the same Immich server can be given access to an album, named
the way a person names it — an account name or an e-mail address, never a UUID:

```toml
[albums.photos-of-alex]
name       = "Photos of Alex"
share_with = ["Sam"]        # or ["sam@example.com"]
share_role = "viewer"       # "viewer" (default) may look; "editor" may also change
```

Albums the butler has **no rule for** — the hand-made ones, usually the
majority — are shared with a `[[shares]]` block instead. It only ever hands out
access: it never creates, fills or renames an album.

```toml
[[shares]]
albums = ["Holiday snaps", "Birthdays"]   # or ["*"] for every album you own
with   = ["Sam"]
role   = "viewer"
```

Two things the butler deliberately does **not** do:

- **It never takes access away.** Removing a name from `share_with` or from a
  `[[shares]]` list leaves that account's access alone, because an edited config
  file is a poor reason for somebody to lose sight of an album.

  So un-sharing is **two steps, in this order**: revoke in the Immich UI first
  (open the album, remove the account from the people it is shared with), *then*
  take the name out of the config. Done the other way round, the name is still
  listed when the next run comes along and the access goes straight back.
- **It cannot share people.** Immich has no per-person sharing; a person belongs
  to one account. What it does have is a *cluster group*, which makes faces
  recognised across the accounts in it — a one-time invitation in Immich's
  sharing settings, not something this tool does. A rule per person, shared, is
  the browsable substitute.

Sharing needs **`user.read`** (to resolve a name to an account) and
**`albumUser.create`** on the API key, plus `albumUser.update` if you change a
role later. Without them the albums still fill and the run says what is missing.

Design mode rewrites this file when it saves. Values survive — the login hashes
included — but comments you add do not.

See [`examples/config.toml`](examples/config.toml) for a complete file.

## Usage

```sh
immich-album-butler run                     # daemon: follow every album's schedule
immich-album-butler run --once --dry-run    # show what would change, change nothing
immich-album-butler run --once italy-2019   # update one album now
immich-album-butler design                  # web UI on http://127.0.0.1:8081 (this machine only)
immich-album-butler design --host 0.0.0.0 --port 9000  # reachable from the LAN, port 9000 (needs a login)
immich-album-butler passwd alex             # make a design-mode login
```

Configuration directory: `--config-dir`. The API key comes from `IMMICH_KEY`,
so it is never stored in a config file and never visible in `ps`. How to start
design mode, on a server or by hand, is under [Design mode](#starting-it).

## Design mode

A small web UI for building album rules, started when you want it and stopped
again by itself after `design_idle_minutes` of nobody using it.

### Starting it

Design mode needs the same three things as a run: the config directory, the
state directory, and `IMMICH_KEY` in the environment. Pick the way that matches
how you installed the butler.

**On a server installed with `deploy/install.sh`** — the unit already has all of
that set up and listens on every interface (`--host 0.0.0.0`):

```sh
sudo systemctl start immich-album-butler-design     # then open http://<server>:8081
sudo systemctl stop immich-album-butler-design      # when you are done
journalctl -u immich-album-butler-design -n 20      # the log line names the address
```

**By hand** — on your own machine, or to try it without systemd. The install
script copies the package but does not put an `immich-album-butler` command on
the `PATH`, so call it as a module (global options such as `--config-dir` go
*before* `design`):

```sh
export IMMICH_KEY=...                                # your Immich API key
python3 -m immich_album_butler --config-dir ./cfg --state-dir ./state design
```

Once the package is pip-installed, `immich-album-butler` replaces
`python3 -m immich_album_butler`.

**Opening it.** Opening the URL does not start design mode — start it first (the
unit or the command above), then browse to it:

| Started as | URL |
|---|---|
| the systemd unit | `http://<server-ip>:8081` |
| by hand, defaults | `http://127.0.0.1:8081` (only from the same machine) |
| by hand, `--host 0.0.0.0 --port 9000` | `http://<machine-ip>:9000` |

The startup log line prints the exact address (`design mode on http://...`). If
the page does not load, the process is not running or a firewall is in the way.

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Address to listen on. Loopback means only this machine can connect; `0.0.0.0` means anything that can reach it |
| `--port` | `8081` | Port. Also settable as `design_port` in `config.toml`; the option wins |
| `--config-dir` | `/etc/immich-album-butler` | Where `config.toml` lives (also `BUTLER_CONFIG_DIR`) |
| `--state-dir` | `/var/lib/immich-album-butler` | Where `state.json` and the trip-scan cache live (also `BUTLER_STATE_DIR`) |

The listening address is printed at startup. It stops by itself after
`design_idle_minutes` (default 30, `0` = never) without a request, or on Ctrl+C.

**Before you expose it beyond your own machine, make a login first.** Design
mode refuses to listen on anything but loopback until `config.toml` has a
`[[design.users]]` entry, since it can show your photos:

```sh
python3 -m immich_album_butler passwd alex      # asks twice, prints a block; needs a terminal
# paste the printed [[design.users]] block into config.toml, then start design mode
```

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

### API key permissions

The butler needs some permissions on its Immich API key. The **required** ones
run on every update; the **optional** ones depend on which features you use:

| Permission | Purpose | Needed for |
|---|---|---|
| `asset.read` | **required** | Search and fetch asset metadata and thumbnails |
| `album.read` | **required** | List your albums |
| `album.create` | **required** | Create new albums |
| `albumAsset.create` | **required** | Add assets to albums |
| `album.update` | optional | Rename with `album_suffix`, set `cover` |
| `user.read` | optional | Resolve account names for `share_with` |
| `albumUser.create` | optional | Share albums with other accounts |
| `albumUser.update` | optional | Change a shared account's role later |
| `albumAsset.delete` | optional | Remove assets when `sync = "mirror"`, or outside an exact first/last photo, or duplicates from the Duplicates tab |
| `duplicate.read` | optional | List Immich's duplicate groups for the Duplicates tab |

Without an optional permission, that feature fails gracefully: the album still
fills correctly, and the run reports what is missing.

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

### Duplicates

The **Duplicates** tab starts with an overview: every album you own that holds
two or more members of one of Immich's duplicate groups (Utilities → Duplicates
in Immich), with how many copies could go, most first. The scan is cached in
the state directory and redone on the first visit each day or with **Rescan**.

Opening an album lists its groups. Each preselects the earliest-taken copy to
keep; pick another with its **keep** radio. **Remove N duplicates from album**
takes the others out of that album only; they stay in your library and in any
other album. An album a scheduled rule keeps filling (↻) is flagged: its rule
still matches the removed copies, so the next run adds them back. Needs
`duplicate.read` and `albumAsset.delete` on the key.

## Safety

The butler writes **albums only**. It never touches your originals, never
deletes assets, and never deletes an album. It only *adds* assets to albums —
unless an album is explicitly set to `sync = "mirror"`, which also removes
assets from **that album** when they stop matching — and unless the album's
`from` or `to` is an exact first or last photo: then media taken before the
first or after the last are removed from the album, so moving either end in or
out trims or extends it. The design UI's **Duplicates** tab removes extra
copies of an Immich duplicate group from an album, only when you confirm it and
always keeping one. Nothing ever leaves your library.

## License

MIT — see [LICENSE](LICENSE).
