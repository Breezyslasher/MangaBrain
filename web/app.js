"use strict";

const state = {
  medium: "anime",
  // The tabs to show, from /settings. null until it answers: every tab.
  enabledMedia: null,
  mode: null, // {type: "single", id} | {type: "mix"}
  mix: [], // [{id, title}]
  pending: [], // rendered lazily via Show more
  shown: 0,
  genreState: {}, // name -> "inc" | "exc"
  tagState: {}, // name -> "inc" | "exc"
  tagVocabulary: [], // this medium's tags, for the suggestion dropdown
  genres: null, // this medium's genres, from /genres; null until loaded
  facets: null, // its countries, formats and statuses, from /facets
  tagSuggestIndex: -1, // keyboard-highlighted suggestion
  titleLang: localStorage.getItem("mb_title_lang") || "english",
  shownSeedIds: "", // seeds currently on screen, so a re-rank does not scroll
};

const PAGE_SIZE = 60;
const MAX_MIX = 5;

// Controls a medium has no data behind. Hiding them is not cosmetic: a
// value left in a box still went into the query after switching tabs, and
// three of these exclude every book at the SQL level rather than being
// ignored, because a NULL country never equals 'JP'. So one table drives
// both the hiding and the query, and the Books tab cannot ask a question
// books have no answer to.
//
// Score is here for a different reason from the rest. A book can have a
// NULL score and still be a good book, so the API now lets unscored titles
// pass the floor - but that makes the control a no-op on this tab rather
// than a filter, and a no-op slider is worse than an absent one.
const UNSUPPORTED = {
  book: ["minScore", "country", "status", "maxPop", "minLen", "maxLen"],
  // Films (TMDB) and TV (TVmaze): their popularity, vote and rating
  // fields are never stored (nothing may rank or filter by fame), so there
  // is no score or popularity behind either control; length has no
  // column for them.
  movie: ["minScore", "maxPop", "minLen", "maxLen"],
  tv: ["minScore", "maxPop", "minLen", "maxLen"],
};

function supports(id) {
  return !(UNSUPPORTED[state.medium] || []).includes(id);
}

function controlValue(id) {
  const el = $(id);
  if (!el || !supports(id)) return "";
  return el.value;
}

// The format, country and status dropdowns are filled from /facets with
// what each medium's titles actually carry. They used to be AniList's lists
// typed in here, so the Films tab offered Japan, Korea, China and Taiwan
// and nothing else. Only the words shown for each value live here.
const STATUS_ORDER = ["FINISHED", "RELEASING", "NOT_YET_RELEASED", "HIATUS", "CANCELLED"];
const PRINT_STATUS = {
  FINISHED: "Finished",
  RELEASING: "Publishing",
  NOT_YET_RELEASED: "Not yet published",
  HIATUS: "On hiatus",
  CANCELLED: "Cancelled",
};
const STATUS_LABELS = {
  anime: {
    FINISHED: "Finished",
    RELEASING: "Airing",
    NOT_YET_RELEASED: "Not yet aired",
    HIATUS: "On hiatus",
    CANCELLED: "Cancelled",
  },
  manga: PRINT_STATUS,
  light_novel: PRINT_STATUS,
  one_shot: PRINT_STATUS,
  book: PRINT_STATUS,
  movie: {
    FINISHED: "Released",
    RELEASING: "Releasing",
    NOT_YET_RELEASED: "Upcoming",
    HIATUS: "On hold",
    CANCELLED: "Cancelled",
  },
  // TVmaze's "To Be Determined" is stored as HIATUS.
  tv: {
    FINISHED: "Ended",
    RELEASING: "Airing",
    NOT_YET_RELEASED: "In development",
    HIATUS: "To be determined",
    CANCELLED: "Cancelled",
  },
};

function statusLabel(status) {
  const labels = STATUS_LABELS[state.medium] || STATUS_LABELS.anime;
  return labels[status] || status.replaceAll("_", " ").toLowerCase();
}

let regionNames = null;
try {
  regionNames = new Intl.DisplayNames(["en"], { type: "region" });
} catch {
  // An old browser shows the codes instead.
}

