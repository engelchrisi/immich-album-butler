# Changes

## 0.14.1 — 2026-10-09
- Design UI: the template picker in the builder is labelled "Template:" instead
  of "Start from:"; a new "New album" button in the builder's action bar
  discards the current draft and starts a blank one without leaving the
  builder.

## 0.14.0 — 2026-10-09
- Design UI: the builder's album templates each get their own bookmarkable
  URL (`/builder/new/<template>`), and four more are added — Trip (opens the
  Trips tab), A place, Year in review, Anniversary — alongside the earlier
  Birthday over the years, Christmas, New Year's Eve, Summer, Person album, A
  person over the years and Two people together.
- Design UI: the "inherit the global default" schedule option now shows just
  the resolved value (e.g. "manual"), not the explanation of where it comes
  from.
- `examples/config.toml`: dropped the explicit `auto-update-schedule =
  "manual"` line — the same value as the built-in default, so it was
  redundant.

## 0.12.0 — 2026-10-09
- Design UI: the builder is simplified. Who/When/Where, Options, Sharing and
  Player are now sub-tabs with their own bookmarkable URL
  (`/builder/<album>/<rule|options|sharing|player>`) instead of one long
  stacked form; a folded-away tab shows a dot and a hover summary when it
  holds anything other than the defaults.
- Design UI: the date fields accept a bare `yyyy` for a whole year (or two
  different years for a span of years), replacing the "whole year"/"whole
  month"/"± 1 day" preset buttons with one "clear".
- Design UI: the Player tab keeps Kind, Order and Dwell as controls;
  `slot`/`active`/`caption`/`activity`, if set by hand in config.toml, still
  round-trip through Save and are listed as "also set in config: …".
- Design UI: Include videos moved from Where (it is not a place filter) to
  Options; Keep per year is now hidden, not just disabled, when Pick is
  "all"; the Sharing tab's explanation is one line with a "why?" detail.
- Design UI: a new, empty album offers quick-fill templates (Birthday over
  the years, Christmas, New Year's Eve, Summer, Person album, A person over
  the years, Two people together). Birthday reads the person's birth date
  from Immich (`Person.birth_date`, `/api/people`'s new `birth_date` field)
  when Immich has one set.

## 0.11.2 — 2026-10-09
- Design UI: the builder's right column (preview, actions and Optional) now
  sticks and scrolls as one unit on wide screens, so Optional no longer
  slides under the preview.

## 0.11.1 — 2026-10-09
- Design UI: opening the builder from the menu (`/builder`) shows an empty
  new-album form instead of the last edited album; only Edit on the Albums
  page (`/builder/<slug>`) opens a saved album. The album name field no
  longer shows a sample name as placeholder.

## 0.11.0 — 2026-10-09
- Design UI: "Save as…" in the builder saves the current rule as a new album
  under a new name; the album it was opened from stays as saved. A name
  another rule already uses is refused (`POST /api/albums` with `new: true`).

## 0.10.0 — 2026-10-08
- Design UI: the Backup tab is split into three sections, read top to bottom:
  *Back up* (shows the directory backups are written to), *Saved backups* and
  *Restore* (1. choose a backup, 2. choose one album or all and whether to
  restore the Butler settings, 3. preview, then restore). "Restore now" only
  unlocks after a preview. The settings checkbox now says what it changes.
- Saved backups are listed newest first and can be deleted several at once
  (`POST /api/backups/delete`; only `backup-*.json` files, never Immich).
- `backups` prints the backup directory.

## 0.9.1 — 2026-10-08
- Design UI: fix a JavaScript syntax error in the Backup tab (unescaped line
  breaks in `app.js`) that stopped the whole page script from loading.

## 0.9.0 — 2026-10-08
- Album backup and restore: `backup`, `backups` and `restore` commands and a
  Backup tab (`/backup`) in design mode. A backup is one private JSON file with
  every owned album's metadata and assets (id, checksum, path, EXIF — no
  images), `config.toml` and the album-id map. Restore only adds: it recreates
  missing albums (cover and sharing too), extends existing ones, finds
  re-imported assets by checksum, and optionally restores `config.toml`
  (keeping `config.toml.bak`). `--dry-run` previews.

## 0.8.1 — 2026-10-08
- Design UI: editing an album in the Builder now gets its own URL
  (`/builder/<slug>`) too — it was missed in 0.8.0, which only gave the
  Albums/Builder/Trips/Duplicates tabs and the opened-album viewer their own
  URLs.

## 0.8.0 — 2026-10-08
- Design UI: every page (Albums, Builder, Trips, Duplicates, an opened album)
  now has its own URL path and can be bookmarked; back/forward works between
  them.

## 0.7.0 — 2026-10-08
- Design UI: Builder defaults changed — Include Videos now off, cover
  defaults to a favourited picture (else Immich's own pick). Starting a
  build from the Trips page defaults both description-hint kind and order
  to "trip".

## 0.6.3 — 2026-10-06
- Design UI: album tiles show the type tag beside the name, and "Last run" is
  a short readable timestamp (the run summary is on hover). The cover fills
  the height of the rows, so no gap is left below it.
- Design UI: the log records the UI files on disk at startup and the hash of
  each one served, so a stale page can be told apart from a stale deploy.

## 0.6.2 — 2026-10-06
- Design UI: Albums page tiles redesigned. Cover on the left, labelled rows
  (Rule, Schedule, Last run / Error) on the right, a status dot (OK, failed,
  disabled, not run), extras (shares, picks, cover) as chips, and the actions
  in a footer with View on the right. Long rules and errors are clamped, with
  the full text on hover.

## 0.6.1 — 2026-09-30
- Design UI: split the builder's Options group — Album/Schedule/Sharing stay
  on the left, Description hint moved into a new "Optional" group on the
  right, under the preview.
- Design UI: editing a Description hint field (Kind, Order, Slot size, Dwell,
  Active window, Caption, Activity) no longer re-runs the full Immich match
  and recount; a new `/api/hint-preview` recomputes just the description
  line, since those fields never affect which media match.

## 0.6.0 — 2026-09-30
- Album hints (N35/N36): per-album `hint_kind`/`hint_order` overrides and
  `slot`/`dwell`/`active`/`caption`/`activity` playback fields, a closed
  vocabulary enforced at config load, design-mode save and `hint_line()`
  itself, editable in the album builder with a live preview. See
  `docs/hints.md`.
- Design UI: rebalanced the album builder's two columns — Options moved to
  the right column under the preview/actions, which now stay visible while
  scrolling (sticky) on wide screens.

## 0.5.0 — 2026-09-29
- Baseline version reset; adopted semantic versioning (see CLAUDE.md).
