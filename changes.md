# Changes

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
