# Changes

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
