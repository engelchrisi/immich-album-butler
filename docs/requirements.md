# Requirements

What immich-album-butler must do. Numbers are stable; new requirements get the next free number.
Priority: P1 = needed, P2 = wanted.

## 1. Functional

### 1.1 Rules and albums
| # | Requirement | Prio |
|---|---|---|
| N1 | An album is described by a rule: people (by name), places, date window, and how people combine (`people_mode`). The rule lives in `config.toml`; no UUID ever appears there | P1 |
| N2 | People can be grouped (`[groups.*]`) and a group used wherever a person is | P1 |
| N3 | Places are an OR with "no place at all" when `include_unlocated` is set; the server API only ANDs, so places are filtered client-side | P1 |
| N4 | The Immich album id and last-run time per album are kept in a state file, not in the config | P1 |
| N5 | An album mirrors its rule: an asset that stops matching is removed from the album when it stops matching (never from the library) | P1 |
| N6 | An album cover can be chosen by rule (`everyone`, `newest`, …) or by original file name | P2 |
| N7 | An album can be shared with any number of accounts, each with its own role, from the config. The role names what a *new* share is granted with; once an account has access, later runs never correct its role again, even if the config changes it (`[[shares]]`, a separate feature for albums with no rule, does keep every account's role in sync) | P2 |
| N8 | Albums the butler owns are marked so they can be told from manual ones | P2 |
| N28 | **A rule may name a recurring calendar window.** `on_from`/`on_to` ("MM-DD") match every year from `since_year` to now; a single day is `on_from == on_to` (the default when `on_to` is omitted), and the window may be asymmetric or wrap across New Year (`on_to` earlier than `on_from`). It is queried as one windowed search per year, not by filtering the library client-side, and it composes with `people`, `countries`, `states` and `cities`. `on_from` together with `from`/`to` is refused at load, and a rule with only `on_from` is not "empty". | P2 |
| N29 | **An album may be capped per year, and its contents may rotate.** `pics_per_year` caps how many of each calendar year's matched assets the album holds; `pick` chooses which — `all` (today's behaviour), `best` (favourites and ratings first, stable between runs), `random` (a fresh sample each run, repeats possible), or `rotate`, which prefers assets not used in recent runs so that repeated runs work through the matched set instead of repeating one sample. The rotation bag lives in `state.json` (additively; the file stays version 1), is advanced only by `apply()` so a dry run spends nothing, and is seeded deterministically so the design preview shows what the run will do. Every album already mirrors its rule (N5), so a cap is simply more removal; `rotate` additionally needs a non-manual schedule, and a cadence under a day is warned about. | P2 |
| N30 | **Rotation is honest about what it costs.** Each run removes and re-adds album members, which shows in Immich's activity feed and can leave a client that caches album contents up to its own refresh interval behind. Assets that stop matching leave the bag and new ones join it, so the cycle carries on rather than restarting. Nothing is ever deleted from the library (N22). | P2 |
| N31 | **An album's play hint is published in its description.** An album with a `chunk` (`<n>/<span>`: n photos from one `year`, `month` or `day`, n from 1 to 50) and an optional `chunk_order` (`chronological`, the default, or `random`) gets one `[butler v1] chunk=… chunk_order=…` line in the Immich album description, and nothing else is ever written to it -- not the album's kind, and not when to show it. An album without a `chunk` has no line, and one the butler wrote earlier is removed. Both values are a closed vocabulary (`describe.py`), refused at load with the allowed list named; `chunk_order` without `chunk` is refused. The butler owns only that line -- the last one matching the marker -- and leaves every other line of a hand-written description verbatim. It is written only when it would change, it rides in the plan so a dry run shows it, and a missing `album.update` scope is a warning rather than a failed run. The reference for a reader is `docs/hints.md`. | P2 |
| N32 | A rule may exclude videos and keep images only (`include_videos = false`, default `true`) | P2 |
| N35 | ~~An album's `kind`/`order` hint can be overridden.~~ **Removed in 1.0.0:** the line no longer carries a kind or an order; see N31. `hint_kind` and `hint_order` are refused at load. | P2 |
| N36 | ~~An album may carry extra playback hints.~~ **Removed in 1.0.0:** `slot`, `dwell`, `active`, `caption` and `activity` are gone from the line and refused at load, naming the key; see N31. | P2 |
| N37 | **Backup.** `backup` (CLI) and the Backup tab save one JSON file per run, in `<state-dir>/backups/`: every album the account owns with its description, cover, sharing and, per asset, the id, checksum, original path and a few EXIF fields -- no image data -- plus `config.toml` verbatim and the slug-to-album-id map from `state.json`. The file holds the login hashes, so it is written 0600 and treated as a secret. | P1 |
| N38 | **Restore only adds.** A missing album is created (description, cover and sharing included); an existing one only gains the assets it lacks. Restore never removes an asset from an album, never deletes an album or a library asset (N22). An asset whose id Immich no longer knows is found again by checksum; one found nowhere is reported, not fatal. `state.json` is pointed at recreated albums. `--dry-run` / the UI preview show the plan and write nothing. | P1 |
| N39 | **Config restore is opt-in.** `restore --config` (UI: a checkbox) puts the backed-up `config.toml` back, only if it parses, after keeping the current one as `config.toml.bak`. | P1 |
| N40 | **Backups are reachable at `/backup`** in design mode (own bookmarkable URL). The tab reads top to bottom: *Back up* (shows the directory the files go to), *Saved backups* (newest first by creation time; tick several to delete them, after confirmation -- only `backup-*.json` files in the backup directory, never anything in Immich), *Restore* (choose a backup, choose one album or all and whether to restore the Butler settings, preview, then restore after confirmation; "Restore now" is only enabled after a preview of the same choice). | P2 |

