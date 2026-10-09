# Builder UI simplification

**Done (0.12.0, templates extended afterwards).** Shipped as planned except:
- No `⋯` overflow menu — Analyze/Delete config stayed as plain buttons in the action bar
  (dropped for risk/payoff, not worth the extra component for two buttons).
- "Cover: a picture I name…" was **not** removed from the select (kept as-is): dropping it
  would have blocked choosing a named cover on a *new* rule, which looked like a net loss.
- Album templates are quick-fill buttons on a new, empty draft rather than a separate
  full-screen picker, but each one now has its own bookmarkable URL
  (`/builder/new/<template>`, applied by `route()`) as the plan asked. All 11 planned
  templates are in: Trip (opens the Trips tab), Birthday over the years (reads the person's
  birth date from Immich), Christmas, New Year's Eve, Summer, A place, Year in review,
  Person album, A person over the years, Two people together, Anniversary.
- The When help text stays always visible (shortened, mentions `yyyy`) rather than only
  showing when a recurring day is typed — simpler, same value.

## Context
Builder tab (`immich_album_butler/design/static/index.html`, `#builder`) is dense: nested
boxes, long always-visible help, rarely used fields at the same weight as the core rule
(name, Who, When, Where). Decided: **layout L3 (sub-tabs)** + feature trims + album
templates (below). Config format unchanged → minor bump.

## Layout — L3 sub-tabs (chosen)
```
 Album name [Italy 2019________]
 [Rule] [Options •] [Sharing 2] [Player]  │ ┌ preview ───┐
 ─────────────────────────────────────────│ │ 123 photos │
 WHO   [search…] (any ▾)  [Alex×]         │ │ ▢▢▢▢▢▢     │
 WHEN  From [______] To [______] ×        │ └────────────┘
 WHERE [cty▾][st▾][city▾]  ☑ no GPS        │ [Save][Save as…][Dry run][Run now][⋯]
                                          │  run-result · analysis
```
- Album name + sub-tab bar above the tabs, always visible; preview + actions in the sticky
  right column on every sub-tab. `⋯` = Analyze, Delete config.
- Sub-tabs:
  - **Rule** — Who, When, Where (+ no-GPS checkbox).
  - **Options** — Cover, Schedule (+ next run), Pick, Keep per year, Include videos.
  - **Sharing** — account search, share list, one-line note.
  - **Player** — hints Kind, Order, Dwell (+ "also set in config" line).
- Tab badges replace the `<details>` summaries: `•` when the tab holds non-default values,
  a count on Sharing. Hover title = one-line summary (e.g. "Cover: favourite · Sched: manual").
- URL: `/builder/<rule>/<subtab>` (and `/builder/new/<subtab>`), so each sub-tab is
  bookmarkable; default sub-tab = Rule. Reuse the existing tab-routing code of the main menu.
- Phone width: tab bar scrolls horizontally inside itself; preview column drops below.

## Other layouts considered (not chosen)

### L1 Single column
Preview + actions sticky on top, form below. Removes the half-empty right column.
```
┌ preview ─────────────────────── [Save][Save as…][Dry run][Run now][⋯] ┐
│ 123 photos  ▢▢▢▢▢▢▢▢                                                    │
└─────────────────────────────────────────────────────────────────────────┘
 Album name [______________________]
 WHO   [search…        ] (any ▾)  [Alex×]
 WHEN  From [________] To [________] ×
 WHERE [country▾][state▾][city▾]  ☑ no GPS
 ▸ Options  Cover: fav · Sched: manual · Pick: all · Videos: yes · Shared: nobody
 ▸ Player hints  auto
 analysis…
```
+ simplest, best on phone. − wide screens show long thin inputs.

### L2 Form + preview rail
Two columns kept; right column holds only preview, actions, result, analysis.
```
 Album name [__________]   │ ┌ preview ────────┐
 WHO   [search…] (any ▾)   │ │  123 photos     │
 WHEN  [From] [To] ×       │ │  ▢▢▢▢▢▢         │
 WHERE [cty▾][st▾][city▾]  │ └─────────────────┘
 ▸ Options  Cover:fav · …  │ [Save][Dry][Run][⋯]
 ▸ Player hints  auto      │  run-result
                           │  analysis…
```
+ closest to today, smallest change. − right column still mostly empty.

### L4 Core row
Who / When / Where as three cards side by side; preview as a strip below.
```
 Album name [____________________] [Save][Dry][Run][⋯]
 ┌ WHO ──────────┐┌ WHEN ────────┐┌ WHERE ───────┐
 │[search…]     ││From [______] ││[country ▾]   │
 │(any ▾) Alex× ││To   [______] ││[state ▾]     │
 └──────────────┘└──────────────┘│☑ no GPS      │
                                 └──────────────┘
 ┌ preview ▢▢▢▢▢▢▢▢▢▢▢▢  123 photos ──────────────┐
 ▸ Options  Cover: fav · Sched: manual
 ▸ Player hints  auto
```
+ whole rule visible at a glance. − cards stack on phone; WHO chips can overflow.

