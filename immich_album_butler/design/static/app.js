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
  accounts: [],         // other accounts on this server, for sharing
  accountsNote: "",
};

function emptyDraft() {
  return {
    slug: "", name: "", enabled: true, sync: "add", schedule: "inherit",
    cover: "auto", share_with: [], share_role: "viewer",
    match: {
      from: null, to: null, countries: [], states: [], cities: [],
      people: [], people_mode: "any", include_unlocated: true,
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

/* -- tabs --------------------------------------------------------------- */

$("tabs").addEventListener("click", (event) => {
  const button = event.target.closest(".tab");
  if (!button) return;
  for (const el of document.querySelectorAll(".tab")) {
    el.classList.toggle("active", el === button);
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.classList.toggle("active", panel.id === button.dataset.tab);
  }
  if (button.dataset.tab === "albums") loadAlbums();
  if (button.dataset.tab === "trips") loadTrips(false);
});

function showTab(name) {
  document.querySelector(`.tab[data-tab="${name}"]`).click();
}

/* -- albums tab --------------------------------------------------------- */

async function loadAlbums() {
  let data;
  try { data = await api("/api/albums"); }
  catch (error) { return banner(error.message); }

  state.saved = data.albums;
  $("albums-note").textContent =
    `default schedule: ${data.default_schedule}`;
  $("schedule").querySelector('option[value="inherit"]').textContent =
    `inherit the global default: ${data.default_schedule}`;
  const list = $("album-list");
  list.replaceChildren();

  if (!data.albums.length) {
    list.append(el("div", { class: "muted" },
      "No albums configured yet. Start one from the Builder, or from a detected trip."));
  }

  for (const album of data.albums) {
    const status = album.last_error
      ? el("span", { class: "pill err" }, "last run failed")
      : album.enabled ? "" : el("span", { class: "pill off" }, "disabled");

    const card = el("div", { class: "card album" },
      album.cover_asset
        ? el("img", { class: "album-cover", src: `/api/thumb/${album.cover_asset}`,
                      loading: "lazy", alt: "" })
        : el("div", { class: "album-cover none" }),
      el("h3", {}, album.immich_name || album.name, " ", status),
      el("div", { class: "meta" },
        describe(album.match), el("br"),
        `${album.schedule}${album.schedule_inherited ? " (inherited)" : ""}`,
        album.sync === "mirror" ? " · mirrored" : "",
        album.cover && album.cover !== "auto" ? ` · cover: ${album.cover}` : "",
        (album.share_with || []).length
          ? ` · shared with ${album.share_with.join(", ")}` : ""),
      album.last_error
        ? el("div", { class: "warn bad" }, album.last_error)
        : el("div", { class: "muted" },
             album.last_result || "not run yet"),
      el("div", { class: "bar" },
        button("Edit", () => editAlbum(album)),
        button("Dry run", () => runAlbum(album.slug, true)),
        button("Run now", () => runAlbum(album.slug, false))));
    list.append(card);
  }

  for (const problem of data.errors || []) banner(problem);
}

function describe(match) {
  const parts = [];
  const bound = (v) => v ? v.replace("T", " ").slice(0, 16) : "…";
  if (match.from || match.to) parts.push(`${bound(match.from)} → ${bound(match.to)}`);
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

$("new-album").onclick = () => { state.draft = emptyDraft(); fillForm(); showTab("builder"); };

$("new-person-album").onclick = () => {
  state.draft = emptyDraft();
  state.draft.match.include_unlocated = true;
  fillForm();
  showTab("builder");
  $("person-search").focus();
  banner("Pick one or more people. Leave the dates and places empty: that is " +
         "all a person album needs.", true);
};

function editAlbum(album) {
  state.draft = {
    slug: album.slug, name: album.name, enabled: album.enabled,
    sync: album.sync, cover: album.cover || "auto",
    share_with: [...(album.share_with || [])],
    share_role: album.share_role || "viewer",
    schedule: album.schedule_inherited ? "inherit" : album.schedule,
    match: { ...album.match },
  };
  fillForm();
  showTab("builder");
}

/* -- builder: form <-> draft -------------------------------------------- */

function fillForm() {
  const { draft } = state;
  $("album-name").value = draft.name;
  fillDates();
  $("people-mode").value = draft.match.people_mode;
  $("include-unlocated").checked = draft.match.include_unlocated;
  $("enabled").checked = draft.enabled;
  $("mirror").checked = draft.sync === "mirror";
  fillCover(draft.cover || "auto");
  fillSharing(draft.share_with || [], draft.share_role || "viewer");
  $("schedule").value = [...$("schedule").options].some(o => o.value === draft.schedule)
    ? draft.schedule : "inherit";
  $("delete-config").hidden = !state.saved.some(a => a.slug === draft.slug);
  renderChosenPeople();
  renderChosenPlaces();
  refreshPreview();
}

/* From and To hold a day, or the moment a picked first/last photo was taken
 * ("2023-05-28T14:32:10"). The date inputs show the day; an exact time shows
 * as a chip underneath, and removing it widens back to the whole day. */
function fillDates() {
  const { match } = state.draft;
  $("date-from").value = (match.from || "").slice(0, 10);
  $("date-to").value = (match.to || "").slice(0, 10);
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
  refreshPreview();
}

function bindDraft() {
  $("album-name").oninput = (e) => { state.draft.name = e.target.value; };
  $("date-from").onchange = (e) => setBound("from", e.target.value || null);
  $("date-to").onchange = (e) => setBound("to", e.target.value || null);
  $("people-mode").onchange = (e) => set("people_mode", e.target.value);
  $("include-unlocated").onchange = (e) => set("include_unlocated", e.target.checked);
  $("enabled").onchange = (e) => { state.draft.enabled = e.target.checked; };
  $("mirror").onchange = (e) => {
    state.draft.sync = e.target.checked ? "mirror" : "add";
    if (e.target.checked) {
      banner("Mirroring needs the albumAsset.delete scope on the API key.", true);
    }
  };
  $("schedule").onchange = (e) => {
    state.draft.schedule = e.target.value;
    refreshPreview();
  };
  $("cover").onchange = () => { readCover(); refreshPreview(); };
  $("cover-name").oninput = debounce(() => { readCover(); refreshPreview(); }, 350);
  $("share-with").onchange = () => { readSharing(); refreshPreview(); };
  // A plain click toggles an account, so one can be taken off the list again
  // without knowing about Ctrl-click.
  $("share-with").onmousedown = (event) => {
    if (!(event.target instanceof HTMLOptionElement)) return;
    event.preventDefault();
    event.target.selected = !event.target.selected;
    $("share-with").focus();
    $("share-with").dispatchEvent(new Event("change"));
  };
  $("share-role").onchange = () => { readSharing(); refreshPreview(); };
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
  fillSharing(state.draft.share_with || [], state.draft.share_role || "viewer");
}

function fillSharing(chosen, role) {
  const box = $("share-with");
  const wanted = new Set(chosen.map(name => name.toLowerCase()));
  box.replaceChildren();
  for (const account of state.accounts || []) {
    const label = account.name || account.email;
    const option = el("option", { value: label }, label);
    option.selected = wanted.has(label.toLowerCase()) ||
                      wanted.has((account.email || "").toLowerCase());
    box.append(option);
  }
  // A name in the rule that no longer answers to an account is kept and shown,
  // rather than quietly dropped on the next save.
  for (const name of chosen) {
    if (![...box.options].some(o => o.selected && o.value === name)) {
      const option = el("option", { value: name }, `${name} (unknown)`);
      option.selected = true;
      box.append(option);
    }
  }
  $("share-role").value = role;
  $("share-role-row").hidden = chosen.length === 0;
  if (state.accountsNote) $("share-note").textContent = state.accountsNote;
}

function readSharing() {
  const chosen = [...$("share-with").selectedOptions].map(o => o.value);
  state.draft.share_with = chosen;
  state.draft.share_role = $("share-role").value;
  $("share-role-row").hidden = chosen.length === 0;
}

/* The cover is one value in the config but two controls here: a list of rules
   plus, for "a picture I name", the file name itself. */
const COVER_RULES = ["auto", "everyone", "newest", "oldest"];

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
  refreshPreview();
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
    for (const person of data.people) hits.push({ name: person.name });
  } catch (error) { return banner(error.message); }

  if (!hits.length) {
    box.append(el("div", { class: "muted" }, `Nobody named “${query}”.`));
    return;
  }
  for (const hit of hits) {
    const chip = el("span", { class: hit.group ? "chip group" : "chip" },
      hit.name, hit.group ? el("span", { class: "muted" },
        ` ${hit.members.length}` ) : "");
    chip.onclick = () => addPerson(hit.name);
    box.append(chip);
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

/* -- builder: dates ----------------------------------------------------- */

document.querySelectorAll("[data-preset]").forEach((button) => {
  button.onclick = () => {
    const { match } = state.draft;
    const anchor = match.from || match.to || new Date().toISOString().slice(0, 10);
    const [year, month] = anchor.split("-");
    if (button.dataset.preset === "clear") { match.from = match.to = null; }
    if (button.dataset.preset === "year") {
      match.from = `${year}-01-01`; match.to = `${year}-12-31`;
    }
    if (button.dataset.preset === "month") {
      match.from = `${year}-${month}-01`;
      match.to = new Date(Number(year), Number(month), 0).toISOString().slice(0, 10);
    }
    if (button.dataset.preset === "pad") {
      // Whole days again: widening drops a picked first or last photo.
      if (match.from) match.from = shiftDays(match.from.slice(0, 10), -1);
      if (match.to) match.to = shiftDays(match.to.slice(0, 10), +1);
    }
    fillDates();
    refreshPreview();
  };
});

function shiftDays(iso, days) {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
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
  const empty = !match.from && !match.to && !match.people.length &&
    !match.countries.length && !match.states.length && !match.cities.length;
  if (empty) {
    box.replaceChildren(el("div", { class: "muted" },
      "Pick a date range, a place or a person to see a preview."));
    return;
  }

  const token = ++state.previewToken;
  box.replaceChildren(el("div", { class: "spin" }, "Counting…"));
  let data;
  try { data = await post("/api/preview", state.draft); }
  catch (error) {
    if (token !== state.previewToken) return;
    box.replaceChildren(el("div", { class: "warn bad" }, error.message));
    return;
  }
  if (token !== state.previewToken) return;   // a newer preview already won

  state.immichName = data.immich_name;
  const children = [
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
  $("next-run").textContent = data.next_run
    ? `next automatic run: ${data.next_run.replace("T", " ")}`
    : "never runs automatically";
  box.replaceChildren(...children);
}, 350);

/* -- builder: actions --------------------------------------------------- */

$("save").onclick = async () => {
  if (!state.draft.name.trim()) return banner("Give the album a name first.");
  try {
    const result = await post("/api/albums", state.draft);
    state.draft.slug = result.saved;
    await loadAlbums();
    $("delete-config").hidden = false;
    banner(`Saved ${result.path}`, true);
  } catch (error) { banner(error.message); }
};

$("dry-run").onclick = () => requireSaved(() => runAlbum(state.draft.slug, true));
$("run-now").onclick = () => requireSaved(() => runAlbum(state.draft.slug, false));

function requireSaved(work) {
  if (!state.saved.some(a => a.slug === state.draft.slug)) {
    return banner("Save the album first — a run writes to Immich, so it runs " +
                  "what the config says, not an unsaved draft.");
  }
  work();
}

$("delete-config").onclick = async () => {
  if (!confirm(`Delete the config for “${state.immichName || state.draft.name}”?\n\n` +
               `The album in Immich is not touched.`)) return;
  try {
    const result = await api(`/api/albums/${encodeURIComponent(state.draft.slug)}`,
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

/* -- trips tab ---------------------------------------------------------- */

$("rescan").onclick = () => loadTrips(true);

/* Hiding trips that already have an album is a per-viewer preference. */
const ONLY_NEW = "butler.trips.onlyNew";
try { $("only-new").checked = localStorage.getItem(ONLY_NEW) !== "0"; } catch {}
function applyOnlyNew() {
  $("trip-list").classList.toggle("only-new", $("only-new").checked);
}
$("only-new").onchange = () => {
  try { localStorage.setItem(ONLY_NEW, $("only-new").checked ? "1" : "0"); } catch {}
  applyOnlyNew();
};
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
  list.replaceChildren();

  if (!data.trips.length) {
    list.append(el("div", { class: "muted" }, data.scanned_at
      ? "No trips found. Try a smaller “away km” or a lower minimum of media."
      : "Press “Scan the library” to look for trips."));
    return;
  }

  for (const trip of data.trips) {
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
  await loadAlbums();
  try { state.groups = (await api("/api/groups")).groups; }
  catch (error) { banner(error.message); }
  await loadAccounts();
  fillPlaces("country");
  fillForm();
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
  if (!match || image.closest("#hover-card")) return;
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
