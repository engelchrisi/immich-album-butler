"use strict";

/* Design mode's UI.
 *
 * One draft object is the single source of truth. Every control writes into it
 * and asks for a preview; the preview is computed by the same code runtime
 * mode runs, so what is shown here is what a run would actually do.
 */

const $ = (id) => document.getElementById(id);

const state = {
  draft: emptyDraft(),
  saved: [],            // albums that exist as config files
  groups: [],
  people: [],           // last search result
  previewToken: 0,
  hintPreviewToken: 0,
  accounts: [],         // other accounts on this server, for sharing
  accountsNote: "",
  extends: null,        // the unmanaged Immich album the preview would take over
};

function emptyDraft() {
  return {
    slug: "", name: "", enabled: true, schedule: "inherit",
    cover: "favorite", share_with: [],   // [{ account, role }]
    pics_per_year: null, pick: "all",
    // N35/N36: description-hint overrides. "" (not null) is "not set" here,
    // so a bare select/text input maps to it directly -- see docs/hints.md.
    hint_kind: "", hint_order: "", slot: "", dwell: null,
    active: "", caption: "", activity: "",
    match: {
      from: null, to: null, countries: [], states: [], cities: [],
      people: [], people_mode: "any", include_unlocated: true,
      include_videos: false,
      on_from: "", on_to: "", since_year: null,
    },
  };
}

/* -- transport ---------------------------------------------------------- */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 401) {
    // The session expired while the tab sat open; go and sign in again.
    location.href = "/login";
    throw new Error("signed out");
  }
  let body = {};
  try { body = await response.json(); } catch (_) { /* empty body */ }
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

const post = (path, payload) =>
  api(path, { method: "POST", body: JSON.stringify(payload) });

function banner(message, ok = false) {
  const box = $("banner");
  box.textContent = message;
  box.classList.toggle("ok", ok);
  box.hidden = !message;
  if (message && ok) setTimeout(() => { box.hidden = true; }, 4000);
}

/* -- tabs & routing ------------------------------------------------------ */

/* Each tab (and the album browser) gets its own bookmarkable URL path,
 * driven by the History API -- no page reload, but back/forward and
 * bookmarks work as expected. */

const TABS = ["albums", "builder", "trips", "duplicates", "backup"];
const SUBTABS = ["rule", "options", "sharing", "player"];

function activateTab(name) {
  for (const el of document.querySelectorAll(".tab")) {
    el.classList.toggle("active", el.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.classList.toggle("active", panel.id === name);
  }
  if (name === "albums") loadAlbums();
  if (name === "trips") loadTrips(false);
  if (name === "duplicates") loadDuplicates();
  if (name === "backup") loadBackups();
}

/* The builder's own sub-tabs (Rule/Options/Sharing/Player) get a path segment
 * of their own, so a particular tab can be bookmarked or linked to. */
function activateSubtab(name) {
  if (!SUBTABS.includes(name)) name = "rule";
  state.subtab = name;
  for (const el of document.querySelectorAll("#builder-subtabs .subtab")) {
    el.classList.toggle("active", el.dataset.subtab === name);
  }
  for (const panel of document.querySelectorAll("#builder .subtab-panel")) {
    panel.classList.toggle("active", panel.dataset.subtab === name);
  }
}

function route(path) {
  const albumMatch = /^\/albums\/([^/]+)$/.exec(path);
  if (albumMatch) return openAlbumView(decodeURIComponent(albumMatch[1]), { push: false });

  const builderMatch = /^\/builder\/([^/]+)(?:\/([^/]+))?$/.exec(path);
  if (builderMatch) {
    const [, rawSlug, second] = builderMatch;
    const slug = decodeURIComponent(rawSlug);
    if (slug === "new") {
      if (second === "trip") return navigate("/trips", { replace: true });
      state.draft = emptyDraft();
      if (second && TEMPLATE_IDS.includes(second)) {
        applyTemplate(second).then(fillForm);
      } else {
        fillForm();
      }
    } else {
      const album = (state.saved || []).find((a) => a.slug === slug);
      if (album) editAlbumDraft(album);
      else { banner(`No album "${slug}" found.`); activateTab("builder"); return; }
    }
    activateTab("builder");
    activateSubtab(SUBTABS.includes(second) ? second : "rule");
    return;
  }
  // Plain /builder is a new album; only /builder/<slug> shows a saved one.
  if (path === "/builder") {
    if (state.draft.slug) { state.draft = emptyDraft(); fillForm(); }
    activateTab("builder");
    activateSubtab("rule");
    return;
  }
  activateTab(TABS.includes(path.slice(1)) ? path.slice(1) : "albums");
}

$("builder-subtabs").addEventListener("click", (event) => {
  const button = event.target.closest(".subtab");
  if (!button) return;
  // Switching sub-tabs must never touch state.draft -- going through navigate()/route()
  // would re-run the "new album" branch and wipe an unsaved draft (slug is always "new"
  // until Save). Update the URL and the visible panel directly instead.
  const path = `/builder/${encodeURIComponent(state.draft.slug || "new")}/${button.dataset.subtab}`;
  if (location.pathname !== path) history.pushState(null, "", path);
  activateSubtab(button.dataset.subtab);
});

function navigate(path, { replace = false } = {}) {
  if (location.pathname !== path) {
    history[replace ? "replaceState" : "pushState"](null, "", path);
  }
  route(path);
}

function showTab(name) {
  navigate(`/${name}`);
}

$("tabs").addEventListener("click", (event) => {
  const button = event.target.closest(".tab");
  if (!button) return;
  navigate(`/${button.dataset.tab}`);
});

window.addEventListener("popstate", () => route(location.pathname));

/* -- albums tab --------------------------------------------------------- */

async function loadAlbums() {
  let data;
  try { data = await api("/api/albums"); }
  catch (error) { return banner(error.message); }

  state.saved = data.albums;
  $("albums-note").textContent =
    `default schedule: ${data.default_schedule}`;
  $("schedule").querySelector('option[value="inherit"]').textContent =
    data.default_schedule;
  renderAlbums();

  for (const problem of data.errors || []) banner(problem);
}

function filteredAlbums() {
  let albums = state.saved || [];
  const type = $("album-type-filter").value;
  if (type) albums = albums.filter(a => a.type === type);
  const needle = ($("album-filter").value || "").trim().toLowerCase();
  return needle
    ? albums.filter(a => (a.immich_name || a.name).toLowerCase().includes(needle))
    : albums;
}

function formatLastRun(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined,
      { dateStyle: "medium", timeStyle: "short" });
  } catch { return iso; }
}

function renderAlbums() {
  const list = $("album-list");
  list.replaceChildren();

  const albums = state.saved || [];
  if (!albums.length) {
    list.append(el("div", { class: "muted" },
      "No albums configured yet. Start one from the Builder, or from a detected trip."));
    return;
  }

  const shown = filteredAlbums();

  if (!shown.length) {
    list.append(el("div", { class: "muted" },
      `No albums match “${$("album-filter").value.trim()}”.`));
    return;
  }

  const TYPE_LABEL = { manual: "manual", scheduled: "scheduled",
                       normal: "normal Immich album" };

  for (const album of shown) {
    const managed = album.type !== "normal";
    const name = album.immich_name || album.name;

    // Status word for managed albums only; the dot's colour carries it.
    const [statusText, statusClass] = !managed ? [null]
      : album.last_error ? ["failed", "err"]
      : !album.enabled ? ["disabled", "off"]
      : !album.last_run ? ["not run", "off"]
      : ["OK", "ok"];
    const status = statusText
      ? el("span", { class: `status ${statusClass}` }, statusText) : "";

    // Labelled rows: one fixed label column, one value column.
    const rows = managed
      ? [infoRow("Rule", describe(album.match), "", ""),
         infoRow("Schedule",
           `${album.schedule}${album.schedule_inherited ? " (inherited)" : ""}`),
         album.last_error
           ? infoRow("Error", album.last_error, "bad clamp", "")
           : album.last_run
             ? infoRow("Last run", formatLastRun(album.last_run), "",
                       album.last_result || "")
             : infoRow("Last run", "never")]
      : [infoRow("Items", album.asset_count == null ? "—" : String(album.asset_count)),
         album.shared ? infoRow("Shared", "with me") : ""];

    // Extras as small chips, only when there are any.
    const chips = managed ? [
      (album.shares || []).length
        ? `shared with ${album.shares.map(s => s.account).join(", ")}` : "",
      album.pics_per_year ? `${album.pics_per_year}/yr (${album.pick})` : "",
      album.cover && album.cover !== "auto" ? `cover: ${album.cover}` : "",
    ].filter(Boolean) : [];

    const actions = managed
      ? [button("Edit", () => editAlbum(album)),
         button("Run now", () => runAlbum(album.slug, false))]
      : [];

    const card = el("div", { class: `card album type-${album.type}` },
      el("div", { class: "album-body" },
        album.cover_asset
          ? el("img", { class: "album-cover", src: `/api/thumb/${album.cover_asset}`,
                        loading: "lazy", alt: "" })
          : el("div", { class: "album-cover none" }),
        el("div", { class: "album-main" },
          el("div", { class: "album-head" },
            el("h3", { title: name }, name),
            el("span", { class: "pill" }, TYPE_LABEL[album.type] || album.type),
            status),
          el("dl", { class: "album-info" }, ...rows.flat().filter(Boolean)))),
      chips.length
        ? el("div", { class: "album-chips" },
            ...chips.map(c => el("span", { class: "pill" }, c)))
        : "",
      el("div", { class: "bar" }, ...actions, el("span", { class: "push" }),
         button("View", () => openAlbumView(album.album_id))));
    list.append(card);
  }
}

// One label/value pair for the album card's <dl>. `cls` styles the value;
// `title` (optional) is the hover text, e.g. the absolute time.
function infoRow(label, value, cls = "", title = "") {
  return [el("dt", {}, label),
          el("dd", { class: cls, title: title || value }, value)];
}