function countryName(code) {
  try {
    return (regionNames && regionNames.of(code)) || code;
  } catch {
    return code;
  }
}

// AniList's genres: what the anime and manga tabs show until /genres
// answers for the medium, or if it cannot be reached. Other media name
// their genres differently (TVmaze "Legal", books "Biography") and show
// only the server's list.
const GENRES = [
  "Action",
  "Adventure",
  "Comedy",
  "Drama",
  "Ecchi",
  "Fantasy",
  "Horror",
  "Mahou Shoujo",
  "Mecha",
  "Music",
  "Mystery",
  "Psychological",
  "Romance",
  "Sci-Fi",
  "Slice of Life",
  "Sports",
  "Supernatural",
  "Thriller",
];

const $ = (id) => document.getElementById(id);

// A browser can hold a cached index.html from one deploy alongside a fresh
// app.js from the next; a missing element must degrade to a warning, never
// an exception that kills the rest of startup and leaves buttons dead.
function on(id, event, fn) {
  const node = $(id);
  if (node) node.addEventListener(event, fn);
  else
    console.warn(
      `missing #${id} - stale cached index.html? Hard-refresh the page.`,
    );
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
  const withAuth = () =>
    fetch(url, {
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
      const token = window.prompt(
        "This MangaBrain server requires an access token:",
      );
      if (token && token.trim()) localStorage.setItem("mb_token", token.trim());
    }
    if ((localStorage.getItem("mb_token") || "") !== used)
      resp = await withAuth();
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
    throw new Error(
      body.detail
        ? JSON.stringify(body.detail)
        : `Request failed (${resp.status})`,
    );
  }
  return resp.json();
}

function filterParams() {
  const params = {
    adult: $("adult").checked ? "true" : "",
    year_min: $("yearMin").value,
    year_max: $("yearMax").value,
    min_score: controlValue("minScore"),
    country: controlValue("country"),
    status: controlValue("status"),
    format: $("format").value,
    max_popularity: controlValue("maxPop"),
    genre_in: shownGenres().filter((g) => state.genreState[g] === "inc"),
    genre_ex: shownGenres().filter((g) => state.genreState[g] === "exc"),
    tag_in: Object.keys(state.tagState).filter(
      (t) => state.tagState[t] === "inc",
    ),
    tag_ex: Object.keys(state.tagState).filter(
      (t) => state.tagState[t] === "exc",
    ),
  };
  // One control per bound, mapped to the field the medium uses. A floor was
  // missing until now, so "a long series to sink into" could not be asked
  // for at all. Unknown lengths pass both bounds: an absent episode count is
  // not evidence a title is short, nor that it is long.
  const maxLen = controlValue("maxLen");
  if (maxLen) {
    if (state.medium === "anime") params.max_episodes = maxLen;
    else params.max_chapters = maxLen;
  }
  const minLen = controlValue("minLen");
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
  // Only the mobile layout offers Hardcover; the box is absent on desktop.
  if (isChecked("hcExclude")) excludeLists.push("hardcover");
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
    return (
      media.title_english || media.title || media.title_native || `#${media.id}`
    );
  }
  return (
    media.title || media.title_english || media.title_native || `#${media.id}`
  );
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
  if (media.medium && media.medium !== state.medium)
    parts.push(media.medium.replaceAll("_", " "));
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
      err,
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

// A page about the title on the site its data came from: AniList and
// MyAnimeList for anime and manga, TMDB, TVmaze or Open Library for the
// rest. The server builds them from the row's source and that source's id.
function externalLink(link) {
  const a = document.createElement("a");
  a.href = link.url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = link.label;
  return a;
}

