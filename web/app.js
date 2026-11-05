"use strict";

const state = {
  medium: "anime",
  mode: null, // {type: "single", id} | {type: "mix"}
  mix: [], // [{id, title}]
  pending: [], // rendered lazily via Show more
  shown: 0,
  genreState: {}, // name -> "inc" | "exc"
  tagState: {}, // name -> "inc" | "exc"
  tagVocabulary: [], // full tag list, for the suggestion dropdown
  tagSuggestIndex: -1, // keyboard-highlighted suggestion
  titleLang: localStorage.getItem("mb_title_lang") || "english",
  shownSeedIds: "", // seeds currently on screen, so a re-rank does not scroll
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

// When the server runs with AUTH_TOKEN set (public exposure, e.g. through a
// Cloudflare Tunnel), every API call must carry the token. It is asked for
// once on the first 401 and remembered locally.
function authHeaders() {
  const token = localStorage.getItem("mb_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function authFetch(url, options = {}) {
  const withAuth = () => fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });
  const used = localStorage.getItem("mb_token") || "";
  let resp = await withAuth();
  if (resp.status === 401) {
    // Parallel 401s (the app fires several requests on load) must not stack
    // one dialog per request: only ask when the stored token is still the
    // one this request failed with; otherwise another caller already asked
    // (window.prompt blocks the thread, serializing the continuations).
    if ((localStorage.getItem("mb_token") || "") === used) {
      const token = window.prompt("This MangaBrain server requires an access token:");
      if (token && token.trim()) localStorage.setItem("mb_token", token.trim());
    }
    if ((localStorage.getItem("mb_token") || "") !== used) resp = await withAuth();
  }
  return resp;
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
  const resp = await authFetch(url);
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
  // One control per bound, mapped to the field the medium uses. A floor was
  // missing until now, so "a long series to sink into" could not be asked
  // for at all. Unknown lengths pass both bounds: an absent episode count is
  // not evidence a title is short, nor that it is long.
  const maxLen = $("maxLen").value;
  if (maxLen) {
    if (state.medium === "anime") params.max_episodes = maxLen;
    else params.max_chapters = maxLen;
  }
  const minLen = $("minLen") ? $("minLen").value : "";
  if (minLen) {
    if (state.medium === "anime") params.min_episodes = minLen;
    else params.min_chapters = minLen;
  }
  // Positive filter only, and only for anime: a chosen language means "known
  // to be dubbed in it", and "Any" means no dub filtering at all. There is
  // deliberately no sub-only option, because an absent cast is not evidence
  // of one. English can be answered by AniList, ANN or franchise inference;
  // the other languages exist only where ANN matched the entry.
  const dubLang = $("dubLang").value;
  if (state.medium === "anime" && dubLang) params.dub_lang = dubLang;
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
  // Measured and inherited dubs are labelled differently: "dub" means AniList
  // credits an English voice actor for this entry, "dub (series)" means a
  // relative is dubbed and this entry inherited it, which is a weaker claim.
  if (media.has_english_dub) {
    parts.push(media.dub_source === "franchise" ? "dub (series)" : "dub");
  }
  return parts.join(" / ");
}

// The dubbed filter keeps only titles known to be dubbed, so a title the
// backfill has not checked yet is excluded while the filter is on. That is
// the right behaviour for a positive filter and a confusing one to meet
// unannounced, so the coverage is shown next to the toggle until it is
// complete. Checked once per session; a failure leaves the note empty rather
// than blocking the panel.
// Display names for language codes. Only the NAMES live here - the set of
// languages comes from /dub/languages, because a hardcoded set drifts as
// coverage grows and already had: the picker offered nine options while the
// database held 29. An unknown code is shown as the code rather than
// dropped, so a language that appears in the data is always selectable.
const LANGUAGE_NAMES = {
  en: "English",
  ja: "Japanese",
  es: "Spanish",
  pt: "Portuguese",
  "pt-br": "Portuguese (Brazil)",
  it: "Italian",
  de: "German",
  fr: "French",
  ko: "Korean",
  tl: "Tagalog",
  pl: "Polish",
  ar: "Arabic",
  hu: "Hungarian",
  no: "Norwegian",
  sv: "Swedish",
  he: "Hebrew",
  id: "Indonesian",
  zh: "Chinese",
  "zh-cn": "Chinese (Mandarin)",
  "zh-hk": "Chinese (Cantonese)",
  "zh-tw": "Chinese (Taiwan)",
  nl: "Dutch",
  th: "Thai",
  ru: "Russian",
  da: "Danish",
  hi: "Hindi",
  fi: "Finnish",
  tr: "Turkish",
  ca: "Catalan",
  vi: "Vietnamese",
  el: "Greek",
  cs: "Czech",
  ro: "Romanian",
  uk: "Ukrainian",
};

