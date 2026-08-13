"use strict";

const state = {
  medium: "anime",
  seed: null,
};

const FORMATS = {
  anime: ["TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC"],
  manga: ["MANGA"],
  light_novel: ["NOVEL"],
  one_shot: ["ONE_SHOT"],
};

const $ = (id) => document.getElementById(id);

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
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, value);
    }
  }
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${resp.status})`);
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
  };
  if ($("malExclude").checked && $("malUser").value.trim()) {
    params.mal_user = $("malUser").value.trim();
  }
  return params;
}

function weightParams() {
  return {
    w_semantic: $("wSemantic").value / 100,
    w_tags: $("wTags").value / 100,
    w_genres: $("wGenres").value / 100,
  };
}

function setStatus(text) {
  $("status").textContent = text || "";
}

function mediaTitle(media) {
  return media.title || media.title_english || media.title_native || `#${media.id}`;
}

function mediaMeta(media) {
  const parts = [];
  if (media.start_year) parts.push(media.start_year);
  if (media.format) parts.push(media.format.replaceAll("_", " "));
  if (media.medium && media.medium !== state.medium) parts.push(media.medium.replaceAll("_", " "));
  return parts.join(" / ");
}

function card(media, badgeText, onClick) {
  const el = document.createElement("div");
  el.className = "card";
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
  el.appendChild(info);
  el.addEventListener("click", onClick);
  return el;
}

function clearResults() {
  $("seed").innerHTML = "";
  $("related").innerHTML = "";
  $("results").innerHTML = "";
  setStatus("");
}

async function runSearch(query) {
  state.seed = null;
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

function renderSeed(seed) {
  const wrap = $("seed");
  wrap.innerHTML = "";
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
  const desc = document.createElement("p");
  desc.textContent = seed.description || "";
  body.appendChild(h3);
  body.appendChild(meta);
  body.appendChild(desc);
  el.appendChild(img);
  el.appendChild(body);
  wrap.appendChild(el);
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

async function loadRecommendations(mediaId) {
  state.seed = mediaId;
  clearResults();
  setStatus("Finding similar titles...");
  try {
    const data = await api(`/recommend/${mediaId}`, {
      ...weightParams(),
      ...filterParams(),
      cross_media: $("crossMedia").checked ? "true" : "",
      limit: 60,
    });
    renderSeed(data.seed);
    renderRelated(data.related);
    const grid = $("results");
    for (const item of data.results) {
      grid.appendChild(
        card(item.media, `${item.similarity}%`, () => loadRecommendations(item.media.id))
      );
    }
    setStatus(data.results.length ? "" : "No recommendations pass the active filters.");
    window.scrollTo({ top: 0, behavior: "smooth" });
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

function bindEvents() {
  for (const button of document.querySelectorAll("#tabs button")) {
    button.addEventListener("click", () => {
      document.querySelector("#tabs button.active").classList.remove("active");
      button.classList.add("active");
      state.medium = button.dataset.medium;
      populateFormats();
      state.seed = null;
      runSearch($("search").value);
    });
  }

  $("search").addEventListener("input", debounce((e) => runSearch(e.target.value), 300));

  for (const [slider, label] of [
    ["wSemantic", "wSemanticVal"],
    ["wTags", "wTagsVal"],
    ["wGenres", "wGenresVal"],
  ]) {
    $(slider).addEventListener("input", () => {
      $(label).textContent = $(slider).value;
    });
    $(slider).addEventListener("change", () => {
      if (state.seed) loadRecommendations(state.seed);
    });
  }

  for (const id of ["adult", "crossMedia", "yearMin", "yearMax", "minScore",
                    "country", "status", "format", "malExclude"]) {
    $(id).addEventListener("change", () => {
      if (state.seed) loadRecommendations(state.seed);
    });
  }

  $("surprise").addEventListener("click", surprise);
  $("malRefresh").addEventListener("click", refreshMal);
}

populateFormats();
bindEvents();
