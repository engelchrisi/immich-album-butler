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
  const list = $("album-list");
  list.replaceChildren();

  if (!data.albums.length) {
    list.append(el("div", { class: "muted" },
      "No albums configured yet. Start one from the Builder, or from a detected trip."));
  }

  for (const album of data.albums) {
    const status = album.last_error
      ? el("span", { class: "pill err" }, "last run failed")
      : el("span", { class: album.enabled ? "pill on" : "pill off" },
           album.enabled ? "enabled" : "disabled");

    const card = el("div", { class: "card" },
      el("h3", {}, album.name, " ", status),
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
  if (match.from || match.to) parts.push(`${match.from || "…"} → ${match.to || "…"}`);
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
  $("date-from").value = draft.match.from || "";
  $("date-to").value = draft.match.to || "";
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

function bindDraft() {
  $("album-name").oninput = (e) => { state.draft.name = e.target.value; };
  $("date-from").onchange = (e) => set("from", e.target.value || null);
  $("date-to").onchange = (e) => set("to", e.target.value || null);
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
      if (match.from) match.from = shiftDays(match.from, -1);
      if (match.to) match.to = shiftDays(match.to, +1);
    }
    $("date-from").value = match.from || "";
    $("date-to").value = match.to || "";
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

  const children = [
    el("div", { class: "count" }, String(data.matched),
       el("small", {}, data.creates_album
          ? "assets — this album does not exist in Immich yet"
          : `assets matched · ${data.already_in_album} already in the album · ` +
            `${data.to_add} to add` +
            (data.to_remove ? ` · ${data.to_remove} to remove` : ""))),
  ];
  for (const warning of data.warnings || []) {
    children.push(el("div", { class: "warn" }, warning));
  }
  if (data.thumbnails.length) {
    const strip = el("div", { class: "strip" });
    for (const id of data.thumbnails) {
      strip.append(el("img", { src: `/api/thumb/${id}`, loading: "lazy", alt: "" }));
    }
    children.push(strip);
  }
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
  if (!confirm(`Delete the config for “${state.draft.name}”?\n\n` +
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
      "Nothing obvious is missing: no assets sit just outside this rule."));
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
  if (!confirm(`Add ${group.count} asset(s) to “${state.draft.name}” now?\n\n` +
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

async function loadTrips(rescan) {
  const list = $("trip-list");
  const note = $("scan-note");
  note.textContent = rescan
    ? "Scanning the whole library — this takes a while…" : "";
  if (rescan) list.replaceChildren(el("div", { class: "spin" }, "Scanning…"));

  const query = new URLSearchParams({
    rescan: rescan ? "1" : "0",
    away_km: $("away-km").value || "100",
    min_assets: $("min-assets").value || "30",
  });
  let data;
  try { data = await api(`/api/trips?${query}`); }
  catch (error) { note.textContent = ""; return banner(error.message); }

  note.textContent = data.scanned_at
    ? `${data.assets} assets, scanned ${data.scanned_at.replace("T", " ")}`
    : "no scan yet";
  list.replaceChildren();

  if (!data.trips.length) {
    list.append(el("div", { class: "muted" }, data.scanned_at
      ? "No trips found. Try a smaller “away km” or fewer minimum assets."
      : "Press “Scan the library” to look for trips."));
    return;
  }

  for (const trip of data.trips) {
    const strip = el("div", { class: "strip" });
    for (const id of trip.thumbnails.slice(0, 8)) {
      strip.append(el("img", { src: `/api/thumb/${id}`, loading: "lazy", alt: "" }));
    }
    const card = el("div", { class: "card" },
      el("h3", {}, trip.name, trip.covered_by
        ? el("span", { class: "pill on" }, `covered by ${trip.covered_by}`) : ""),
      el("div", { class: "meta" },
        `${trip.start} → ${trip.end} · ${trip.days} days · ${trip.total} assets ` +
        `(${trip.located} located, ${trip.unlocated} without GPS)`),
      el("div", { class: "meta" },
        [...trip.countries, ...trip.cities.slice(0, 3)].join(", ")),
      strip,
      el("div", { class: "bar" }, button("Open in builder", () => fromTrip(trip))));
    list.append(card);
  }
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
