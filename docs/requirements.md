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
| N5 | `sync = "mirror"` removes assets from that album when they stop matching; the default only adds | P1 |
| N6 | An album cover can be chosen by rule (`everyone`, `newest`, …) or by original file name | P2 |
| N7 | An album can be shared with another account, with a role, from the config | P2 |
| N8 | Albums the butler owns are marked so they can be told from manual ones | P2 |

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
| N16 | Saving the config is the only write to disk; it is atomic | P1 |
| N17 | Trips are detected from geotagged photos far from home; unlocated photos join by date window | P2 |
| N18 | Analyze suggests near-misses for a rule (adjust the rule or add assets) and never edits by itself | P2 |
| N19 | A Duplicates tab and a Browse tab; removal from an album only on explicit confirmation and never the last copy | P2 |
| N20 | Login is mandatory: scrypt hashes in `config.toml`, produced by `passwd`; no default account; without a user it binds to loopback only | P1 |
| N21 | Design mode stops itself after `design_idle_minutes` idle | P2 |

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