function card(media, badgeText, onClick, components) {
  const el = document.createElement("div");
  el.className = "card";
  if (components) {
    // genres is null when neither side had any to compare - books from
    // Open Library often have none. Math.round(null * 100) is 0, which
    // would read as "nothing in common" rather than "not compared".
    const genrePart =
      components.genres === null || components.genres === undefined
        ? " genres: not compared"
        : ` genres: ${Math.round(components.genres * 100)}`;
    el.title =
      `Match components - synopsis: ${Math.round(components.semantic * 100)},` +
      ` tags: ${Math.round(components.tags * 100)},` +
      genrePart;
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
  info.appendChild(title);
  info.appendChild(meta);
  const first = (media.links || [])[0];
  if (first) {
    const link = externalLink(first);
    link.className = "ext";
    link.addEventListener("click", (e) => e.stopPropagation());
    info.appendChild(link);
  }
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
      card(
        item.media,
        badge,
        () => loadRecommendations(item.media.id),
        item.components,
      ),
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
    const params = {
      ...filterParams(),
      medium: state.medium,
      q: term,
      limit: "60",
    };
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
      : "Nothing matches that description. Try fewer words, or loosen the filters.",
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
      setStatus(withFilters ? "No titles match those filters." : "No matches.");
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
  for (const link of seed.links || []) links.appendChild(externalLink(link));
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
  setStatus(
    data.results.length ? "" : "No recommendations pass the active filters.",
  );

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
  state.mix.push({
    id: media.id,
    title: mediaTitle(media),
    cover: media.cover_image_large || media.cover_image || "",
    meta: mediaMeta(media),
  });
  renderMix();
}

async function loadAccountSettings() {
  try {
    const data = await api("/settings");
    if (data.anilist_username) $("alUser").value = data.anilist_username;
    if (data.mal_username) $("malUser").value = data.mal_username;
    if (data.kitsu_username && $("kitsuUser"))
      $("kitsuUser").value = data.kitsu_username;
    if (data.yamtrack_url) $("ytUrl").value = data.yamtrack_url;
    if (data.yamtrack_token_set)
      $("ytToken").placeholder = "saved (enter to replace)";
    applyEnabledMedia(data.enabled_media);
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
  if ($("ytToken").value.trim())
    body.yamtrack_token = $("ytToken").value.trim();
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
    if (data.yamtrack_token_set)
      $("ytToken").placeholder = "saved (enter to replace)";
    $("acctStatus").textContent = "Saved.";
  } catch (err) {
    $("acctStatus").textContent = err.message;
  }
}

// ---------------------------------------------------------------------
// Settings: which media this install holds.
//
// Nothing here names a medium. The datasets, the media each one fills and
// what it costs all come from GET /datasets, and the tab labels from the
// tab buttons, so a medium added on the server shows up here as it is.

const INSTALL_ACTIVE = ["queued", "running", "waiting"];
let settingsPoll = null;

function tabLabel(group) {
  const option = document.querySelector(`#tabs option[data-medium="${group}"]`);
  return option ? option.textContent : group;
}

// Tabs follow the selection. A deep link into a hidden medium is honoured
// while it is on screen; only an idle page moves to a visible tab.
function applyEnabledMedia(media) {
  if (!Array.isArray(media) || !media.length) return;
  state.enabledMedia = media;
  for (const option of document.querySelectorAll("#tabs option")) {
    // Disabled as well as hidden: Safari ignores hidden on an option.
    const off = !media.includes(option.dataset.medium);
    option.hidden = off;
    option.disabled = off;
  }
  if (!media.includes(state.medium) && !state.mode) {
    setMedium(media[0]);
    history.replaceState(null, "", MEDIUM_TO_PATH[media[0]] || "/");
  }
}

function formatGB(bytes) {
  return `${((bytes || 0) / 1e9).toFixed(2)} GB`;
}

function installText(dataset) {
  const install = dataset.install;
  if (install && install.state === "running") {
    if (install.phase === "downloading" && install.total)
      return `Downloading ${formatGB(install.bytes)} of ${formatGB(install.total)}`;
    // TV and films: titles read so far (as "bytes"), against "total" when
    // the source knows it - TMDB does, TVmaze's index does not.
    if (install.phase === "fetching")
      return install.total
        ? `Fetching ${(install.bytes || 0).toLocaleString()} of ${install.total.toLocaleString()} titles`
        : `Fetching: ${(install.bytes || 0).toLocaleString()} titles read`;
    if (install.phase === "merging") return "Merging into the catalogue";
    if (install.phase === "indexing") return "Building the search index";
    return "Starting";
  }
  if (install && install.state === "queued")
    return "Queued. The server starts it within a minute.";
  if (install && (install.state === "waiting" || install.state === "failed"))
    return install.message;
  if (dataset.installed) {
    const items = dataset.items.toLocaleString();
    if (!dataset.installable) return `${items} titles`;
    return `${items} installed, ${dataset.embedded.toLocaleString()} with embeddings`;
  }
  if (dataset.release_available === false)
    return "Not published yet. It downloads as soon as it is.";
  return `Not downloaded. ${dataset.download_hint}`;
}

function mediaStatus(text) {
  $("mediaStatus").textContent = text || "";
}

async function renderMediaSettings() {
  const wrap = $("mediaSettings");
  let data;
  try {
    data = await api("/datasets");
  } catch (err) {
    wrap.textContent = err.message;
    return;
  }
  const enabled = state.enabledMedia || [];
  let active = false;
  wrap.replaceChildren();
  for (const dataset of data.datasets) {
    const install = dataset.install;
    if (install && INSTALL_ACTIVE.includes(install.state)) active = true;

    const card = document.createElement("div");
    card.className = "dataset";
    const title = document.createElement("div");
    title.className = "dataset-title";
    title.textContent = dataset.label;
    const desc = document.createElement("div");
    desc.className = "dataset-desc";
    desc.textContent = dataset.description;
    const status = document.createElement("div");
    status.className = "dataset-status";
    if (install && install.state === "failed") status.classList.add("failed");
    status.textContent = installText(dataset);
    card.append(title, desc, status);

    if (
      install &&
      install.state === "running" &&
      (install.phase === "downloading" || install.phase === "fetching") &&
      install.total
    ) {
      const bar = document.createElement("progress");
      bar.max = install.total;
      bar.value = install.bytes || 0;
      card.append(bar);
    }

    const media = document.createElement("div");
    media.className = "dataset-media";
    for (const group of dataset.media) {
      const label = document.createElement("label");
      label.className = "check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = enabled.includes(group);
      box.addEventListener("change", () => toggleMedium(group, box, dataset));
      label.append(box, ` ${tabLabel(group)}`);
      media.append(label);
    }
    card.append(media);

    // Retry after a failure; Update to merge in a newer release. Neither
    // while one is already under way.
    const busy = install && INSTALL_ACTIVE.includes(install.state);
    if (
      dataset.installable &&
      !busy &&
      (install?.state === "failed" || dataset.installed)
    ) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent =
        install?.state === "failed"
          ? "Retry"
          : dataset.update_label || "Update from the newest release";
      button.addEventListener("click", () => installDataset(dataset));
      card.append(button);
    }
    wrap.append(card);
  }
  clearTimeout(settingsPoll);
  settingsPoll = null;
  // The mobile layout shows the same list on its Media page, not the dialog.
  if (active && ($("settingsDialog").open || window.mbMediaVisible))
    settingsPoll = setTimeout(renderMediaSettings, 4000);
}