function languageLabel(code) {
  return LANGUAGE_NAMES[code] || code.toUpperCase();
}

// Fills the dub language picker from the catalog. Runs once per session; a
// failure leaves the markup's English-only fallback in place rather than
// emptying the control, since a filter you cannot set is worse than a short
// list.
async function loadDubLanguages() {
  if (state.dubLanguagesLoaded) return;
  state.dubLanguagesLoaded = true;
  const select = $("dubLang");
  if (!select) return;
  try {
    const data = await api("/dub/languages", {});
    const languages = (data && data.languages) || [];
    if (!languages.length) return;
    const chosen = select.value;
    // Rebuild rather than append: the markup carries English as a
    // pre-fetch fallback and it would otherwise appear twice.
    select.innerHTML = "";
    const any = document.createElement("option");
    any.value = "";
    any.textContent = "Any (no dub filter)";
    select.appendChild(any);
    for (const entry of languages) {
      const option = document.createElement("option");
      option.value = entry.code;
      option.textContent = `${languageLabel(entry.code)} (${entry.titles})`;
      select.appendChild(option);
    }
    // Keep whatever the user had selected, including a restored preference.
    if (chosen) select.value = chosen;
  } catch (err) {
    // The inline English option stays, which is the right behaviour: a
    // filter you cannot set is worse than a short list. But failing
    // silently made a deployment problem look like a feature that did not
    // work - the picker simply showed English and gave no reason - so the
    // reason goes to the console even though the UI carries on.
    console.warn(
      "dub language picker: /dub/languages failed, keeping the English" +
        " fallback. If this is a 404 the API container predates the" +
        " endpoint and needs pulling.",
      err
    );
  }
}

