"use strict";

const state = {
  medium: "anime",
  mode: null, // {type: "single", id} | {type: "mix"}
  mix: [], // [{id, title}]
  pending: [], // rendered lazily via Show more
  shown: 0,
  genreState: {}, // name -> "inc" | "exc"
  tagState: {}, // name -> "inc" | "exc"
  titleLang: localStorage.getItem("mb_title_lang") || "english",
};

const PAGE_SIZE = 60;
const MAX_MIX = 5;

const FORMATS = {
  anime: ["TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC"],
  manga: ["MANGA"],
  light_novel: ["NOVEL"],
  one_shot: ["ONE_SHOT"],
};

const GENRES = [
  "Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror",
  "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance",
  "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller",
];

const $ = (id) => document.getElementById(id);

// A browser can hold a cached index.html from one deploy alongside a fresh
// app.js from the next; a missing element must degrade to a warning, never
// an exception that kills the rest of startup and leaves buttons dead.
function on(id, event, fn) {
  const node = $(id);
  if (node) node.addEventListener(event, fn);
  else console.warn(`missing #${id} - stale cached index.html? Hard-refresh the page.`);
}

const isChecked = (id) => {
  const node = $(id);
  return node ? node.checked : false;
};

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, item);
    } else {
      url.searchParams.set(key, value);
    }
  }
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail ? JSON.stringify(body.detail) : `Request failed (${resp.status})`);
  }
  return resp.json();
}

function filterParams() {
  const params = {
    adult: $("adult").checked ? "true" : "",
    year_min: $("yearMin").value,
    year_max: $("yearMax").value,
    min_score: $("minScore").value,
    country: $("country").value,
    status: $("status").value,
    format: $("format").value,
    max_popularity: $("maxPop").value,
    genre_in: GENRES.filter((g) => state.genreState[g] === "inc"),
    genre_ex: GENRES.filter((g) => state.genreState[g] === "exc"),
    tag_in: Object.keys(state.tagState).filter((t) => state.tagState[t] === "inc"),
    tag_ex: Object.keys(state.tagState).filter((t) => state.tagState[t] === "exc"),
  };
  const maxLen = $("maxLen").value;
  if (maxLen) {
    if (state.medium === "anime") params.max_episodes = maxLen;
    else params.max_chapters = maxLen;
  }
  if (isChecked("malExclude") && $("malUser").value.trim()) {
    params.mal_user = $("malUser").value.trim();
  }
  if (isChecked("alExclude") && $("alUser").value.trim()) {
    params.anilist_user = $("alUser").value.trim();
  }
  // exclude_list is repeatable: every checked tracker list combines.
  const excludeLists = [];
  if (isChecked("kitsuExclude")) excludeLists.push("kitsu");
  if (isChecked("ytExclude")) excludeLists.push("yamtrack");
  if (excludeLists.length) params.exclude_list = excludeLists;
  if (isChecked("keepPlanned")) params.keep_planned = "true";
  return params;
}

function weightParams() {
  const params = {
    w_semantic: $("wSemantic").value / 100,
    w_tags: $("wTags").value / 100,
    w_genres: $("wGenres").value / 100,
  };
  const taste = $("wTaste");
  if (taste && taste.value > 0) params.w_taste = taste.value / 100;
  return params;
}

function recommendParams() {
  return {
    ...weightParams(),
    ...filterParams(),
    cross_media: $("crossMedia").checked ? "true" : "false",
    exclude_franchise: $("excludeFranchise").checked ? "true" : "false",
    limit: 200,
  };
}

// The status filter select is id="status"; the message area must use a
// different id or getElementById would return the select and wipe its options.
function setStatus(text) {
  $("statusMsg").textContent = text || "";
}

function mediaTitle(media) {
  if (state.titleLang === "english") {
    return media.title_english || media.title || media.title_native || `#${media.id}`;
  }
  return media.title || media.title_english || media.title_native || `#${media.id}`;
}