async function toggleMedium(group, box, dataset) {
  const current = state.enabledMedia || [];
  const next = box.checked
    ? [...current, group]
    : current.filter((g) => g !== group);
  if (!next.length) {
    box.checked = true;
    mediaStatus("At least one medium has to stay on.");
    return;
  }
  const install = dataset.install;
  const willDownload =
    box.checked &&
    dataset.installable &&
    !dataset.installed &&
    !(install && INSTALL_ACTIVE.includes(install.state));
  // Asked before, never after: switching this on is what starts it.
  if (
    willDownload &&
    !window.confirm(`Download ${dataset.label}? ${dataset.download_hint}`)
  ) {
    box.checked = false;
    return;
  }
  mediaStatus(willDownload ? "Starting the download..." : "Saving...");
  try {
    const resp = await authFetch("/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled_media: next }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(
        body.detail ? String(body.detail) : `Save failed (${resp.status})`,
      );
    }
    applyEnabledMedia((await resp.json()).enabled_media);
    mediaStatus("");
  } catch (err) {
    box.checked = !box.checked;
    mediaStatus(err.message);
  }
  await renderMediaSettings();
}

async function installDataset(dataset) {
  if (
    !dataset.installed &&
    !window.confirm(`Download ${dataset.label}? ${dataset.download_hint}`)
  )
    return;
  mediaStatus("");
  try {
    const resp = await authFetch(
      `/datasets/${encodeURIComponent(dataset.key)}/install`,
      {
        method: "POST",
      },
    );
    if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
  } catch (err) {
    mediaStatus(err.message);
  }
  await renderMediaSettings();
}