async function loadDubCoverage() {
  if (state.dubCoverageLoaded) return;
  state.dubCoverageLoaded = true;
  const note = $("dubNote");
  if (!note) return;
  try {
    const data = await api("/dub/coverage", {});
    if (!data || data.complete || !data.total) {
      note.textContent = "";
      return;
    }
    const pct = Math.round((100 * data.checked) / data.total);
    note.textContent = `English: ${pct}% checked`;
    note.title =
      `${data.checked} of ${data.total} anime checked against AniList for an` +
      ` English cast; ${data.dubbed} have one. Titles not yet checked are` +
      " hidden while a dub language is selected. Other languages come from" +
      " ANN and cover only the titles it could be matched to.";
  } catch {
    note.textContent = "";
  }
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
    // A described result has no percentage to show, only how many of the
    // query's facets found it.
    const badge = "badge" in item ? item.badge : `${item.similarity}%`;
    grid.appendChild(
      card(item.media, badge, () => loadRecommendations(item.media.id), item.components)
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

// Description search: the catalog ranked by how well each synopsis matches
// a sentence, rather than by name. Filters apply the same way they do to a
// browse, so "a quiet story about grief" can still be narrowed to manga
// from the 1990s with an English dub.
async function runDescribe(text) {
  const term = text.trim();
  if (term.length < 3) return;
  state.mode = null;
  clearResults();
  setStatus("Reading the catalog...");
  try {
    const params = { ...filterParams(), medium: state.medium, q: term, limit: "60" };
    const data = await api("/describe", params);
    renderDescribed(data);
  } catch (err) {
    setStatus(err.message);
  }
}

function renderDescribed(data) {
  const multi = (data.facets || []).length > 1;
  state.pending = data.results.map((item) => ({
    media: item.media,
    // No percentage here. The server returns a raw query-document cosine,
    // and display_similarity calibrates a different comparison entirely;
    // dressing one up as the other would invent confidence nobody
    // measured. Facet coverage is a fact about the query, so it is shown.
    similarity: null,
    badge: multi ? `${item.facets_hit}/${item.facet_count}` : "",
    components: null,
  }));
  state.shown = 0;
  renderPending();
  setStatus(
    data.results.length
      ? ""
      : "Nothing matches that description. Try fewer words, or loosen the filters."
  );
}

// Typing searches titles by name only, which is what the box has always
// done and is the right behaviour for picking a seed quickly. Pass
// withFilters to apply the filter panel as well, which is what the button
// does - and with an empty query that becomes a browse over the catalog,
// since /search no longer requires a term.
async function runSearch(query, withFilters = false) {
  state.mode = null;
  clearResults();
  const term = query.trim();
  if (!term && !withFilters) return;
  setStatus(term ? "Searching..." : "Browsing...");
  try {
    const params = {
      medium: state.medium,
      adult: $("adult").checked ? "true" : "",
      limit: "60",
    };
    if (term) params.q = term;
    if (withFilters) {
      Object.assign(params, filterParams());
      // /search declares exclude_lists while /recommend declares
      // exclude_list. FastAPI ignores an unknown query parameter rather
      // than erroring, so sending the recommend spelling here meant Kitsu
      // and Yamtrack exclusions were silently dropped from every filtered
      // browse. The asymmetry is the server's; this renames per endpoint.
      if (params.exclude_list) {
        params.exclude_lists = params.exclude_list;
        delete params.exclude_list;
      }
    }
    const data = await api("/search", params);
    if (data.results.length) {
      setStatus("");
    } else {
      setStatus(
        withFilters
          ? "No titles match those filters."
          : "No matches."
      );
    }
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
  meta.textContent = mediaMeta(seed);

  // Genres and top tags as chips; clicking one adds it as a require filter.
  const chips = document.createElement("div");
  chips.className = "chips seed-chips";
  for (const genre of seed.genres || []) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = genre;
    chip.title = "Require this genre";
    chip.addEventListener("click", () => {
      state.genreState[genre] = "inc";
      renderGenreChips();
      rerunActive();
    });
    chips.appendChild(chip);
  }
  for (const tag of (seed.tags || []).slice(0, 10)) {
    const chip = document.createElement("span");
    chip.className = "chip tag";
    chip.textContent = tag;
    chip.title = "Require this tag";
    chip.addEventListener("click", () => {
      state.tagState[tag] = "inc";
      renderTagChips();
      rerunActive();
    });
    chips.appendChild(chip);
  }
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
  body.appendChild(chips);
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
  const seeds = data.seeds && data.seeds.length ? data.seeds : [data.seed];
  renderSeeds(seeds);
  renderRelated(data.related);
  state.pending = data.results;
  state.shown = 0;
  renderPending();
  setStatus(data.results.length ? "" : "No recommendations pass the active filters.");

  // Only move the page when the seeds actually changed (a new title, a
  // Surprise me, a fresh For-you draw). Adjusting a slider or filter
  // re-ranks the same seeds, and yanking the reader back would lose their
  // place in the list. scrollIntoView with block "nearest" scrolls the
  // minimum needed, so nothing moves when the results are already visible -
  // unlike scrollTo(0), which jumped past the whole filter sidebar on the
  // single-column (mobile) layout.
  const seedIds = seeds.map((s) => s.id).join(",");
  if (seedIds !== state.shownSeedIds) {
    state.shownSeedIds = seedIds;
    $("seed").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
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
    const resp = await authFetch("/settings", {
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
    const resp = await authFetch("/yamtrack/refresh", { method: "POST" });
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
    const resp = await authFetch("/kitsu/refresh", { method: "POST" });
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
    if (!isChecked("useRatings")) params.use_ratings = "false";
    const data = await api("/foryou", params);
    // Pin the sampled seeds: slider and filter changes re-rank this same
    // feed via rerunForYou; only pressing the button samples fresh seeds.
    state.mode = { type: "foryou", ids: (data.seeds || []).map((s) => s.id) };
    renderRecommendations(data);
  } catch (err) {
    setStatus(err.message);
  }
}

async function rerunForYou() {
  const ids = state.mode && state.mode.ids;
  if (!ids || !ids.length) {
    loadForYou();
    return;
  }
  clearResults();
  setStatus("Updating your feed...");
  try {
    const params = { ...recommendParams(), ids };
    // Keep the feed's list exclusion even when no exclude box is ticked,
    // matching what /foryou applies when it samples.
    const anilistName = $("alUser").value.trim();
    const malName = $("malUser").value.trim();
    if (anilistName) params.anilist_user = anilistName;
    else if (malName) params.mal_user = malName;
    const data = await api("/recommend", params);
    state.mode = { type: "foryou", ids };
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
    const resp = await authFetch(`/mal/${encodeURIComponent(username)}/refresh`, { method: "POST" });
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
  // Print media has no dub, so the toggle is hidden rather than ignored.
  $("dubbedRow").style.display = state.medium === "anime" ? "" : "none";
  if (state.medium === "anime") {
    loadDubCoverage();
    loadDubLanguages();
  }
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
  hideTagSuggestions();
  renderTagChips();
  rerunActive();
}

async function loadTagVocabulary() {
  try {
    const data = await api("/tags");
    state.tagVocabulary = data.tags || [];
  } catch {
    // Autocomplete is a convenience; typing exact tag names still works.
  }
}

// Custom suggestion dropdown for the tag input. A native datalist hides its
// suggestions in the keyboard strip on Android and is easy to never notice;
// this one is visible, filtered as you type, and keyboard-navigable.
const MAX_TAG_SUGGESTIONS = 12;

function tagMatches(query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const vocab = state.tagVocabulary || [];
  const starts = [];
  const contains = [];
  for (const name of vocab) {
    const lower = name.toLowerCase();
    if (lower.startsWith(q)) starts.push(name);
    else if (lower.includes(q)) contains.push(name);
    if (starts.length >= MAX_TAG_SUGGESTIONS) break;
  }
  return [...starts, ...contains].slice(0, MAX_TAG_SUGGESTIONS);
}

function hideTagSuggestions() {
  const box = $("tagSuggest");
  if (box) {
    box.hidden = true;
    box.innerHTML = "";
  }
  state.tagSuggestIndex = -1;
}

function renderTagSuggestions() {
  const box = $("tagSuggest");
  if (!box) return;
  const matches = tagMatches($("tagInput").value);
  if (!matches.length) {
    hideTagSuggestions();
    return;
  }
  box.innerHTML = "";
  matches.forEach((name, i) => {
    const item = document.createElement("div");
    item.textContent = name;
    if (i === state.tagSuggestIndex) item.classList.add("active");
    // mousedown fires before the input's blur, so the click always lands.
    item.addEventListener("mousedown", (e) => {
      e.preventDefault();
      $("tagInput").value = name;
      hideTagSuggestions();
      $("tagInput").focus();
    });
    box.appendChild(item);
  });
  box.hidden = false;
}

function tagInputKeydown(e) {
  const box = $("tagSuggest");
  const open = box && !box.hidden;
  const count = open ? box.children.length : 0;
  if (e.key === "ArrowDown" && count) {
    e.preventDefault();
    state.tagSuggestIndex = (state.tagSuggestIndex + 1) % count;
    renderTagSuggestions();
  } else if (e.key === "ArrowUp" && count) {
    e.preventDefault();
    state.tagSuggestIndex = (state.tagSuggestIndex - 1 + count) % count;
    renderTagSuggestions();
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (open && state.tagSuggestIndex >= 0) {
      $("tagInput").value = box.children[state.tagSuggestIndex].textContent;
      hideTagSuggestions();
    } else if (open && count === 1) {
      $("tagInput").value = box.children[0].textContent;
      hideTagSuggestions();
    } else {
      // No selection: Enter means "require this tag".
      hideTagSuggestions();
      addTagFilter("inc");
    }
  } else if (e.key === "Escape") {
    hideTagSuggestions();
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
    const resp = await authFetch(`/anilist/${encodeURIComponent(username)}/refresh`, {
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
  else if (state.mode.type === "foryou") rerunForYou();
}

// Per-medium deep links, matching Anibrain's structure: /anime, /manga,
// /light-novel, /one-shot all serve the SPA; the path picks the tab.
const PATH_TO_MEDIUM = {
  "/anime": "anime",
  "/manga": "manga",
  "/light-novel": "light_novel",
  "/one-shot": "one_shot",
};
const MEDIUM_TO_PATH = Object.fromEntries(
  Object.entries(PATH_TO_MEDIUM).map(([path, medium]) => [medium, path])
);

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
      // Keep the URL shareable: the medium lives in the path, hash cleared.
      history.replaceState(null, "", MEDIUM_TO_PATH[button.dataset.medium] || "/");
      runSearch($("search").value);
    });
  }

  on("search", "input", debounce((e) => runSearch(e.target.value), 300));

  // The button is the only way to apply the filter panel to the catalog;
  // typing stays a plain title lookup so picking a seed is not slowed down
  // by a dozen filter clauses on every keystroke.
  on("browseBtn", "click", () => runSearch($("search").value, true));
  on("describeBtn", "click", () => runDescribe($("describe").value));
  on("describe", "keydown", (e) => {
    // Enter only: a description is a sentence, and re-ranking the whole
    // catalog on every keystroke would be one ANN scan per character.
    if (e.key === "Enter") runDescribe($("describe").value);
  });
  // Enter in the box means "I have finished typing", which is the natural
  // moment to honour the filters too.
  on("search", "keydown", (e) => {
    if (e.key === "Enter") runSearch($("search").value, true);
  });

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

  for (const id of ["adult", "dubLang", "crossMedia", "excludeFranchise", "yearMin", "yearMax",
                    "minScore", "country", "status", "format", "maxPop", "maxLen", "minLen",
                    "malExclude", "alExclude", "kitsuExclude", "ytExclude", "keepPlanned"]) {
    on(id, "change", rerunActive);
  }

  on("ytRefresh", "click", refreshYamtrack);
  on("kitsuRefresh", "click", refreshKitsu);
  on("acctSave", "click", saveAccountSettings);

  on("tagReq", "click", () => addTagFilter("inc"));
  on("tagExc", "click", () => addTagFilter("exc"));
  on("tagInput", "input", () => {
    state.tagSuggestIndex = -1;
    renderTagSuggestions();
  });
  on("tagInput", "keydown", tagInputKeydown);
  on("tagInput", "blur", hideTagSuggestions);
  on("alRefresh", "click", refreshAnilist);

  $("titleLang").value = state.titleLang;
  on("titleLang", "change", () => {
    state.titleLang = $("titleLang").value;
    localStorage.setItem("mb_title_lang", state.titleLang);
    if (state.mode) rerunActive();
    else if ($("search").value.trim()) runSearch($("search").value);
  });

  on("forYou", "click", loadForYou);
  // Sampling-mode change needs a fresh sample; re-ranking pinned seeds
  // would not reflect it.
  on("useRatings", "change", () => {
    if (state.mode && state.mode.type === "foryou") loadForYou();
  });
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
{
  const pathMedium = PATH_TO_MEDIUM[location.pathname];
  if (pathMedium) setMedium(pathMedium);
}
loadTagVocabulary();
loadAccountSettings();
applyHash();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