function describe(match) {
  const parts = [];
  const bound = (v) => v ? v.replace("T", " ").slice(0, 16) : "…";
  if (match.from || match.to) parts.push(`${bound(match.from)} → ${bound(match.to)}`);
  if (match.on_from) {
    let day = match.on_to && match.on_to !== match.on_from
      ? `${match.on_from} – ${match.on_to} every year`
      : `${match.on_from} every year`;
    if (match.since_year) day += ` since ${match.since_year}`;
    parts.push(day);
  }
  const places = [...match.countries, ...match.states, ...match.cities];
  if (places.length) parts.push(places.join(", "));
  if (match.people.length) {
    parts.push(match.people.join(match.people_mode === "all" ? " + " : ", "));
  }
  return parts.join(" · ") || "everything";
}

async function runAlbum(slug, dryRun) {
  banner(dryRun ? "Planning…" : "Running…", true);
  try {
    const result = await post("/api/run", { slug, dry_run: dryRun });
    if (result.error) return banner(result.error);
    const verb = dryRun ? "would add" : "added";
    banner(`${result.album}: ${verb} ${result.added}` +
           (result.removed ? `, removed ${result.removed}` : ""), true);
    loadAlbums();
  } catch (error) { banner(error.message); }
}

async function runAllAlbums() {
  const albums = filteredAlbums().filter(a => a.slug);
  if (!albums.length) return banner("No albums to run.");

  let ok = 0, failed = 0;
  for (const album of albums) {
    banner(`Running ${ok + failed + 1}/${albums.length}: ${album.immich_name || album.name}…`, true);
    try {
      const result = await post("/api/run", { slug: album.slug, dry_run: false });
      if (result.error) failed++; else ok++;
    } catch { failed++; }
  }
  banner(`Ran ${ok} album${ok === 1 ? "" : "s"}` + (failed ? `, ${failed} failed` : ""), true);
  loadAlbums();
}

$("album-filter").oninput = renderAlbums;
$("album-type-filter").onchange = renderAlbums;
$("new-album").onclick = () => navigate("/builder/new");
$("run-all-albums").onclick = runAllAlbums;

$("builder-new-album").onclick = () => navigate("/builder/new");

$("new-person-album").onclick = () => {
  navigate("/builder/new/rule");
  state.draft.match.include_unlocated = true;
  $("person-search").focus();
  banner("Pick one or more people. Leave the dates and places empty: that is " +
         "all a person album needs.", true);
};

function editAlbum(album) {
  editAlbumDraft(album);
  navigate(`/builder/${encodeURIComponent(album.slug)}/rule`);
}

function editAlbumDraft(album) {
  state.draft = {
    slug: album.slug, name: album.name, enabled: album.enabled,
    cover: album.cover || "auto",
    share_with: (album.shares || []).map(s => ({ ...s })),
    pics_per_year: album.pics_per_year ?? null, pick: album.pick || "all",
    hint_kind: album.hint_kind || "", hint_order: album.hint_order || "",
    slot: album.slot || "", dwell: album.dwell ?? null,
    active: album.active || "", caption: album.caption || "",
    activity: album.activity || "",
    schedule: album.schedule_inherited ? "inherit" : album.schedule,
    match: { ...album.match },
  };
  fillForm();
}

/* -- builder: form <-> draft -------------------------------------------- */

function fillForm() {
  const { draft } = state;
  $("album-name").value = draft.name;
  fillDates();
  $("people-mode").value = draft.match.people_mode;
  $("include-unlocated").checked = draft.match.include_unlocated;
  $("include-videos").checked = draft.match.include_videos;
  $("recur-since").value = draft.match.since_year ?? "";
  $("pick").value = draft.pick || "all";
  fillPicsPerYear();
  $("hint-kind").value = draft.hint_kind || "";
  $("hint-order").value = draft.hint_order || "";
  $("hint-dwell").value = draft.dwell ?? "";
  updateWhenExclusivity();
  fillCover(draft.cover || "auto");
  fillSharing();
  $("schedule").value = !draft.enabled
    ? "none"
    : [...$("schedule").options].some(o => o.value === draft.schedule)
      ? draft.schedule : "inherit";
  $("delete-config").hidden = $("save-as").hidden =
    !state.saved.some(a => a.slug === draft.slug);
  $("templates").hidden = !!(draft.slug || draft.name || draft.match.people.length ||
    draft.match.on_from || draft.match.from);
  renderChosenPeople();
  renderChosenPlaces();
  refreshHintExtra();
  refreshSubtabBadges();
  refreshPreview();
}

/* Slot/Active/Caption/Activity have no control of their own any more (the
 * builder only exposes Kind/Order/Dwell) but a value loaded from config.toml
 * must still round-trip on Save, so it is surfaced here rather than dropped. */
function refreshHintExtra() {
  const { draft } = state;
  const extras = [];
  if (draft.slot) extras.push(`slot=${draft.slot}`);
  if (draft.active) extras.push(`active=${draft.active}`);
  if (draft.caption) extras.push(`caption=${draft.caption}`);
  if (draft.activity) extras.push(`activity=${draft.activity}`);
  $("hint-extra").textContent = extras.length ? `also set in config: ${extras.join(" ")}` : "";
}

/* A folded-away sub-tab still needs to say whether it holds anything other
 * than the defaults, so a dot marks it and a hover title summarises it. */
function refreshSubtabBadges() {
  const { draft } = state;
  const optionsOn = draft.cover !== "favorite" || draft.schedule !== "inherit" ||
    !draft.enabled || draft.pick !== "all" || draft.pics_per_year != null ||
    draft.match.include_videos;
  setSubtabBadge("options", optionsOn, optionsOn
    ? `Cover: ${draft.cover} · Schedule: ${draft.enabled ? draft.schedule : "none"} · Pick: ${draft.pick}`
    : "");
  setSubtabBadge("sharing", draft.share_with.length > 0,
    draft.share_with.length ? `Shared with ${draft.share_with.length} account(s)` : "");
  const playerOn = !!(draft.hint_kind || draft.hint_order || draft.dwell != null ||
    draft.slot || draft.active || draft.caption || draft.activity);
  setSubtabBadge("player", playerOn, playerOn
    ? `Kind: ${draft.hint_kind || "auto"} · Order: ${draft.hint_order || "auto"}` : "");
}

function setSubtabBadge(subtab, on, title) {
  const button = document.querySelector(`#builder-subtabs .subtab[data-subtab="${subtab}"]`);
  button.classList.toggle("has-badge", on);
  button.title = title;
}

/* From and To hold a day, or the moment a picked first/last photo was taken
 * ("2023-05-28T14:32:10"). The date inputs show the day; an exact time shows
 * as a chip underneath, and removing it widens back to the whole day. They
 * also double as the entry point for a recurring window: typed as dd/mm
 * (no year) they set on_from/on_to instead of from/to -- see
 * parseDateField/applyDateFields. */
const pad2 = (n) => String(n).padStart(2, "0");