function formatMembers(n) {
  if (!n) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function mediaMeta(media) {
  const parts = [];
  if (media.start_year) parts.push(media.start_year);
  if (media.format) parts.push(media.format.replaceAll("_", " "));
  if (media.average_score) parts.push(String(media.average_score));
  const members = formatMembers(media.popularity);
  if (members) parts.push(members);
  if (media.medium && media.medium !== state.medium) parts.push(media.medium.replaceAll("_", " "));
  return parts.join(" / ");
}

function anilistUrl(media) {
  const kind = media.medium === "anime" ? "anime" : "manga";
  return `https://anilist.co/${kind}/${media.id}`;
}

function malUrl(media) {
  if (!media.id_mal) return null;
  const kind = media.medium === "anime" ? "anime" : "manga";
  return `https://myanimelist.net/${kind}/${media.id_mal}`;
}

function card(media, badgeText, onClick, components) {
  const el = document.createElement("div");
  el.className = "card";
  if (components) {
    el.title =
      `Match components - synopsis: ${Math.round(components.semantic * 100)},` +
      ` tags: ${Math.round(components.tags * 100)},` +
      ` genres: ${Math.round(components.genres * 100)}`;
  }
  const img = document.createElement("img");
  img.className = "cover";
  img.loading = "lazy";
  img.alt = "";
  if (media.cover_image_large || media.cover_image) {
    img.src = media.cover_image_large || media.cover_image;
  }
  el.appendChild(img);
  if (badgeText) {
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = badgeText;
    el.appendChild(badge);
  }
  const addBtn = document.createElement("button");
  addBtn.className = "add-mix";
  addBtn.textContent = "+";
  addBtn.title = "Add to seed mix";
  addBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    addToMix(media);
  });
  el.appendChild(addBtn);

  const info = document.createElement("div");
  info.className = "info";
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = mediaTitle(media);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = mediaMeta(media);
  const link = document.createElement("a");
  link.className = "ext";
  link.href = anilistUrl(media);
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "AniList";
  link.addEventListener("click", (e) => e.stopPropagation());
  info.appendChild(title);
  info.appendChild(meta);
  info.appendChild(link);
  el.appendChild(info);
  el.addEventListener("click", onClick);
  return el;
}

function clearResults() {
  $("seed").innerHTML = "";
  $("related").innerHTML = "";
  $("results").innerHTML = "";
  const more = document.querySelector(".show-more");
  if (more) more.remove();
  state.pending = [];
  state.shown = 0;
  setStatus("");
}

function renderPending() {
  const grid = $("results");
  const next = state.pending.slice(state.shown, state.shown + PAGE_SIZE);
  for (const item of next) {
    grid.appendChild(
      card(item.media, `${item.similarity}%`, () => loadRecommendations(item.media.id), item.components)
    );
  }
  state.shown += next.length;
  const existing = document.querySelector(".show-more");
  if (existing) existing.remove();
  if (state.shown < state.pending.length) {
    const btn = document.createElement("button");
    btn.className = "show-more";
    btn.textContent = `Show more (${state.pending.length - state.shown} left)`;
    btn.addEventListener("click", renderPending);
    grid.after(btn);
  }
}

async function runSearch(query) {
  state.mode = null;
  clearResults();
  if (!query.trim()) return;
  setStatus("Searching...");
  try {
    const data = await api("/search", {
      q: query.trim(),
      medium: state.medium,
      adult: $("adult").checked ? "true" : "",
    });
    setStatus(data.results.length ? "" : "No matches.");
    const grid = $("results");
    for (const media of data.results) {
      grid.appendChild(card(media, null, () => loadRecommendations(media.id)));
    }
  } catch (err) {
    setStatus(err.message);
  }
}

function renderSeedCard(seed) {
  const el = document.createElement("div");
  el.className = "seed-card";
  const img = document.createElement("img");
  if (seed.cover_image_large || seed.cover_image) {
    img.src = seed.cover_image_large || seed.cover_image;
  }
  img.alt = "";
  const body = document.createElement("div");
  const h3 = document.createElement("h3");
  h3.textContent = mediaTitle(seed);
  const meta = document.createElement("div");
  meta.className = "meta muted";
  meta.textContent = [mediaMeta(seed), (seed.genres || []).join(", ")]
    .filter(Boolean)
    .join(" — ");
  const links = document.createElement("div");
  links.className = "ext-links";
  const al = document.createElement("a");
  al.href = anilistUrl(seed);
  al.target = "_blank";
  al.rel = "noopener";
  al.textContent = "AniList";
  links.appendChild(al);
  const mal = malUrl(seed);
  if (mal) {
    const ml = document.createElement("a");
    ml.href = mal;
    ml.target = "_blank";
    ml.rel = "noopener";
    ml.textContent = "MyAnimeList";
    links.appendChild(ml);
  }
  const desc = document.createElement("p");
  desc.textContent = seed.description || "";
  body.appendChild(h3);
  body.appendChild(meta);
  body.appendChild(links);
  body.appendChild(desc);
  el.appendChild(img);
  el.appendChild(body);
  return el;
}