## Album templates (proposal)
"New album" opens a template picker first; a template fills `state.draft`, asks at most one
question (person or date), then lands in the normal builder for tweaks. Client-side presets
only; URL `/builder/new/<template>` (bookmarkable). "Blank" keeps today's empty form.

```
 New album from…
 ┌ Trip ──────┐┌ Birthday ──┐┌ Christmas ─┐┌ Person over ┐┌ Person ────┐┌ Blank ─────┐
 │ → Trips tab││ pick person││ 24–26/12   ││ the years   ││ all photos ││ empty form │
 └────────────┘└────────────┘└────────────┘└─────────────┘└────────────┘└────────────┘
 more: New Year's Eve · Summer · A place · Year in review · Two together · Anniversary
```

| Template | Asks | Fills |
|---|---|---|
| Trip | — | opens Trips tab (existing) |
| Birthday over the years | person | people=[p], recurring day = Immich `birthDate` (dd/mm; ask if unset), pick best, keep/yr ~10, hint recurring-day / one-per-year, name "Birthday of Alex" |
| Christmas over the years | — | recurring 24/12–26/12, no people, pick best, keep/yr ~15, active 12-01..12-31, name "Christmas" |
| Person over the years | person | people=[p], no date, pick best, keep/yr ~5, order one-per-year, name "Alex over the years" |
| Person album | person | people=[p], pick all, kind person, name "Photos of Alex" |
| New Year's Eve | — | recurring 31/12–01/01 (verify window wrapping over year end is supported) |
| Summer | — | recurring 21/06–22/09, pick best, keep/yr |
| A place | place | country/state/city, kind place, name = place |
| Year in review | year | `yyyy`, pick best, cap ~100 total (needs total cap or keep/yr) |
| Two together | 2+ people | people_mode all, name "Alex & Sam" |
| Anniversary | date + name | recurring dd/mm, pick best, keep/yr |

Notes:
- Birthday needs `birthDate` from Immich people — add it to `/api/people` response
  (not read anywhere today).
- Moving feasts (Easter) not expressible with fixed dd/mm — out of scope.
- Templates may set fields hidden by the hints trim (e.g. `active`); they stay in the draft.
- Defaults (keep/yr values) are suggestions — tune when implementing.

## Changes (decided)
1. **Layout** — L3 sub-tabs as above. Keep `.split` / `.sticky-col`; left column becomes
   album name + tab bar + four sub-tab panels.
2. **Flatten** — no outer "Options"/"Optional" fieldsets; each sub-tab holds its controls
   directly, Album/Schedule as small headings inside Options. Badges recomputed from
   `state.draft` wherever the draft loads or a field changes. Opening a saved rule always
   lands on Rule (unless the URL names a sub-tab).
3. **Date presets + help** — remove the presets row and its handler (`[data-preset]` in
   `app.js`, `shiftDays` if unused, the preset line in `updateWhenExclusivity`). Replacement:
   `parseDateField` accepts `yyyy` (kind `year`); a `yyyy` or `mm/yyyy` in From with To empty
   spans just that year/month. Small × clear button in the When row. Static help → shown only
   when `on_from` is set. Update placeholder + error text to include `yyyy`.
4. **Sharing text** — 6-line paragraph → one line ("Only grants access, never revokes or
   changes it — do that in Immich.") + link to the docs section.
5. **Player hints trim** — UI keeps Kind, Order, Dwell. Slot/Active/Caption/Activity removed
   from markup and handlers, but **kept in `state.draft`** when loaded, so Save never drops
   them; show a muted "also set in config: slot=3 caption=year" line when present.
6. **Small ones**
   - Keep per year: hidden (not just disabled) when Pick = all.
   - Cover "a picture I name…": removed from the select; a loaded named cover still shows as
     a single extra option with that file name so it round-trips.
   - Include videos: moves from Where into Options.

## Files
- `immich_album_butler/design/static/index.html`, `style.css`, `app.js`
- `docs/requirements.md` — N6 note (named cover via config only), date input rule (`yyyy`, lone From)
- `immich_album_butler/design/api.py` — `birthDate` in the `/api/people` response (templates)
- `docs/design.md` — UI section; `changes.md`; `pyproject.toml` minor bump

## Verification
- `python scripts/run-tests.py`
- Run the design UI locally (no deploy):
  - new rule opens on Rule; badges appear when Options/Sharing/Player get non-defaults;
    preview/actions visible on every sub-tab
  - each sub-tab URL reloads to the same sub-tab; browser back/forward switches sub-tabs
  - `2019` → whole year; `06/2019` alone → June; `17/05` shows the recurring help
  - load a rule with slot/caption + named cover → Save → config file unchanged (diff)
  - phone width: no horizontal scroll
  - each template: `/builder/new/<template>` opens prefilled; Birthday uses the person's
    Immich birth date or asks; Save writes the expected rule