function isoToDisplay(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

function mmddToDisplay(mmdd) {
  if (!mmdd) return "";
  const [m, d] = mmdd.split("-");
  return `${d}/${m}`;
}

function fillDates() {
  const { match } = state.draft;
  if (match.on_from) {
    $("date-from").value = mmddToDisplay(match.on_from);
    $("date-to").value = mmddToDisplay(match.on_to || match.on_from);
  } else {
    $("date-from").value = isoToDisplay(match.from);
    $("date-to").value = isoToDisplay(match.to);
  }
  const box = $("exact-bounds");
  box.replaceChildren();
  const time = (v) => v && v.includes("T") ? v.slice(11, 19) : null;
  if (time(match.from)) {
    box.append(removableChip(`From photo at ${time(match.from)}`,
      () => setBound("from", match.from.slice(0, 10))));
  }
  if (time(match.to)) {
    box.append(removableChip(`To photo at ${time(match.to)}`,
      () => setBound("to", match.to.slice(0, 10))));
  }
}

function setBound(key, taken) {
  state.draft.match[key] = taken;
  fillDates();
  updateWhenExclusivity();
  refreshPreview();
}

/* dd/mm/yyyy -> an exact date; dd/mm -> a recurring day; mm/yyyy -> a whole
 * month; yyyy alone (or yyyy in both fields) -> a whole year, or a span of
 * whole years if From and To name different years (both fields required for
 * month/year and year/year -- see the plan's clarifying answers). Bad or
 * mismatched input is reported and nothing is applied. */
function parseDateField(raw) {
  const text = (raw || "").trim();
  if (!text) return null;
  let m = text.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (m) return { kind: "full", day: +m[1], month: +m[2], year: +m[3] };
  m = text.match(/^(\d{2})\/(\d{4})$/);
  if (m) return { kind: "month", month: +m[1], year: +m[2] };
  m = text.match(/^(\d{4})$/);
  if (m) return { kind: "year", year: +m[1] };
  m = text.match(/^(\d{2})\/(\d{2})$/);
  if (m) return { kind: "day", day: +m[1], month: +m[2] };
  return { kind: "invalid" };
}

function applyDateFields() {
  const from = parseDateField($("date-from").value);
  const to = parseDateField($("date-to").value);
  const errorBox = $("date-error");
  const fail = (msg) => { errorBox.textContent = msg; errorBox.hidden = false; };
  errorBox.hidden = true;
  errorBox.textContent = "";

  if (from?.kind === "invalid" || to?.kind === "invalid") {
    fail("Use dd/mm/yyyy, dd/mm, mm/yyyy, or yyyy.");
    return;
  }
  const match = state.draft.match;
  if (!from && !to) {
    match.from = match.to = null;
    match.on_from = match.on_to = "";
  } else if (from?.kind === "year" || to?.kind === "year") {
    if ((from && from.kind !== "year") || (to && to.kind !== "year")) {
      fail("From and To must both be dates, both yyyy, both month/year, or both day/month.");
      return;
    }
    const fromYear = from ? from.year : to.year;
    const toYear = to ? to.year : fromYear;
    match.from = `${fromYear}-01-01`;
    match.to = `${toYear}-12-31`;
    match.on_from = match.on_to = "";
  } else if (from?.kind === "month" || to?.kind === "month") {
    if (!from || !to || from.kind !== "month" || to.kind !== "month") {
      fail("mm/yyyy needs both From and To.");
      return;
    }
    match.from = `${from.year}-${pad2(from.month)}-01`;
    const lastDay = new Date(to.year, to.month, 0).getDate();
    match.to = `${to.year}-${pad2(to.month)}-${pad2(lastDay)}`;
    match.on_from = match.on_to = "";
  } else if (from?.kind === "day" || to?.kind === "day") {
    if ((from && from.kind !== "day") || (to && to.kind !== "day")) {
      fail("From and To must both be dates, both month/year, or both day/month.");
      return;
    }
    const onFrom = from ? `${pad2(from.month)}-${pad2(from.day)}`
                         : `${pad2(to.month)}-${pad2(to.day)}`;
    match.on_from = onFrom;
    match.on_to = to ? `${pad2(to.month)}-${pad2(to.day)}` : onFrom;
    match.from = match.to = null;
  } else {
    if (from && to && from.kind !== to.kind) {
      fail("From and To must both be dates, both month/year, or both day/month.");
      return;
    }
    match.from = from ? `${from.year}-${pad2(from.month)}-${pad2(from.day)}` : null;
    match.to = to ? `${to.year}-${pad2(to.month)}-${pad2(to.day)}` : null;
    match.on_from = match.on_to = "";
  }
  fillDates();
  updateWhenExclusivity();
  refreshPreview();
}

function bindDraft() {
  // The name decides which Immich album a run fills, so the preview follows it.
  $("album-name").oninput = (e) => { state.draft.name = e.target.value; refreshPreview(); };
  $("date-from").onchange = applyDateFields;
  $("date-to").onchange = applyDateFields;
  $("people-mode").onchange = (e) => set("people_mode", e.target.value);
  $("include-unlocated").onchange = (e) => set("include_unlocated", e.target.checked);
  $("include-videos").onchange = (e) => set("include_videos", e.target.checked);
  $("recur-since").onchange = (e) =>
    set("since_year", e.target.value ? parseInt(e.target.value, 10) : null);
  $("pics-per-year").onchange = (e) => {
    state.draft.pics_per_year = e.target.value ? parseInt(e.target.value, 10) : null;
    refreshPreview();
  };
  $("pick").onchange = (e) => {
    state.draft.pick = e.target.value;
    if (e.target.value === "all") state.draft.pics_per_year = null;
    fillPicsPerYear();
    refreshPreview();
  };
  $("hint-kind").onchange = (e) => { state.draft.hint_kind = e.target.value; refreshHintPreview(); };
  $("hint-order").onchange = (e) => { state.draft.hint_order = e.target.value; refreshHintPreview(); };
  $("hint-dwell").onchange = (e) => {
    state.draft.dwell = e.target.value ? parseInt(e.target.value, 10) : null;
    refreshHintPreview();
  };
  $("date-clear").onclick = () => {
    const { match } = state.draft;
    match.from = match.to = null;
    match.on_from = match.on_to = "";
    fillDates();
    updateWhenExclusivity();
    refreshPreview();
  };
  $("schedule").onchange = (e) => {
    const value = e.target.value;
    if (value === "none") {
      state.draft.enabled = false;
    } else {
      state.draft.enabled = true;
      state.draft.schedule = value;
    }
    refreshPreview();
  };
  $("cover").onchange = () => { readCover(); refreshPreview(); };
  $("cover-name").oninput = debounce(() => { readCover(); refreshPreview(); }, 350);
  $("share-search").onchange = (e) => { addShare(e.target.value); e.target.value = ""; };
}

/* Pick = "all" always ignores pics_per_year (picker.py), so the field is
 * blanked and disabled rather than showing a number that has no effect. */
function fillPicsPerYear() {
  const { draft } = state;
  const capped = draft.pick !== "all";
  $("pics-per-year-row").hidden = !capped;
  $("pics-per-year").value = capped ? (draft.pics_per_year ?? "") : "";
}

/* -- builder: sharing ---------------------------------------------------

   The picker lists the other accounts on this server. Loading it is allowed
   to fail: a key without `user.read` simply cannot share, and that must not
   stop the rest of the builder working. */

async function loadAccounts() {
  try {
    const data = await api("/api/accounts");
    state.accounts = data.accounts || [];
    state.accountsNote = data.unavailable || "";
  } catch (_) {
    state.accounts = [];
    state.accountsNote = "the account list could not be read";
  }
  fillSharing();
}

/* The user's own Immich albums that no rule keeps yet, offered as album names:
   picking one makes the rule extend it, and typing it by hand is error-prone. */
async function loadImmichAlbums() {
  let albums = [];
  try { albums = (await api("/api/immich-albums")).albums || []; }
  catch (_) { /* the picker is a convenience; a plain name still works */ }
  $("immich-albums").replaceChildren(...albums.map(album =>
    el("option", { value: album.name, label: `${album.name} — ${album.asset_count} media` })));
}

function fillSharing() {
  const shared = new Set(state.draft.share_with.map(s => s.account.toLowerCase()));
  $("share-accounts").replaceChildren(
    ...(state.accounts || [])
      .filter(a => !shared.has((a.name || a.email || "").toLowerCase()))
      .map(a => el("option", { value: a.name || a.email })));
  $("share-note").textContent = state.accountsNote || "";

  const box = $("share-list");
  box.replaceChildren();
  state.draft.share_with.forEach((share, index) => {
    const role = el("select", {},
      el("option", { value: "viewer" }, "Viewer — can view all assets"),
      el("option", { value: "editor" }, "Editor — can view, upload and delete assets"));
    role.value = share.role;
    role.onchange = (e) => {
      state.draft.share_with[index].role = e.target.value;
      refreshPreview();
    };
    const remove = el("span", { class: "x", title: "remove" }, "×");
    remove.onclick = () => {
      state.draft.share_with.splice(index, 1);
      fillSharing();
      refreshPreview();
    };
    box.append(el("div", { class: "share-row" },
      el("span", { class: "account" }, share.account), role, remove));
  });
}

/* Adding the same account twice would just be confusing -- the picker already
 * hides an account once it's shared, but a hand-typed name is kept too. */
function addShare(name) {
  name = name.trim();
  if (!name) return;
  if (state.draft.share_with.some(s => s.account.toLowerCase() === name.toLowerCase())) return;
  state.draft.share_with.push({ account: name, role: "viewer" });
  fillSharing();
  refreshPreview();
}

/* The cover is one value in the config but two controls here: a list of rules
   plus, for "a picture I name", the file name itself. */
const COVER_RULES = ["auto", "everyone", "newest", "oldest", "favorite"];

function fillCover(cover) {
  const named = !COVER_RULES.includes(cover);
  $("cover").value = named ? "named" : cover;
  $("cover-name").value = named ? cover : "";
  $("cover-name-row").hidden = !named;
}

function readCover() {
  const choice = $("cover").value;
  $("cover-name-row").hidden = choice !== "named";
  state.draft.cover = choice === "named"
    ? ($("cover-name").value.trim() || "auto")
    : choice;
}

function set(key, value) {
  state.draft.match[key] = value;
  updateWhenExclusivity();
  refreshPreview();
}

/* From/To double as the recurring-window entry (see applyDateFields), so a
 * rule is either a window or a recurring day depending on what was typed,
 * never both at once. The presets only make sense for an exact window, and
 * the since-year field only for a recurring one. */
function updateWhenExclusivity() {
  const hasRecur = !!state.draft.match.on_from;
  $("since-year-row").hidden = !hasRecur;
}

/* -- builder: people ---------------------------------------------------- */

const debounce = (fn, ms) => {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
};

$("person-search").oninput = debounce(async (event) => {
  const query = event.target.value.trim();
  const box = $("person-results");
  box.replaceChildren();
  if (!query) return;

  const hits = [];
  for (const group of state.groups) {
    if (group.name.toLowerCase().includes(query.toLowerCase())) {
      hits.push({ name: group.name, group: true, members: group.members });
    }
  }
  try {
    const data = await api(`/api/people?q=${encodeURIComponent(query)}`);
    for (const person of data.people) hits.push({ name: person.name, id: person.id });
  } catch (error) { return banner(error.message); }

  if (!hits.length) {
    box.append(el("div", { class: "muted" }, `Nobody named “${query}”.`));
    return;
  }
  // Immich can hold two face clusters with the same name. A rule names a person
  // by name, so an ambiguous name is refused at run time -- flag it here rather
  // than showing two identical chips and letting the run fail later.
  const counts = {};
  for (const hit of hits) if (!hit.group) counts[hit.name] = (counts[hit.name] || 0) + 1;
  let ambiguous = false;
  for (const hit of hits) {
    const clashes = !hit.group && counts[hit.name] > 1;
    ambiguous = ambiguous || clashes;
    const chip = el("span", { class: hit.group ? "chip group" : "chip" },
      hit.name,
      hit.group ? el("span", { class: "muted" }, ` ${hit.members.length}`) : "",
      clashes ? el("span", { class: "muted" }, ` #${(hit.id || "").slice(0, 4)}`) : "");
    if (clashes) chip.title = `Two Immich people are named “${hit.name}”; `
      + "merge or rename them in Immich, or a rule naming this person will fail.";
    chip.onclick = () => addPerson(hit.name);
    box.append(chip);
  }
  if (ambiguous) {
    box.append(el("div", { class: "warn" },
      "Two people share a name. A rule that names them will be refused until "
      + "you merge or rename one in Immich."));
  }
}, 250);

function addPerson(name) {
  const people = state.draft.match.people;
  if (!people.includes(name)) people.push(name);
  $("person-search").value = "";
  $("person-results").replaceChildren();
  renderChosenPeople();
  if (!state.draft.name) {
    $("album-name").value = state.draft.name =
      people.length === 1 ? `Photos of ${people[0]}` : `Photos of ${people.join(", ")}`;
  }
  refreshPreview();
}

function renderChosenPeople() {
  const box = $("person-chosen");
  box.replaceChildren();
  for (const name of state.draft.match.people) {
    box.append(removableChip(name, () => {
      state.draft.match.people = state.draft.match.people.filter(n => n !== name);
      renderChosenPeople();
      refreshPreview();
    }));
  }
  $("make-group").hidden = state.draft.match.people.length < 2;
}

$("make-group").onclick = async () => {
  const name = prompt("Name for this group (e.g. “Family Example”):");
  if (!name) return;
  try {
    const data = await post("/api/groups",
      { name, members: state.draft.match.people });
    state.groups = data.groups;
    state.draft.match.people = [name];
    renderChosenPeople();
    refreshPreview();
    banner(`Saved the group “${name}”. The album now refers to it by name.`, true);
  } catch (error) { banner(error.message); }
};

/* -- builder: templates --------------------------------------------------
 *
 * A handful of common album shapes, offered as quick-fill buttons on a new,
 * still-empty draft (fillForm hides the row once the draft holds anything).
 * Each one just sets fields on state.draft and re-renders the form -- the
 * result is an ordinary draft the usual controls can still tweak or correct. */

const TEMPLATE_IDS = ["birthday", "christmas", "person-years", "person", "nye",
  "summer", "place", "year-review", "together", "anniversary"];

$("templates").addEventListener("click", (event) => {
  const button = event.target.closest("[data-template]");
  if (!button) return;
  // Trip already has its own tab and flow; the template just points there.
  navigate(button.dataset.template === "trip" ? "/trips"
    : `/builder/new/${button.dataset.template}`);
});

/* Called from route() once a /builder/new/<template> URL is reached (so the
 * result is the same whichever way it was opened -- clicked or bookmarked).
 * Each one just sets fields on state.draft; fillForm()/activateSubtab() are
 * the caller's job, since route() needs those to run either way. */
async function applyTemplate(id) {
  const ask = (prompt_) => (prompt(prompt_) || "").trim();
  const draft = state.draft;

  if (id === "christmas") {
    draft.name = draft.name || "Christmas";
    draft.match.on_from = "12-24"; draft.match.on_to = "12-26";
    draft.pick = "best"; draft.pics_per_year = 15; draft.active = "12-01..12-31";
  } else if (id === "nye") {
    draft.name = draft.name || "New Year's Eve";
    draft.match.on_from = "12-31"; draft.match.on_to = "01-01";
    draft.pick = "best"; draft.pics_per_year = 10;
  } else if (id === "summer") {
    draft.name = draft.name || "Summer";
    draft.match.on_from = "06-21"; draft.match.on_to = "09-22";
    draft.pick = "best"; draft.pics_per_year = 20;
  } else if (id === "person") {
    const name = ask("Person's name, as named in Immich:");
    if (!name) return;
    draft.match.people = [name];
    draft.name = `Photos of ${name}`;
  } else if (id === "person-years") {
    const name = ask("Person's name, as named in Immich:");
    if (!name) return;
    draft.match.people = [name];
    draft.name = `${name} over the years`;
    draft.pick = "best"; draft.pics_per_year = 5; draft.hint_order = "one-per-year";
  } else if (id === "together") {
    const a = ask("First person's name, as named in Immich:");
    if (!a) return;
    const b = ask("Second person's name, as named in Immich:");
    if (!b) return;
    draft.match.people = [a, b];
    draft.match.people_mode = "all";
    draft.name = `${a} & ${b}`;
  } else if (id === "birthday") {
    const name = ask("Person's name, as named in Immich:");
    if (!name) return;
    draft.match.people = [name];
    draft.name = `Birthday of ${name}`;
    draft.pick = "best"; draft.pics_per_year = 10; draft.hint_order = "one-per-year";
    try {
      const data = await api(`/api/people?q=${encodeURIComponent(name)}`);
      const hit = (data.people || []).find((p) => p.name === name) || data.people[0];
      if (hit && hit.birth_date) {
        const [, month, day] = hit.birth_date.split("-");
        draft.match.on_from = draft.match.on_to = `${month}-${day}`;
      } else {
        banner(`Immich has no birth date for ${name} — set the date on the Rule tab.`);
      }
    } catch (_) { /* best effort; the date can still be set by hand */ }
  } else if (id === "place") {
    draft.hint_kind = "place";
    draft.pick = "best";
    banner("Choose a country, state or city below.", true);
  } else if (id === "year-review") {
    const raw = ask("Which year?");
    const year = parseInt(raw, 10);
    if (!raw || Number.isNaN(year)) return;
    draft.match.from = `${year}-01-01`;
    draft.match.to = `${year}-12-31`;
    draft.name = draft.name || `Year in review ${year}`;
    draft.pick = "best"; draft.pics_per_year = 60;
  } else if (id === "anniversary") {
    const label = ask('What is this anniversary called? (e.g. "Wedding anniversary")');
    if (!label) return;
    const raw = ask("Date, as dd/mm:");
    const m = raw.match(/^(\d{2})\/(\d{2})$/);
    if (!m) return banner("Use dd/mm for the date.");
    draft.match.on_from = draft.match.on_to = `${m[2]}-${m[1]}`;
    draft.name = label;
    draft.pick = "best"; draft.pics_per_year = 10;
  }
}

/* -- builder: places ---------------------------------------------------- */

async function fillPlaces(kind, params = {}) {
  const select = $(`pick-${kind}`);
  const query = new URLSearchParams({ type: kind, ...params });
  select.replaceChildren(el("option", { value: "" }, `${kind}…`));
  try {
    const data = await api(`/api/places?${query}`);
    for (const value of data.values) select.append(el("option", { value }, value));
  } catch (error) { banner(error.message); }
}

for (const kind of ["country", "state", "city"]) {
  $(`pick-${kind}`).onchange = (event) => {
    const value = event.target.value;
    event.target.selectedIndex = 0;
    if (!value) return;
    const key = { country: "countries", state: "states", city: "cities" }[kind];
    if (!state.draft.match[key].includes(value)) state.draft.match[key].push(value);
    renderChosenPlaces();
    refreshPreview();
    // Narrow the next level down, the way Immich's own suggestions do.
    if (kind === "country") fillPlaces("state", { country: value });
    if (kind === "state") fillPlaces("city", { state: value });
  };
}

function renderChosenPlaces() {
  const box = $("place-chosen");
  box.replaceChildren();
  for (const [key, label] of [["countries", ""], ["states", ""], ["cities", ""]]) {
    for (const value of state.draft.match[key]) {
      box.append(removableChip(value + label, () => {
        state.draft.match[key] = state.draft.match[key].filter(v => v !== value);
        renderChosenPlaces();
        refreshPreview();
      }));
    }
  }
}

/* -- builder: preview --------------------------------------------------- */

const refreshPreview = debounce(async () => {
  const box = $("preview");
  const { match } = state.draft;
  state.immichName = null;
  refreshSubtabBadges();
  const empty = !match.from && !match.to && !match.people.length &&
    !match.countries.length && !match.states.length && !match.cities.length &&
    !match.on_from;
  if (empty) {
    box.replaceChildren(el("div", { class: "muted" },
      "Pick a date range, a recurring day, a place or a person to see a preview."));
    $("hint-preview").textContent = "";
    return;
  }

  const token = ++state.previewToken;
  box.replaceChildren(el("div", { class: "spin" }, "Counting…"));
  let data;
  try { data = await post("/api/preview", state.draft); }
  catch (error) {
    if (token !== state.previewToken) return;
    box.replaceChildren(el("div", { class: "warn bad" }, error.message));
    $("hint-preview").textContent = "";
    return;
  }
  if (token !== state.previewToken) return;   // a newer preview already won

  state.immichName = data.immich_name;
  state.extends = data.extends ? { ...data.extends, to_add: data.to_add } : null;
  const children = [
    ...(data.extends ? [extendNotice(data)] : []),
    ...(data.immich_name
      ? [el("div", { class: "muted" }, `In Immich as “${data.immich_name}”`)] : []),
    el("div", { class: "count" }, String(data.matched),
       el("small", {}, data.creates_album
          ? "media — this album does not exist in Immich yet"
          : `media matched · ${data.already_in_album} already in the album · ` +
            `${data.to_add} to add` +
            (data.to_remove ? ` · ${data.to_remove} to remove` : ""))),
  ];
  for (const warning of data.warnings || []) {
    children.push(el("div", { class: "warn" }, warning));
  }
  children.push(...editStrips(data, data.taken));
  if (data.cover_asset) {
    children.push(el("div", { class: "cover" },
      el("img", { src: `/api/thumb/${data.cover_asset}`, loading: "lazy", alt: "" }),
      el("span", { class: "muted" }, "would become the album cover")));
  }
  if (data.to_share) {
    children.push(el("div", { class: "muted" },
      `${data.to_share} account(s) would gain access to this album`));
  }
  $("next-run").textContent = !state.draft.enabled
    ? "disabled — this album is skipped by every run"
    : data.next_run ? `next automatic run: ${data.next_run.replace("T", " ")}` : "";
  // See docs/hints.md for what this line means.
  $("hint-preview").textContent = data.hint ? `description hint: ${data.hint}` : "";
  box.replaceChildren(...children);
}, 350);

/* The "Optional" group's fields only change the description-hint line, never
   which media match -- so this skips the expensive full /api/preview (which
   re-queries Immich) and hits the cheap, local-only /api/hint-preview instead. */
const refreshHintPreview = debounce(async () => {
  refreshSubtabBadges();
  const token = ++state.hintPreviewToken;
  let data;
  try { data = await post("/api/hint-preview", state.draft); }
  catch (error) {
    if (token !== state.hintPreviewToken) return;   // a newer request already won
    $("hint-preview").textContent = "";
    return;
  }
  if (token !== state.hintPreviewToken) return;      // a newer request already won
  $("hint-preview").textContent = data.hint ? `description hint: ${data.hint}` : "";
}, 350);

/* A run on this rule takes over an album the butler has never kept -- an
   import, say. That is worth more than a count in small print. */
function extendNotice(data) {
  const ext = data.extends;
  return el("div", { class: "notice extend" },
    ext.cover_asset
      ? el("img", { src: `/api/thumb/${ext.cover_asset}`, loading: "lazy", alt: "" })
      : el("div", { class: "album-cover none" }),
    el("div", {},
      el("strong", {}, `Extends your existing Immich album “${ext.name}” ` +
                       `(${ext.asset_count} media)`),
      el("div", {}, `Its own media stay; ${data.to_add} are added.` +
        (ext.rename_to ? ` It is renamed to “${ext.rename_to}”.` : "")),
      ...(data.to_remove
        ? [el("div", { class: "bad" }, `${data.to_remove} of its media lie outside ` +
              "the first/last photo and will be taken out of it.")] : [])));
}

/* -- builder: actions --------------------------------------------------- */

$("save").onclick = () => {
  if (!state.draft.name.trim()) return banner("Give the album a name first.");
  saveDraft(state.draft, { replace: true }, (result) => `Saved ${result.path}`);
};

/* N41: the draft becomes a new album under a new name; the one it was
 * opened from stays as it was saved. */
$("save-as").onclick = () => {
  const name = (prompt("Name of the new album:", `${state.draft.name} (copy)`) || "").trim();
  if (!name) return;
  saveDraft({ ...state.draft, slug: "", name, new: true }, { replace: false },
            () => `Saved as a new album “${name}”.`);
};

async function saveDraft(payload, how, message) {
  try {
    const result = await post("/api/albums", payload);
    state.draft.slug = result.saved;
    state.draft.name = payload.name;
    $("album-name").value = payload.name;
    await loadAlbums();
    $("delete-config").hidden = $("save-as").hidden = false;
    navigate(`/builder/${encodeURIComponent(state.draft.slug)}/${state.subtab || "rule"}`, how);
    banner(message(result), true);
  } catch (error) { banner(error.message); }
}

$("dry-run").onclick = () => requireSaved(() => runAlbum(state.draft.slug, true));
$("run-now").onclick = () => requireSaved(async () => {
  const ext = state.extends;
  if (ext && !confirm(`This adds ${ext.to_add} media to your existing Immich album ` +
                      `“${ext.name}”` + (ext.rename_to ? ` and renames it to “${ext.rename_to}”` : "") +
                      ".\n\nContinue?")) return;
  await runAlbum(state.draft.slug, false);
  if (ext) {   // it is the butler's now: no longer on offer, no longer "extends"
    loadImmichAlbums();
    refreshPreview();
  }
});

function requireSaved(work) {
  if (!state.saved.some(a => a.slug === state.draft.slug)) {
    return banner("Save the album first — a run writes to Immich, so it runs " +
                  "what the config says, not an unsaved draft.");
  }
  work();
}

$("delete-config").onclick = async () => {
  const name = state.immichName || state.draft.name;
  if (!confirm(`Delete the config for “${name}”?`)) return;
  const alsoImmich = confirm(
    `Also delete the album “${name}” from Immich?\n\n` +
    `OK: delete the Immich album too — its photos stay in your library.\n` +
    `Cancel: keep the Immich album, remove only the butler rule.`);
  try {
    const query = alsoImmich ? "?immich=1" : "";
    const result = await api(
      `/api/albums/${encodeURIComponent(state.draft.slug)}${query}`,
      { method: "DELETE" });
    banner(result.note, true);
    state.draft = emptyDraft();
    fillForm();
    loadAlbums();
    showTab("albums");
  } catch (error) { banner(error.message); }
};

/* -- analyze ------------------------------------------------------------ */

$("analyze").onclick = async () => {
  const box = $("analysis");
  box.replaceChildren(el("div", { class: "spin" }, "Looking for near misses…"));
  let data;
  try { data = await post("/api/analyze", state.draft); }
  catch (error) {
    box.replaceChildren(el("div", { class: "warn bad" }, error.message));
    return;
  }

  box.replaceChildren();
  if (data.note) box.append(el("div", { class: "muted" }, data.note));
  if (!data.suggestions.length && !data.note) {
    box.append(el("div", { class: "muted good" },
      "Nothing obvious is missing: no media sit just outside this rule."));
    return;
  }

  for (const group of data.suggestions) {
    const card = el("div", { class: "card" },
      el("h3", {}, `${group.count} · ${group.title}`),
      el("div", { class: "meta" }, group.detail));

    const strip = el("div", { class: "strip" });
    for (const id of group.asset_ids.slice(0, 12)) {
      strip.append(el("img", { src: `/api/thumb/${id}`, loading: "lazy", alt: "" }));
    }
    card.append(strip);

    const bar = el("div", { class: "bar" });
    if (group.adjust) {
      bar.append(button("Adjust the rule", () => applyAdjust(group.adjust)));
    }
    bar.append(button("Add these now", () => addNow(group)));
    card.append(bar);
    box.append(card);
  }
};

function applyAdjust(adjust) {
  Object.assign(state.draft.match, adjust);
  fillForm();
  banner("Rule updated — check the preview, then Save.", true);
}

async function addNow(group) {
  if (!confirm(`Add ${group.count} media to “${state.immichName || state.draft.name}” now?\n\n` +
               `This is a one-off: the rule is not changed, so a future run ` +
               `will not re-add them if they are removed.`)) return;
  try {
    const result = await post("/api/add-assets",
      { ...state.draft, asset_ids: group.asset_ids });
    banner(`Added ${result.added} of ${result.requested} to ${result.album}.`, true);
    refreshPreview();
  } catch (error) { banner(error.message); }
}

/* -- backup tab ---------------------------------------------------------- */

/* Backups are files on the server. The panel reads top to bottom: back up;
 * the saved backups (newest first, tick to delete); then restore in three
 * steps (choose a backup, choose what, preview and restore). "Restore now"
 * only unlocks after a preview of the same choice. */

let backupList = [];
let chosenBackup = null;

$("backup-create").onclick = async () => {
  try {
    const made = await post("/api/backups", {});
    banner(`Saved ${made.path}`, true);
    chosenBackup = made.name;
  } catch (error) { banner(error.message); }
  loadBackups();
};

function backupWhen(b) {
  const when = new Date(b.created);
  return isNaN(when) ? b.name : when.toLocaleString(undefined,
    { dateStyle: "medium", timeStyle: "short" });
}

function backupSummary(b) {
  return `${b.albums} albums · ${b.assets} assets · ` +
    `${Math.max(1, Math.round(b.size / 1024))} KiB`;
}

async function loadBackups() {
  const list = $("backup-list");
  let data;
  try { data = await api("/api/backups"); }
  catch (error) { return banner(error.message); }
  $("backup-dir").textContent = data.directory;
  backupList = data.backups;      // newest first, from the server
  if (!backupList.some((b) => b.name === chosenBackup)) {
    chosenBackup = backupList.length ? backupList[0].name : null;
  }
  list.replaceChildren(...(backupList.length ? backupList.map(backupCard)
    : [el("div", { class: "muted" }, "No backups yet. Use “Back up now” above.")]));
  $("backup-select-all").checked = false;
  $("backup-select-all").disabled = !backupList.length;
  updateDeleteButton();

  const choice = $("backup-choice");
  choice.replaceChildren(...backupList.map((b) =>
    el("option", { value: b.name }, `${backupWhen(b)} — ${backupSummary(b)}`)));
  choice.disabled = !backupList.length;
  choice.value = chosenBackup || "";
  chooseBackup(chosenBackup);
}

function backupCard(b) {
  const tick = el("input", { type: "checkbox", class: "backup-tick", value: b.name });
  tick.onchange = updateDeleteButton;
  return el("label", { class: "card backup-card" }, tick,
    el("span", {},
      el("b", {}, backupWhen(b)),
      el("div", { class: "muted" }, `${backupSummary(b)} · ${b.name}`)));
}

function tickedBackups() {
  return [...document.querySelectorAll(".backup-tick:checked")].map((t) => t.value);
}

function updateDeleteButton() {
  const count = tickedBackups().length;
  $("backup-delete").disabled = !count;
  $("backup-delete").textContent = count ? `Delete selected (${count})` : "Delete selected";
  for (const tick of document.querySelectorAll(".backup-tick")) {
    tick.closest(".backup-card").classList.toggle("selected", tick.checked);
  }
}

$("backup-select-all").onchange = () => {
  for (const tick of document.querySelectorAll(".backup-tick")) {
    tick.checked = $("backup-select-all").checked;
  }
  updateDeleteButton();
};

$("backup-delete").onclick = async () => {
  const names = tickedBackups();
  if (!names.length) return;
  if (!confirm(`Delete ${names.length} backup file${names.length > 1 ? "s" : ""}?` +
               "\n\nThis cannot be undone. Immich is not touched.")) return;
  try {
    const result = await post("/api/backups/delete", { names });
    banner(`Deleted ${result.deleted.length} backup${result.deleted.length === 1 ? "" : "s"}`, true);
  } catch (error) { banner(error.message); }
  loadBackups();
};

$("backup-choice").onchange = () => chooseBackup($("backup-choice").value);

function chooseBackup(name) {
  chosenBackup = name;
  const chosen = backupList.find((b) => b.name === name);
  const select = $("backup-album");
  select.replaceChildren(el("option", { value: "" }, "All albums in the backup"),
    ...(chosen ? chosen.album_names : []).map((n) => el("option", { value: n }, n)));
  select.disabled = $("backup-config").disabled = $("backup-preview").disabled = !chosen;
  resetRestorePreview();
}

function resetRestorePreview() {
  $("backup-restore").disabled = true;
  $("backup-result").hidden = true;
}

$("backup-album").onchange = resetRestorePreview;
$("backup-config").onchange = resetRestorePreview;
$("backup-preview").onclick = previewRestore;
$("backup-restore").onclick = runRestore;

function describeRestore(result) {
  const lines = result.albums.map((a) => {
    if (a.action === "skip") return `= ${a.name}: complete`;
    const verb = a.action === "create" ? "create" : "extend";
    const count = result.dry_run ? a.to_add : a.added;
    let line = `+ ${verb} ${a.name}: ${count} assets`;
    if (a.remapped) line += ` (${a.remapped} found again by checksum)`;
    return line + a.unmatched.map((n) => `\n    not in the library: ${n}`).join("") +
      a.warnings.map((w) => `\n    ${w}`).join("");
  });
  if (result.config_restored) lines.push("~ config.toml restored");
  for (const w of result.warnings) lines.push(`! ${w}`);
  return lines.join("\n") || "nothing to restore";
}

function restoreRequest() {
  return { name: chosenBackup, album: $("backup-album").value,
           config: $("backup-config").checked };
}

async function previewRestore() {
  const out = $("backup-result");
  let preview;
  try { preview = await post("/api/backups/restore", { ...restoreRequest(), dry_run: true }); }
  catch (error) { return banner(error.message); }
  out.textContent = "Restore would do:\n" + describeRestore(preview);
  out.hidden = false;
  $("backup-restore").disabled = false;
}

async function runRestore() {
  const out = $("backup-result");
  if (!confirm(`Restore from ${chosenBackup}?\n\n${out.textContent}`)) return;
  $("backup-restore").disabled = true;
  try {
    const done = await post("/api/backups/restore", { ...restoreRequest(), dry_run: false });
    out.textContent = "Done:\n" + describeRestore(done);
    banner("Restore finished", true);
  } catch (error) { banner(error.message); }
}

/* -- duplicates tab ----------------------------------------------------- */

/* Two pages in one panel: the overview of albums with duplicates, and one
 * album's groups to remove copies from. Both read the server's cached scan;
 * only "Rescan" asks Immich afresh. */

$("dup-rescan").onclick = () => loadDuplicates(true);

$("dup-filter").oninput = renderDupList;

const RULE_MANAGED = "rule-managed: removed copies return on the next run";

function showDupPage(page) {
  $("dup-list").hidden = page !== "list";
  $("dup-album").hidden = page !== "album";
}

async function loadDuplicates(rescan = false) {
  const list = $("dup-list");
  const note = $("dup-note");
  showDupPage("list");
  note.textContent = "";
  list.replaceChildren(el("div", { class: "spin" },
    rescan ? "Scanning Immich for duplicates…" : "Loading…"));
  let data;
  try { data = await api(`/api/duplicates${rescan ? "?rescan=1" : ""}`); }
  catch (error) { list.replaceChildren(); return banner(error.message); }

  const total = data.albums.reduce((sum, a) => sum + a.removable, 0);
  note.replaceChildren(`${data.albums.length} albums · ${total} duplicates · scanned `,
    el("b", {}, (data.scanned_at || "").replace("T", " ").slice(0, 16)));
  state.dups = data.albums;
  renderDupList();
}

function renderDupList() {
  const list = $("dup-list");
  list.replaceChildren();
  const albums = state.dups || [];
  if (!albums.length) {
    list.append(el("div", { class: "muted" }, "No album contains duplicates."));
    return;
  }
  const needle = $("dup-filter").value.trim().toLocaleLowerCase();
  const shown = needle
    ? albums.filter(a => a.name.toLocaleLowerCase().includes(needle))
    : albums;
  if (!shown.length) {
    list.append(el("div", { class: "muted" },
      `No albums match “${$("dup-filter").value.trim()}”.`));
    return;
  }
  for (const album of shown) {
    const card = el("div", { class: "card dup-album-card", title: "Open" },
      el("img", { class: "dup-cover", src: `/api/thumb/${album.cover}`,
                  loading: "lazy", alt: "" }),
      el("div", {},
        el("h3", {}, album.name),
        el("div", { class: "meta" },
          `${album.removable} duplicate${album.removable === 1 ? "" : "s"} · ` +
          `${album.groups} group${album.groups === 1 ? "" : "s"}`)));
    card.onclick = () => openDupAlbum(album.album_id);
    list.append(card);
  }
}

async function openDupAlbum(albumId) {
  const box = $("dup-album");
  showDupPage("album");
  box.replaceChildren(el("div", { class: "spin" }, "Loading…"));
  let album;
  try { album = await api(`/api/duplicates/album?id=${encodeURIComponent(albumId)}`); }
  catch (error) { banner(error.message); return loadDuplicates(); }

  const keep = {};                       // duplicate id -> asset id to keep
  const n = album.removable;
  const remove = button(`Remove ${n} duplicate${n === 1 ? "" : "s"} from album`, async () => {
    const ids = album.groups.flatMap((g) =>
      g.assets.map((a) => a.id).filter((id) => id !== keep[g.duplicate_id]));
    if (!confirm(`Remove ${ids.length} duplicate(s) from “${album.name}”?\n\n` +
                 "They are only taken out of the album, not deleted from Immich." +
                 (album.rule_managed ? `\n\nNote: ${RULE_MANAGED}.` : ""))) return;
    remove.disabled = true;
    try {
      const result = await post("/api/duplicates/remove",
        { album_id: album.album_id, asset_ids: ids });
      banner(`Removed ${result.removed} from ${album.name}.`, true);
      loadDuplicates();
    } catch (error) { remove.disabled = false; banner(error.message); }
  });
  remove.classList.add("danger");

  box.replaceChildren(
    el("div", { class: "toolbar" },
      button("← All albums", () => loadDuplicates()),
      el("h3", { class: "dup-title" }, album.name),
      remove),
    el("div", { class: "muted scan-note" },
      `${album.groups.length} group${album.groups.length === 1 ? "" : "s"} · ` +
      "the earliest copy of each is kept unless you pick another."),
    album.rule_managed ? el("div", { class: "warn" }, RULE_MANAGED) : "");

  for (const group of album.groups) {
    keep[group.duplicate_id] = group.keep;
    const row = el("div", { class: "strip dup-group" });
    for (const asset of group.assets) {
      const radio = el("input", { type: "radio", name: `keep-${group.duplicate_id}` });
      radio.checked = asset.id === group.keep;
      radio.onchange = () => { keep[group.duplicate_id] = asset.id; };
      row.append(el("label", { class: "dup-pick",
          title: [asset.file_name, fmt.when(asset.taken_at)].filter(Boolean).join(" · ") },
        el("img", { src: `/api/thumb/${asset.id}`, loading: "lazy", alt: "" }),
        el("span", { class: "muted" }, radio, " keep")));
    }
    box.append(row);
  }
}

/* -- album viewer --------------------------------------------------------- */

/* One album's media, grouped by folder, date, camera, place or kind, opened
 * with the "View" button on an Albums card -- a butler album or a plain
 * Immich one alike. View only, apart from picking a cover (N-cover). The
 * server sends the whole album once; regrouping happens here. */

const browse = { album: null, order: [] };
const BROWSE_GROUP = "butler.browse.groupBy";
try { $("browse-group").value = localStorage.getItem(BROWSE_GROUP) || "folder"; } catch {}
if (!$("browse-group").value) $("browse-group").value = "folder";

$("browse-back").onclick = () => showTab("albums");
$("browse-group").onchange = () => {
  try { localStorage.setItem(BROWSE_GROUP, $("browse-group").value); } catch {}
  renderBrowseAlbum();
};
function foldGroups(open) {
  for (const d of $("browse-groups").querySelectorAll("details")) d.open = open;
}
$("browse-collapse").onclick = () => foldGroups(false);
$("browse-expand").onclick = () => foldGroups(true);

async function openAlbumView(albumId, { push = true } = {}) {
  if (!albumId) return banner("This album has no picture yet.");
  if (push) {
    const path = `/albums/${encodeURIComponent(albumId)}`;
    if (location.pathname !== path) history.pushState(null, "", path);
  }
  for (const el of document.querySelectorAll(".tab")) el.classList.remove("active");
  for (const panel of document.querySelectorAll(".panel")) {
    panel.classList.toggle("active", panel.id === "browse-album");
  }
  $("browse-title").textContent = "";
  $("browse-album-note").textContent = "";
  $("browse-groups").replaceChildren(el("div", { class: "spin" }, "Loading…"));
  try { browse.album = await api(`/api/browse/album?id=${encodeURIComponent(albumId)}`); }
  catch (error) { banner(error.message); return showTab("albums"); }
  $("browse-title").textContent = browse.album.name;
  renderBrowseAlbum();
}

async function setCover(assetId) {
  const album = browse.album;
  if (!album) return;
  try {
    await post("/api/cover", { album_id: album.album_id, asset_id: assetId });
    banner(`Cover set for “${album.name}”.`, true);
  } catch (error) { banner(error.message); }
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];

/* What each "group by" files a picture under, and how the groups are ordered:
 * by key (folders, dates) or biggest first (camera, place). */
const GROUPINGS = {
  none:   { key: () => "", byKey: true },
  folder: { key: (a) => a.folder || "(no folder)", byKey: true },
  day:    { key: (a) => (a.taken_at || "").slice(0, 10) || "(no date)", byKey: true },
  month:  { key: (a) => (a.taken_at || "").slice(0, 7) || "(no date)", byKey: true,
            label: (k) => /^\d{4}-\d{2}$/.test(k)
              ? `${MONTHS[Number(k.slice(5)) - 1]} ${k.slice(0, 4)}` : k },
  year:   { key: (a) => (a.taken_at || "").slice(0, 4) || "(no date)", byKey: true },
  camera: { key: (a) => a.camera || "(unknown camera)" },
  place:  { key: (a) => [a.city, a.country].filter(Boolean).join(", ") || "(no place)" },
  kind:   { key: (a) => (a.kind === "VIDEO" ? "Videos" : "Photos") },
};

function renderBrowseAlbum() {
  const album = browse.album;
  if (!album) return;
  const by = $("browse-group").value;
  const how = GROUPINGS[by] || GROUPINGS.folder;
  const groups = new Map();
  for (const asset of album.assets) {
    const key = how.key(asset);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(asset);
  }
  // Placeholders like "(no date)" always go last.
  const odd = (k) => k.startsWith("(");
  const keys = [...groups.keys()].sort((a, b) =>
    odd(a) - odd(b)
    || (how.byKey ? a.localeCompare(b, undefined, { numeric: true })
                  : groups.get(b).length - groups.get(a).length || a.localeCompare(b)));

  const n = album.assets.length;
  $("browse-album-note").textContent = `${n} item${n === 1 ? "" : "s"}` +
    (by === "none" ? "" : ` · ${keys.length} group${keys.length === 1 ? "" : "s"}`);
  $("browse-fold").hidden = by === "none";

  const box = $("browse-groups");
  box.replaceChildren();
  browse.order = [];                     // what the lightbox steps through
  if (!n) box.append(el("div", { class: "muted" }, "This album is empty."));
  for (const key of keys) {
    const members = groups.get(key);
    const grid = el("div", { class: "browse-grid" });
    for (const asset of members) {
      const index = browse.order.push(asset) - 1;
      const cell = el("div", { class: `browse-cell${asset.kind === "VIDEO" ? " video" : ""}` },
        el("img", { src: `/api/thumb/${asset.id}`, loading: "lazy", alt: "" }));
      cell.onclick = () => openLightbox(index);
      if (asset.kind !== "VIDEO") {
        const star = el("button", { class: "cover-pick", title: "Set as album cover" }, "★");
        star.onclick = (event) => { event.stopPropagation(); setCover(asset.id); };
        cell.append(star);
      }
      grid.append(cell);
    }
    if (by === "none") { box.append(grid); continue; }
    box.append(el("details", { class: "browse-group", open: "" },
      el("summary", {},
        how.label ? how.label(key) : key,
        el("span", { class: "muted" }, ` · ${members.length}`)),
      grid));
  }
}

/* -- lightbox: one large picture, stepped through with the arrow keys ----- */

const lightbox = { index: -1, epoch: 0 };

function openLightbox(index) {
  hideHoverCard();
  lightbox.index = index;
  $("lightbox").hidden = false;
  showLightbox();
}

function closeLightbox() {
  $("lightbox").hidden = true;
  lightbox.index = -1;
}

function stepLightbox(delta) {
  const total = browse.order.length;
  if (!total || lightbox.index < 0) return;
  lightbox.index = (lightbox.index + delta + total) % total;
  showLightbox();
}

async function showLightbox() {
  const asset = browse.order[lightbox.index];
  if (!asset) return closeLightbox();
  const epoch = ++lightbox.epoch;
  const box = $("lightbox");
  box.querySelector("img").src = `/api/thumb/${asset.id}?size=preview`;
  const details = el("div", { class: "muted" }, "loading details…");
  box.querySelector("figcaption").replaceChildren(
    el("div", { class: "lb-head" },
      el("b", {}, asset.file_name || ""),
      asset.kind === "VIDEO" ? el("span", { class: "pill" }, "video") : "",
      el("span", { class: "muted" }, `${lightbox.index + 1} / ${browse.order.length}`)),
    details);
  try {
    const data = await assetDetails(asset.id);
    if (epoch === lightbox.epoch) details.replaceWith(detailRows(data));
  } catch (error) {
    if (epoch === lightbox.epoch) details.textContent = `No details: ${error.message}`;
  }
}

$("lightbox").querySelector(".lb-close").onclick = closeLightbox;
$("lightbox").querySelector(".lb-prev").onclick = () => stepLightbox(-1);
$("lightbox").querySelector(".lb-next").onclick = () => stepLightbox(1);
$("lightbox").addEventListener("click", (event) => {
  if (event.target === $("lightbox")) closeLightbox();
});
document.addEventListener("keydown", (event) => {
  if ($("lightbox").hidden) return;
  if (event.key === "Escape") closeLightbox();
  else if (event.key === "ArrowLeft") stepLightbox(-1);
  else if (event.key === "ArrowRight") stepLightbox(1);
  else return;
  event.preventDefault();
});

/* -- trips tab ---------------------------------------------------------- */

$("rescan").onclick = () => loadTrips(true);

$("trip-filter").oninput = renderTripList;

function applyOnlyNew() {
  $("trip-list").classList.toggle("only-new", $("only-new").checked);
}
$("only-new").onchange = applyOnlyNew;
applyOnlyNew();

async function loadTrips(rescan) {
  const list = $("trip-list");
  const note = $("scan-note");
  note.textContent = rescan
    ? "Scanning the whole library — this takes a while…" : "";
  list.replaceChildren(el("div", { class: "spin" }, rescan ? "Scanning…"
    : "Loading… the first visit each day rescans the library, which takes a few minutes."));

  const query = new URLSearchParams({
    rescan: rescan ? "1" : "0",
    away_km: $("away-km").value || "100",
    min_assets: $("min-assets").value || "30",
  });
  let data;
  try { data = await api(`/api/trips?${query}`); }
  catch (error) { note.textContent = ""; return banner(error.message); }

  const fresh = data.trips.filter((trip) => !trip.in_album).length;
  if (data.scanned_at) {
    note.replaceChildren(`${data.assets} media · scanned `,
      el("b", {}, data.scanned_at.replace("T", " ").slice(0, 16)),
      data.albums_error ? ` · albums not checked: ${data.albums_error}`
        : ` · ${fresh} of ${data.trips.length} trips have no album`);
  } else {
    note.textContent = "no scan yet";
  }
  state.trips = data;
  renderTripList();
}

function renderTripList() {
  const list = $("trip-list");
  const data = state.trips;
  if (!data) return;
  list.replaceChildren();

  if (!data.trips.length) {
    list.append(el("div", { class: "muted" }, data.scanned_at
      ? "No trips found. Try a smaller “away km” or a lower minimum of media."
      : "Press “Scan the library” to look for trips."));
    return;
  }

  const needle = $("trip-filter").value.trim().toLocaleLowerCase();
  const shown = needle
    ? data.trips.filter(t => t.name.toLocaleLowerCase().includes(needle))
    : data.trips;
  if (!shown.length) {
    list.append(el("div", { class: "muted" },
      `No trips match “${$("trip-filter").value.trim()}”.`));
    return;
  }

  for (const trip of shown) {
    const strips = editStrips({ ...trip, matched: trip.total });
    const known = !!trip.in_album;
    const card = el("div", {
        class: `card ${data.albums_error ? "" : known ? "has-album" : "no-album"}` },
      el("h3", {}, trip.name),
      // "covered by" is a saved rule whose dates span the trip. Worth its own
      // tag only when it is not the album already shown: a rule not run yet.
      (known || coveredElsewhere(trip)) ? el("div", { class: "pills" },
        known ? el("span", { class: "pill on" },
                   `in Immich: ${trip.in_album.name} (${trip.in_album.share}%)`) : "",
        coveredElsewhere(trip) ? el("span", { class: "pill" },
                   `rule: ${trip.covered_by}`) : "") : "",
      el("div", { class: "meta" },
        `${trip.start} → ${trip.end} · ${trip.days} days · ${trip.total} media ` +
        `(${trip.located} located, ${trip.unlocated} without GPS)`),
      el("div", { class: "meta" },
        [...trip.countries, ...trip.cities.slice(0, 3)].join(", ")),
      ...strips,
      el("div", { class: "bar" }, button("Open in builder", () => fromTrip(trip))));
    list.append(card);
  }
}

function coveredElsewhere(trip) {
  return trip.covered_by && trip.covered_by !== (trip.in_album && trip.in_album.name);
}

function fromTrip(trip) {
  state.draft = emptyDraft();
  state.draft.name = trip.name;
  state.draft.match.from = trip.start;
  state.draft.match.to = trip.end;
  state.draft.match.countries = trip.countries.slice(0, 3);
  state.draft.match.include_unlocated = true;
  state.draft.hint_kind = "trip";
  state.draft.hint_order = "trip";
  fillForm();
  showTab("builder");
}

/* -- small helpers ------------------------------------------------------ */

/* The first and the last few pictures, labelled, so the start and the end of a
 * date range can both be checked. A short list is one strip with no label.
 * With `taken` (the builder preview), each picture can be made the album's
 * first or last photo: everything taken before or after it is left out. */
function editStrips(data, taken = null) {
  // The hover card offers the picks, on the large picture: the thumbnail
  // only carries the time they would set.
  const picture = (id) => {
    const img = el("img", { src: `/api/thumb/${id}`, loading: "lazy", alt: "" });
    if (taken && taken[id]) img.dataset.taken = taken[id];
    return img;
  };
  const strip = (ids) => {
    const box = el("div", { class: "strip" });
    for (const id of ids) box.append(picture(id));
    return box;
  };
  const first = data.thumbnails || [];
  const last = data.thumbnails_last || [];
  if (!first.length) return [];
  if (!last.length) return [strip(first)];
  const long = first.length > PREVIEW_EDGE;
  if (!long) {
    return [
      el("div", { class: "strip-label" }, `First ${first.length}, oldest first`),
      strip(first),
      el("div", { class: "strip-label" }, `Last ${last.length}, the end of the range`),
      strip(last),
    ];
  }
  const total = data.matched || first.length + last.length;
  return [
    el("div", { class: "strip-label" },
       `First ${first.length} of ${total}, oldest first — scroll →`),
    scroller(strip(first)),
    el("div", { class: "strip-label" },
       `Last ${last.length} of ${total}, the end of the range — ← scroll`),
    scroller(strip(last), { fromEnd: true, offset: total - last.length }),
  ];
}

const PREVIEW_EDGE = 12;     // below this the preview's strips do not scroll

/* A strip in one scrolling row, to look for where an album really begins or
 * ends. The wheel scrolls it sideways, and while it moves a bubble names the
 * time of the picture at the edge that matters: the first one in view, or for
 * the end of the album (`fromEnd`, which starts scrolled right) the last one. */
function scroller(box, { fromEnd = false, offset = 0 } = {}) {
  box.classList.add("scroll");
  const bubble = el("div", { class: `scroll-bubble${fromEnd ? " end" : ""}`, hidden: "" });
  let fade = null;
  let placed = !fromEnd;
  const atEdge = () => {
    const { left, right } = box.getBoundingClientRect();
    const images = [...box.querySelectorAll("img")];
    const index = fromEnd
      ? images.findLastIndex((img) => img.getBoundingClientRect().left < right - 1)
      : images.findIndex((img) => img.getBoundingClientRect().right > left + 1);
    return index < 0 ? null : { img: images[index], number: offset + index + 1 };
  };
  if (fromEnd) {
    requestAnimationFrame(() => {
      if (box.scrollWidth <= box.clientWidth) placed = true;   // nothing to jump
      box.scrollLeft = box.scrollWidth;
    });
  }
  box.addEventListener("scroll", () => {
    if (!placed) { placed = true; return; }   // the jump to the end, not the user
    const seen = atEdge();
    if (!seen) return;
    bubble.textContent = [fmt.when(seen.img.dataset.taken) || "time unknown",
                          `#${seen.number}`].join(" · ");
    bubble.hidden = false;
    clearTimeout(fade);
    fade = setTimeout(() => { bubble.hidden = true; }, 1000);
  });
  box.addEventListener("wheel", (event) => {
    if (!event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    const end = box.scrollWidth - box.clientWidth;
    const moves = event.deltaY < 0 ? box.scrollLeft > 0 : box.scrollLeft < end - 1;
    if (!moves) return;            // at an end the page scrolls on as usual
    event.preventDefault();
    box.scrollLeft += event.deltaY;
  }, { passive: false });
  return el("div", { class: "scroll-wrap" }, bubble, box);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  for (const child of children) {
    if (child === "" || child === null || child === undefined) continue;
    node.append(child);
  }
  return node;
}

function button(label, onclick) {
  const node = el("button", {}, label);
  node.onclick = onclick;
  return node;
}

function removableChip(label, remove) {
  const chip = el("span", { class: "chip" }, label, el("span", { class: "x" }, "×"));
  chip.onclick = remove;
  chip.title = "remove";
  return chip;
}

/* -- start -------------------------------------------------------------- */

async function start() {
  bindDraft();
  try {
    const who = await api("/api/whoami");
    $("whoami-name").textContent = who.protected ? `signed in as ${who.user}` : "";
    $("sign-out").hidden = !who.protected;
  } catch (_) { /* the redirect above already handled it */ }
  $("reload-config").addEventListener("click", async () => {
    try {
      await post("/api/reload-config", {});
      banner("Config reload requested.", true);
    } catch (error) { banner(error.message); }
  });
  await loadAlbums();
  try { state.groups = (await api("/api/groups")).groups; }
  catch (error) { banner(error.message); }
  await loadAccounts();
  loadImmichAlbums();
  fillPlaces("country");
  fillForm();
  navigate(location.pathname === "/" ? "/albums" : location.pathname, { replace: true });
}

start();

/* -- hover card: a large picture with everything Immich knows about it ---- */

const hover = { timer: null, epoch: 0, card: null, details: new Map() };
const THUMB = /^\/api\/thumb\/([A-Za-z0-9-]+)/;

function hoverCard() {
  if (!hover.card) {
    hover.card = el("div", { id: "hover-card", hidden: "" });
    document.body.append(hover.card);
  }
  return hover.card;
}

function hideHoverCard() {
  clearTimeout(hover.timer);
  clearTimeout(hover.hideTimer);
  hover.epoch += 1;
  if (hover.card) hover.card.hidden = true;
}

/* A card with buttons has to survive the trip from the thumbnail to the card,
 * so leaving the thumbnail hides it only after a moment, which entering the
 * card cancels. */
function hideHoverCardSoon() {
  clearTimeout(hover.hideTimer);
  const interactive = hover.card && hover.card.classList.contains("interactive");
  if (!interactive) return hideHoverCard();
  hover.hideTimer = setTimeout(hideHoverCard, 300);
}

function pickActions(taken) {
  const pick = (label, key) => {
    const node = el("button", { type: "button" }, label);
    node.onclick = () => { hideHoverCard(); setBound(key, taken); };
    return node;
  };
  return el("div", { class: "hover-actions" },
    pick("⇤ Set as first photo", "from"),
    pick("Set as last photo ⇥", "to"),
    el("div", { class: "muted" },
       "Media taken before the first or after the last are left out."));
}

function assetDetails(id) {
  if (!hover.details.has(id)) {
    const request = api(`/api/asset/${id}`);
    request.catch(() => hover.details.delete(id));    // let a later hover retry
    hover.details.set(id, request);
  }
  return hover.details.get(id);
}

const fmt = {
  when: (v) => v && v.replace("T", " ").replace(/\.\d+Z?$|Z$/, ""),
  size: (n) => n && (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB`
                                  : `${Math.round(n / 1024)} kB`),
  place: (d) => [d.city, d.state, d.country].filter(Boolean).join(", "),
  gps: (d) => d.latitude != null && d.longitude != null
    ? `${d.latitude.toFixed(5)}, ${d.longitude.toFixed(5)}` : "",
  exposure: (d) => [
    d.exposure && `${d.exposure} s`,
    d.f_number && `f/${d.f_number}`,
    d.focal_length && `${d.focal_length} mm`,
    d.iso && `ISO ${d.iso}`,
  ].filter(Boolean).join("  ·  "),
  dimensions: (d) => d.width && d.height ? `${d.width} × ${d.height}` : "",
};

function detailRows(d) {
  const rows = [
    ["File", d.file_name],
    ["Folder", d.folder],
    ["Taken", [fmt.when(d.taken), d.time_zone].filter(Boolean).join("  ")],
    ["Place", fmt.place(d)],
    ["GPS", fmt.gps(d) || "none"],
    ["Camera", d.camera],
    ["Lens", d.lens],
    ["Exposure", fmt.exposure(d)],
    ["Size", [fmt.dimensions(d), fmt.size(d.size_bytes)].filter(Boolean).join("  ·  ")],
    ["Duration", d.kind === "VIDEO" ? d.duration : ""],
    ["People", d.people.join(", ")],
    ["Note", d.description],
  ];
  const list = el("dl");
  for (const [label, value] of rows) {
    if (!value) continue;
    list.append(el("dt", {}, label), el("dd", {}, String(value)));
  }
  return list;
}

function placeHoverCard(image) {
  const card = hoverCard();
  const anchor = image.getBoundingClientRect();
  const { offsetWidth: w, offsetHeight: h } = card;
  const gap = 12;
  let left = anchor.right + gap;
  if (left + w > innerWidth - 8) left = anchor.left - w - gap;
  left = Math.max(8, Math.min(left, innerWidth - w - 8));
  let top = anchor.top + anchor.height / 2 - h / 2;
  top = Math.max(8, Math.min(top, innerHeight - h - 8));
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
}

async function showHoverCard(image, id) {
  const epoch = ++hover.epoch;
  const card = hoverCard();
  const body = el("div", { class: "hover-details muted" }, "loading details…");
  const big = el("img", { src: `/api/thumb/${id}?size=preview`, alt: "" });
  big.onload = () => { if (epoch === hover.epoch) placeHoverCard(image); };
  const taken = image.dataset.taken;
  const actions = taken ? pickActions(taken) : "";
  card.classList.toggle("interactive", !!taken);
  card.replaceChildren(el("div", { class: "hover-picture" }, big),
                       el("div", { class: "hover-side" }, actions, body));
  card.hidden = false;
  placeHoverCard(image);
  try {
    const details = await assetDetails(id);
    if (epoch !== hover.epoch) return;
    body.className = "hover-details";
    body.replaceChildren(detailRows(details));
  } catch (error) {
    if (epoch !== hover.epoch) return;
    body.textContent = `No details: ${error.message}`;
  }
  placeHoverCard(image);
}

document.addEventListener("mouseover", (event) => {
  const image = event.target instanceof Element ? event.target.closest("img") : null;
  const match = image && THUMB.exec(image.getAttribute("src") || "");
  if (!match || image.closest("#hover-card") || !$("lightbox").hidden) return;
  clearTimeout(hover.timer);
  clearTimeout(hover.hideTimer);
  hover.timer = setTimeout(() => showHoverCard(image, match[1]), 250);
});

document.addEventListener("mouseout", (event) => {
  const image = event.target instanceof Element ? event.target.closest("img") : null;
  if (image && !image.closest("#hover-card")
      && THUMB.test(image.getAttribute("src") || "")) hideHoverCardSoon();
});

// On its way to the card the pointer may cross a neighbouring thumbnail;
// reaching the card cancels both that one's card and the pending hide.
hoverCard().addEventListener("mouseenter", () => {
  clearTimeout(hover.timer);
  clearTimeout(hover.hideTimer);
});
hoverCard().addEventListener("mouseleave", hideHoverCard);
document.addEventListener("scroll", hideHoverCard, true);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideHoverCard();
});