function renderSeeds(seeds) {
  const wrap = $("seed");
  wrap.innerHTML = "";
  if (seeds.length > 3) {
    // Feed mode: many seeds render as a compact chip row, not full cards.
    const hint = document.createElement("div");
    hint.className = "chips-hint";
    hint.textContent = "Based on these titles from your list:";
    wrap.appendChild(hint);
    const box = document.createElement("div");
    box.className = "chips";
    for (const seed of seeds) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = mediaTitle(seed);
      chip.addEventListener("click", () => loadRecommendations(seed.id));
      box.appendChild(chip);
    }
    wrap.appendChild(box);
    return;
  }
  for (const seed of seeds) {
    wrap.appendChild(renderSeedCard(seed));
  }
}

function renderRelated(related) {
  const wrap = $("related");
  wrap.innerHTML = "";
  for (const item of related) {
    const chip = document.createElement("span");
    chip.className = "related-chip";
    chip.textContent = `${item.relation_type.toLowerCase()}: ${mediaTitle(item.media)}`;
    chip.addEventListener("click", () => loadRecommendations(item.media.id));
    wrap.appendChild(chip);
  }
}

function renderRecommendations(data) {
  renderSeeds(data.seeds && data.seeds.length ? data.seeds : [data.seed]);
  renderRelated(data.related);
  state.pending = data.results;
  state.shown = 0;
  renderPending();
  setStatus(data.results.length ? "" : "No recommendations pass the active filters.");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadRecommendations(mediaId, opts = {}) {
  state.mode = { type: "single", id: mediaId };
  clearResults();
  setStatus("Finding similar titles...");
  if (opts.updateHash !== false) {
    const target = `#/rec/${state.medium}/${mediaId}`;
    if (location.hash !== target) location.hash = target;
  }
  try {
    const data = await api(`/recommend/${mediaId}`, recommendParams());
    renderRecommendations(data);
  } catch (err) {
    setStatus(err.message);
  }
}

async function loadMix() {
  if (!state.mix.length) return;
  if (state.mix.length === 1) {
    loadRecommendations(state.mix[0].id);
    return;
  }
  state.mode = { type: "mix" };
  clearResults();
  setStatus("Blending seeds...");
  try {
    const data = await api("/recommend", {
      ids: state.mix.map((m) => m.id),
      ...recommendParams(),
    });
    renderRecommendations(data);
  } catch (err) {
    setStatus(err.message);
  }
}

function renderMix() {
  const wrap = $("mixChips");
  wrap.innerHTML = "";
  for (const entry of state.mix) {
    const chip = document.createElement("span");
    chip.className = "chip inc";
    chip.textContent = `${entry.title} (remove)`;
    chip.addEventListener("click", () => {
      state.mix = state.mix.filter((m) => m.id !== entry.id);
      renderMix();
    });
    wrap.appendChild(chip);
  }
  $("mixBlock").hidden = state.mix.length === 0;
}

function addToMix(media) {
  if (state.mix.some((m) => m.id === media.id)) return;
  if (state.mix.length >= MAX_MIX) {
    setStatus(`Seed mix is full (max ${MAX_MIX}).`);
    return;
  }
  state.mix.push({ id: media.id, title: mediaTitle(media) });
  renderMix();
}

async function loadAccountSettings() {
  try {
    const data = await api("/settings");
    if (data.anilist_username) $("alUser").value = data.anilist_username;
    if (data.mal_username) $("malUser").value = data.mal_username;
    if (data.kitsu_username && $("kitsuUser")) $("kitsuUser").value = data.kitsu_username;
    if (data.yamtrack_url) $("ytUrl").value = data.yamtrack_url;
    if (data.yamtrack_token_set) $("ytToken").placeholder = "saved (enter to replace)";
  } catch {
    // Settings are a convenience; the app works without them.
  }
}

async function saveAccountSettings() {
  const body = {
    anilist_username: $("alUser").value.trim(),
    mal_username: $("malUser").value.trim(),
    yamtrack_url: $("ytUrl").value.trim(),
  };
  // Omit (not blank) the field when the input is missing from a stale page:
  // an empty string would clear the stored value server-side.
  if ($("kitsuUser")) body.kitsu_username = $("kitsuUser").value.trim();
  // Token is write-only: only send it when the user typed a new one.
  if ($("ytToken").value.trim()) body.yamtrack_token = $("ytToken").value.trim();
  $("acctStatus").textContent = "Saving...";
  try {
    const resp = await fetch("/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`Save failed (${resp.status})`);
    const data = await resp.json();
    $("ytToken").value = "";
    if (data.yamtrack_token_set) $("ytToken").placeholder = "saved (enter to replace)";
    $("acctStatus").textContent = "Saved.";
  } catch (err) {
    $("acctStatus").textContent = err.message;
  }
}

async function refreshYamtrack() {
  $("ytStatus").textContent = "Fetching from Yamtrack...";
  try {
    const resp = await fetch("/yamtrack/refresh", { method: "POST" });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Refresh failed (${resp.status})`);
    }
    const data = await resp.json();
    let text = `${data.entry_count} entries cached`;
    if (data.skipped) text += ` (${data.skipped} non-MAL entries skipped)`;
    $("ytStatus").textContent = text;
  } catch (err) {
    $("ytStatus").textContent = err.message;
  }
}

async function refreshKitsu() {
  $("kitsuStatus").textContent = "Fetching from Kitsu...";
  try {
    const resp = await fetch("/kitsu/refresh", { method: "POST" });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Refresh failed (${resp.status})`);
    }
    const data = await resp.json();
    let text = `${data.entry_count} entries cached`;
    if (data.skipped) text += ` (${data.skipped} unmapped entries skipped)`;
    $("kitsuStatus").textContent = text;
  } catch (err) {
    $("kitsuStatus").textContent = err.message;
  }
}