function openSettings(event) {
  if (event) event.preventDefault();
  const dialog = $("settingsDialog");
  if (!dialog.open) dialog.showModal();
  renderMediaSettings();
}

function closeSettings() {
  $("settingsDialog").close();
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
    setStatus(
      "Enter an AniList or MAL username, or sync Kitsu/Yamtrack and" +
        " tick its exclude box, then try again.",
    );
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
    const media = await api("/random", {
      medium: state.medium,
      ...filterParams(),
    });
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
    const resp = await authFetch(
      `/mal/${encodeURIComponent(username)}/refresh`,
      { method: "POST" },
    );
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

// Refills one dropdown, keeping the choice when the new list still has it.
function fillSelect(id, values, label) {
  const select = $(id);
  if (!select) return;
  // While the lists load, the choice is remembered rather than dropped, so
  // flipping the adult toggle does not clear a chosen country.
  const previous = select.value || select.dataset.want || "";
  if (state.facets) delete select.dataset.want;
  else select.dataset.want = previous;
  select.innerHTML = '<option value="">Any</option>';
  for (const value of values) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label(value);
    select.appendChild(opt);
  }
  select.value = values.includes(previous) ? previous : "";
}

function populateFormats() {
  const facets = state.facets || { countries: [], formats: [], statuses: [] };
  fillSelect("format", facets.formats, (f) => f.replaceAll("_", " "));
  const countries = [...facets.countries].sort((a, b) =>
    countryName(a).localeCompare(countryName(b)),
  );
  fillSelect("country", countries, countryName);
  const statuses = [...facets.statuses].sort(
    (a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b),
  );
  fillSelect("status", statuses, statusLabel);
  // One format to choose from (every film is a film) is no choice.
  $("formatWrap").style.display = facets.formats.length > 1 ? "" : "none";
  // Each of these sits in its own <label>, which is the row to hide.
  for (const id of [
    "minScore",
    "country",
    "status",
    "maxPop",
    "minLen",
    "maxLen",
  ]) {
    const row = $(id) && $(id).closest("label");
    const empty =
      (id === "country" || id === "status") && (!state.facets || $(id).options.length < 3);
    if (row) row.style.display = supports(id) && !empty ? "" : "none";
  }
  // Print media has no dub, so the toggle is hidden rather than ignored.
  $("dubbedRow").style.display = state.medium === "anime" ? "" : "none";
  if (state.medium === "anime") {
    loadDubCoverage();
    loadDubLanguages();
  }
}

function shownGenres() {
  if (state.genres) return state.genres;
  return state.medium === "anime" || state.medium === "manga" ? GENRES : [];
}

