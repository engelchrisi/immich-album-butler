# Changes

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