async function loadForYou() {
  const anilistName = $("alUser").value.trim();
  const malName = $("malUser").value.trim();
  const hasListSource = isChecked("kitsuExclude") || isChecked("ytExclude");
  if (!anilistName && !malName && !hasListSource) {
    setStatus("Enter an AniList or MAL username, or sync Kitsu/Yamtrack and"
      + " tick its exclude box, then try again.");
    return;
  }
  state.mode = { type: "foryou" };
  clearResults();
  setStatus("Sampling your list...");
  try {
    const params = { ...recommendParams(), medium: state.medium };
    if (anilistName) params.anilist_user = anilistName;
    else if (malName) params.mal_user = malName;
    const seedsEl = $("forYouSeeds");
    if (seedsEl && seedsEl.value) params.seeds = seedsEl.value;
    const data = await api("/foryou", params);
    renderRecommendations(data);
  } catch (err) {
    setStatus(err.message);
  }
}

async function surprise() {
  setStatus("Rolling the dice...");
  try {
    const media = await api("/random", { medium: state.medium, ...filterParams() });
    await loadRecommendations(media.id);
  } catch (err) {
    setStatus(err.message);
  }
}

async function refreshMal() {
  const username = $("malUser").value.trim();
  if (!username) {
    $("malStatus").textContent = "Enter a MAL username first.";
    return;
  }
  $("malStatus").textContent = "Fetching lists (this can take a minute)...";
  try {
    const resp = await fetch(`/mal/${encodeURIComponent(username)}/refresh`, { method: "POST" });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Refresh failed (${resp.status})`);
    }
    const data = await resp.json();
    $("malStatus").textContent = data.lists
      .map((l) => `${l.list_type}: ${l.entry_count} entries`)
      .join(", ");
  } catch (err) {
    $("malStatus").textContent = err.message;
  }
}

function populateFormats() {
  const select = $("format");
  select.innerHTML = '<option value="">Any</option>';
  for (const fmt of FORMATS[state.medium] || []) {
    const opt = document.createElement("option");
    opt.value = fmt;
    opt.textContent = fmt.replaceAll("_", " ");
    select.appendChild(opt);
  }
  $("formatWrap").style.display = state.medium === "anime" ? "" : "none";
}

function renderGenreChips() {
  const wrap = $("genreChips");
  wrap.innerHTML = "";
  for (const genre of GENRES) {
    const chip = document.createElement("span");
    chip.className = "chip";
    const mode = state.genreState[genre];
    if (mode === "inc") chip.classList.add("inc");
    if (mode === "exc") chip.classList.add("exc");
    chip.textContent = genre;
    chip.addEventListener("click", () => {
      if (state.genreState[genre] === "inc") state.genreState[genre] = "exc";
      else if (state.genreState[genre] === "exc") delete state.genreState[genre];
      else state.genreState[genre] = "inc";
      renderGenreChips();
      rerunActive();
    });
    wrap.appendChild(chip);
  }
}

function renderTagChips() {
  const wrap = $("tagChips");
  wrap.innerHTML = "";
  for (const [name, mode] of Object.entries(state.tagState)) {
    const chip = document.createElement("span");
    chip.className = `chip ${mode}`;
    chip.textContent = `${name} (remove)`;
    chip.addEventListener("click", () => {
      delete state.tagState[name];
      renderTagChips();
      rerunActive();
    });
    wrap.appendChild(chip);
  }
}

function addTagFilter(mode) {
  const name = $("tagInput").value.trim();
  if (!name) return;
  state.tagState[name] = mode;
  $("tagInput").value = "";
  renderTagChips();
  rerunActive();
}

async function loadTagVocabulary() {
  try {
    const data = await api("/tags");
    const list = $("tagList");
    for (const name of data.tags) {
      const opt = document.createElement("option");
      opt.value = name;
      list.appendChild(opt);
    }
  } catch {
    // Autocomplete is a convenience; typing exact tag names still works.
  }
}

async function refreshAnilist() {
  const username = $("alUser").value.trim();
  if (!username) {
    $("alStatus").textContent = "Enter an AniList username first.";
    return;
  }
  $("alStatus").textContent = "Fetching lists...";
  try {
    const resp = await fetch(`/anilist/${encodeURIComponent(username)}/refresh`, {
      method: "POST",
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Refresh failed (${resp.status})`);
    }
    const data = await resp.json();
    $("alStatus").textContent = data.lists
      .map((l) => `${l.list_type}: ${l.entry_count} entries`)
      .join(", ");
  } catch (err) {
    $("alStatus").textContent = err.message;
  }
}