function renderGenreChips() {
  const wrap = $("genreChips");
  wrap.innerHTML = "";
  for (const genre of shownGenres()) {
    const chip = document.createElement("span");
    chip.className = "chip";
    const mode = state.genreState[genre];
    if (mode === "inc") chip.classList.add("inc");
    if (mode === "exc") chip.classList.add("exc");
    chip.textContent = genre;
    chip.addEventListener("click", () => {
      if (state.genreState[genre] === "inc") state.genreState[genre] = "exc";
      else if (state.genreState[genre] === "exc")
        delete state.genreState[genre];
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

// Genres and tags belong to a medium, so both are fetched for the one in
// view and again whenever it changes. A reply for a medium the user has
// since left is dropped.
async function loadVocabularies() {
  const medium = state.medium;
  // Adult titles and AniList's adult tags are listed only with the toggle on.
  const adult = $("adult").checked ? "true" : "";
  const current = () => state.medium === medium && ($("adult").checked ? "true" : "") === adult;
  state.genres = null;
  state.tagVocabulary = [];
  state.facets = null;
  renderGenreChips();
  populateFormats();
  try {
    const data = await api("/facets", { medium, adult });
    if (!current()) return;
    state.facets = data;
    populateFormats();
  } catch {
    // The dropdowns stay at "Any"; the next medium change retries.
  }
  try {
    const data = await api("/genres", { medium, adult });
    if (!current()) return;
    state.genres = data.genres || [];
    // A genre this medium does not have would filter every result out.
    for (const name of Object.keys(state.genreState)) {
      if (!state.genres.includes(name)) delete state.genreState[name];
    }
    renderGenreChips();
  } catch {
    // The fallback list stays; the next medium change retries.
  }
  try {
    const data = await api("/tags", { medium, adult });
    if (!current()) return;
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
    const resp = await authFetch(
      `/anilist/${encodeURIComponent(username)}/refresh`,
      {
        method: "POST",
      },
    );
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
  if (state.mode.type === "single")
    loadRecommendations(state.mode.id, { updateHash: false });
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
  "/books": "book",
  "/movies": "movie",
  "/tv": "tv",
};
const MEDIUM_TO_PATH = Object.fromEntries(
  Object.entries(PATH_TO_MEDIUM).map(([path, medium]) => [medium, path]),
);

function setMedium(medium) {
  if (state.medium === medium) return;
  state.medium = medium;
  $("tabs").value = medium;
  populateFormats();
  loadVocabularies();
}

function applyHash() {
  const match = location.hash.match(
    /^#\/rec\/(anime|manga|light_novel|one_shot|book|movie|tv)\/(\d+)$/,
  );
  if (!match) return false;
  setMedium(match[1]);
  loadRecommendations(Number(match[2]), { updateHash: false });
  return true;
}

function bindEvents() {
  $("tabs").addEventListener("change", (event) => {
    const medium = event.target.value;
    setMedium(medium);
    state.mode = null;
    // Keep the URL shareable: the medium lives in the path, hash cleared.
    history.replaceState(null, "", MEDIUM_TO_PATH[medium] || "/");
    runSearch($("search").value);
  });

  on(
    "search",
    "input",
    debounce((e) => runSearch(e.target.value), 300),
  );

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

  for (const id of [
    "adult",
    "dubLang",
    "crossMedia",
    "excludeFranchise",
    "yearMin",
    "yearMax",
    "minScore",
    "country",
    "status",
    "format",
    "maxPop",
    "maxLen",
    "minLen",
    "malExclude",
    "alExclude",
    "kitsuExclude",
    "ytExclude",
    "keepPlanned",
  ]) {
    on(id, "change", rerunActive);
  }

  on("ytRefresh", "click", refreshYamtrack);
  on("kitsuRefresh", "click", refreshKitsu);
  on("acctSave", "click", saveAccountSettings);
  on("settingsBtn", "click", openSettings);
  on("openSettingsLink", "click", openSettings);
  on("settingsClose", "click", closeSettings);
  // Esc closes a modal dialog too; either way, stop polling.
  on("settingsDialog", "close", () => {
    clearTimeout(settingsPoll);
    settingsPoll = null;
  });

  on("tagReq", "click", () => addTagFilter("inc"));
  on("tagExc", "click", () => addTagFilter("exc"));
  on("tagInput", "input", () => {
    state.tagSuggestIndex = -1;
    renderTagSuggestions();
  });
  on("tagInput", "keydown", tagInputKeydown);
  // The genre and tag lists include adult ones only with the toggle on.
  on("adult", "change", loadVocabularies);
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
  // setMedium loads the medium's vocabularies when it changes the medium;
  // otherwise the default medium's are loaded here.
  if (pathMedium && pathMedium !== state.medium) setMedium(pathMedium);
  else loadVocabularies();
}
loadAccountSettings();
applyHash();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