### 1.2 Runtime mode
| # | Requirement | Prio |
|---|---|---|
| N9 | Headless daemon, no UI, no open port; updates each album on its own schedule | P1 |
| N10 | Schedule grammar is English-shaped: `daily 03:30`, `weekly sun 04:00`, `monthly 1 05:00`, `every 6h`, `every 30m`, `manual` | P1 |
| N11 | `run --once`, `run --once <album>` and `run --dry-run` exist; a dry run changes nothing | P1 |
| N12 | One album's failure never stops the others | P1 |
| N13 | `check` validates the configuration and exits | P1 |

### 1.3 Design mode
| # | Requirement | Prio |
|---|---|---|
| N14 | A web UI to explore people, places and trips, build rules with a live preview, and save them to `config.toml` | P1 |
| N15 | Preview builds the same plan as a real run and never writes | P1 |
| N16 | Saving the config is the only write to disk besides backups (N37), deleting backups on request (N40) and a requested config restore (N39); every write is atomic | P1 |
| N17 | Trips are detected from geotagged photos far from home; unlocated photos join by date window | P2 |
| N18 | Analyze suggests near-misses for a rule (adjust the rule or add assets) and never edits by itself | P2 |
| N19 | A Duplicates tab; removal from an album only on explicit confirmation and never the last copy | P2 |
| N20 | Login is mandatory: scrypt hashes in `config.toml`, produced by `passwd`; no default account; without a user it binds to loopback only | P1 |
| N21 | Design mode stops itself after `design_idle_minutes` idle | P2 |
| N33 | The Albums page lists every Immich album this key can see, not only the ones with a rule: each is tagged "manual", "scheduled" or "normal Immich album", and can be filtered by that kind as well as by name. A "View" button opens that album's media grouped by folder, date, camera, place or kind (replacing the separate Browse tab); an unmanaged album only offers View, since a new rule with the same name already extends the existing album instead of duplicating it | P2 |
| N34 | A cover picture can be set by hand, from the album viewer, for any album this key can see — butler-managed or not; it needs `album.update` like every other cover write and sticks until a rule next overrides it | P2 |
| N41 | **Save as.** The builder can save a saved album's rule under a new name as a new album; the original rule and its Immich album are untouched. A name another rule already uses is refused | P2 |
| N42 | **Builder URLs.** `/builder` (e.g. from the menu) opens an empty new-album form, or the unsaved draft started from *New album* or a trip; only `/builder/<slug>` (Edit on the Albums page) shows a saved album | P2 |

## 2. Safety
| # | Requirement | Prio |
|---|---|---|
| N22 | Writes albums only: never deletes assets, never modifies originals, never deletes an album, never `DELETE /assets` | P1 |
| N23 | The API key comes from the environment (`IMMICH_KEY`), never a flag, a config file, a log or a browser | P1 |

## 3. Non-functional
| # | Requirement | Prio |
|---|---|---|
| N24 | Python 3.11+, standard library only; installable by copying files | P1 |
| N25 | No site-specific defaults; server, time zone and paths come from configuration | P1 |
| N26 | Public repository: no private data in any tracked file or commit message, enforced by `scripts/check-no-private-data.py` | P1 |
| N27 | Tests run against a fake Immich on a real socket, not mocks; the suite needs no real server | P1 |

## 4. Known exceptions to stdlib-only
None.