function rerunActive() {
  if (!state.mode) return;
  if (state.mode.type === "single") loadRecommendations(state.mode.id, { updateHash: false });
  else if (state.mode.type === "mix") loadMix();
  else if (state.mode.type === "foryou") loadForYou();
}

function setMedium(medium) {
  if (state.medium === medium) return;
  state.medium = medium;
  for (const button of document.querySelectorAll("#tabs button")) {
    button.classList.toggle("active", button.dataset.medium === medium);
  }
  populateFormats();
}

function applyHash() {
  const match = location.hash.match(/^#\/rec\/(anime|manga|light_novel|one_shot)\/(\d+)$/);
  if (!match) return false;
  setMedium(match[1]);
  loadRecommendations(Number(match[2]), { updateHash: false });
  return true;
}

function bindEvents() {
  for (const button of document.querySelectorAll("#tabs button")) {
    button.addEventListener("click", () => {
      setMedium(button.dataset.medium);
      state.mode = null;
      if (location.hash) history.replaceState(null, "", location.pathname);
      runSearch($("search").value);
    });
  }

  on("search", "input", debounce((e) => runSearch(e.target.value), 300));

  for (const [slider, label] of [
    ["wSemantic", "wSemanticVal"],
    ["wTags", "wTagsVal"],
    ["wGenres", "wGenresVal"],
    ["wTaste", "wTasteVal"],
  ]) {
    on(slider, "input", () => {
      $(label).textContent = $(slider).value;
    });
    on(slider, "change", rerunActive);
  }

  for (const id of ["adult", "crossMedia", "excludeFranchise", "yearMin", "yearMax",
                    "minScore", "country", "status", "format", "maxPop", "maxLen",
                    "malExclude", "alExclude", "kitsuExclude", "ytExclude", "keepPlanned"]) {
    on(id, "change", rerunActive);
  }

  on("ytRefresh", "click", refreshYamtrack);
  on("kitsuRefresh", "click", refreshKitsu);
  on("acctSave", "click", saveAccountSettings);

  on("tagReq", "click", () => addTagFilter("inc"));
  on("tagExc", "click", () => addTagFilter("exc"));
  on("alRefresh", "click", refreshAnilist);

  $("titleLang").value = state.titleLang;
  on("titleLang", "change", () => {
    state.titleLang = $("titleLang").value;
    localStorage.setItem("mb_title_lang", state.titleLang);
    if (state.mode) rerunActive();
    else if ($("search").value.trim()) runSearch($("search").value);
  });

  on("forYou", "click", loadForYou);
  on("surprise", "click", surprise);
  on("malRefresh", "click", refreshMal);
  on("mixGo", "click", loadMix);
  window.addEventListener("hashchange", () => {
    if (state.mode && state.mode.type === "single") {
      const current = `#/rec/${state.medium}/${state.mode.id}`;
      if (location.hash === current) return;
    }
    applyHash();
  });
}

populateFormats();
renderGenreChips();
bindEvents();
loadTagVocabulary();
loadAccountSettings();
applyHash();
