# MangaBrain

A self-hosted, content-based recommendation engine for anime, manga, manhwa,
manhua, light novels, and one-shots, rebuilding the full scope of the defunct
anibrain.ai.

The defining principle: **similarity is computed from content** (synopsis
embeddings, tags, genres) **and never from popularity**. Obscure titles with
high content similarity rank above popular titles with lower similarity, and a
test suite asserts that no audience-size signal can enter the ranking
function.

## How it works

- The catalog (both AniList media types, ~20k anime + ~150k manga-family
  entries) is synced from the AniList GraphQL API into one `media` table with
  a `medium` discriminator (`anime | manga | manhwa | manhua | light_novel |
  one_shot`) derived from AniList format + country of origin.
- Each title is embedded with `avsolatorio/GIST-Embedding-v0`
  (768-dim, chosen by measured recall on the AniList recommendation-pair
  benchmark, `pipeline.benchmark`) over its genres, top ranked tags, and synopsis
  (title strings are embedded only for rows without a synopsis, to avoid
  subword title leakage), into a single vector space shared across all
  mediums, which makes cross-media similarity free. The text recipe is versioned alongside
  the model name (`EMBED_TEXT_VERSION`); changing either makes
  `pipeline.embed` re-embed the catalog as a migration, and recommendations
  stay consistent throughout by ranking each seed against candidates from
  the seed's own embedding version.
- A recommendation request retrieves the top candidates by semantic ANN
  (pgvector HNSW index) within the seed's medium group, then re-ranks with
  the full weighted score:
  `w1 * semantic_cos + w2 * tag_sim + w3 * genre_sim`
  (defaults 0.5 / 0.3 / 0.2, adjustable per request via query params, no
  re-embedding needed).
- Direct adaptation/source relations of the seed are excluded from results
  and returned separately as "related".

## Run from the prebuilt image (no clone needed)

Every merge to main publishes `ghcr.io/breezyslasher/mangabrain:latest`
(linux/amd64) via GitHub Actions, and a weekly workflow publishes a catalog
snapshot (media, relations, embeddings, sync checkpoints) as a dated
`dataset-*` release, always downloadable through the stable
`releases/latest/download/mangabrain-dataset.dump` URL (GitHub releases on
this repo are immutable, so snapshots are never replaced in place). To run
without building or syncing anything, copy
the single file [`docker-compose.prebuilt.yml`](docker-compose.prebuilt.yml)
anywhere and:

```
docker compose -f docker-compose.prebuilt.yml up -d
```

The services apply the database schema themselves, and on first start the
worker downloads the snapshot and restores it in minutes; the nightly
incremental sync then continues from the snapshot's checkpoint. If the
snapshot is unavailable, the worker falls back to a full AniList sync and
embedding pass on its own. While the first-boot restore is running, API
queries can return transient errors for a few minutes (the restore drops
and recreates the catalog tables); this resolves itself when the restore
completes. Manual snapshot commands:

```
python -m pipeline.restore --url <snapshot url>    # seed or (with --force) replace the catalog
python -m pipeline.snapshot --out my-snapshot.dump # dump your own catalog
```

## Quick start (development)

Requires Python 3.12 and Docker.

```
docker compose up -d db                          # Postgres 16 + pgvector, schema auto-applied
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python -m pipeline.sync_anilist                  # full catalog, both types in one resumable id scan
python -m pipeline.embed                         # generate/update embeddings
python -m pipeline.backfill_jikan                # optional: fill missing synopses from MAL
python -m pipeline.backfill_dub                  # optional: English dub flag from AniList cast data
python -m pipeline.backfill_dub_languages        # all dub languages from AniList, one request per 50 titles (~1 h)
python -m pipeline.propagate_dub                 # infer dubs across franchises from a measured sibling
python -m pipeline.backfill_dub_ann              # dub languages from ANN (all languages, ~45 min)

# quality benchmark: AniList's human-voted recommendation pairs as ground
# truth (evaluation only - the pairs never enter scoring). fetch once, then
# compare embedding models by recall@10/@50 and MRR before committing to a
# full re-embed:
python -m pipeline.benchmark fetch --out benchmark_pairs.json
python -m pipeline.benchmark run --pairs-file benchmark_pairs.json \
    --model BAAI/bge-small-en-v1.5 --model avsolatorio/GIST-small-Embedding-v0
# or run it on CI hardware instead: Actions > model-bench > Run workflow
# (restores the published snapshot, fetches pairs, prints the table in the log)

uvicorn api.main:app --reload                    # dev server + SPA at http://localhost:8000
```

## Full stack (OpenMediaVault / any Docker host)

```
docker compose up --build -d
```

Services:

| service | role |
| ------- | ---- |
| `db`     | PostgreSQL 16 + pgvector; schema applied on first init |
| `api`    | FastAPI: search, recommend, random, MAL sync; serves the SPA on port 8000 |
| `worker` | Same image; nightly incremental AniList sync + embedding pass |

One-off jobs run through the worker image:

```
docker compose run --rm worker python -m pipeline.sync_anilist
docker compose run --rm worker python -m pipeline.embed
```

The full sync scans the shared AniList id space in `id_in` batches (AniList
caps pagination offsets at 5000 rows, so page-number pagination cannot cover
the catalog). It checkpoints after every batch and is safe to interrupt and
rerun; `--type anime` or `--type manga` restricts what gets stored.

## API

| endpoint | description |
| -------- | ----------- |
| `GET /search?q=...&medium=anime` | Trigram-ranked title search within a medium group |
| `POST /search/resolve` | Batch exact title-to-id lookup for a library client: body `{"items": [{"key": "...", "titles": [...], "mal_id": null}], "adult": false}` (<=500 items), one catalog pass. Returns `match` of `mal`/`title`/`synonym`/`ambiguous`/`none` - never a best guess, since a wrong id would exclude an unrelated title. Manga-family only |
| `GET /search/by-mal/{mal_id}?type=manga` | The catalog entry for a MAL id; typed because MAL anime and manga ids are separate id spaces |
| `GET /recommend/{id}` | Ranked similar titles with similarity percentage and per-component scores |
| `GET /recommend?ids=1&ids=2` | Multi-seed blend (up to 5 titles): averaged embedding and tag profile, unioned genres |
| `GET /random?medium=manga` | A random title passing the active filters |
| `POST /mal/{username}/refresh` | Fetch and cache the user's MAL anime + manga lists via Jikan (synchronous: a large list holds the request open for a while at Jikan's rate limit, so avoid aggressive proxy timeouts in front of it) |
| `GET /mal/{username}` | Cached MAL list status |
| `POST /anilist/{username}/refresh` | Fetch and cache the user's AniList anime + manga lists (a few chunked GraphQL queries, no API key) |
| `GET /anilist/{username}` | Cached AniList list status |
| `GET /tags` | Distinct tag vocabulary (for filter autocomplete) |
| `GET /foryou?medium=anime&anilist_user=...` | Personal discovery feed: blends a fresh sample of the user's list into one multi-seed recommendation, excluding everything already on it; seeds come from every configured source (`anilist_user`, `mal_user`, and `exclude_list` lists such as a synced Kitsu/Yamtrack/Suwayomi library) taken round-robin so a large list cannot crowd out a small one, sampled weighted by each title's percentile among the user's own ratings (`use_ratings=false` for uniform sampling; `seeds=N` controls the sample size, fewer = more specific) |
| `POST /exclusions/{name}` | Replace a generic named exclusion list: body `{"anilist_ids": [], "mal_anime_ids": [], "mal_manga_ids": []}`, plus optional `entries` carrying tracker state: `[{"kind": "anilist|mal_anime|mal_manga|kitsu_anime|kitsu_manga", "id": 1, "planned": false, "score": 0-100}]`. Bare ids mean "on the list, status unknown"; entries feed `keep_planned`, For-you sampling and the taste profile. Kitsu ids are resolved to the MAL/AniList ids they map to (unmappable ones are reported in `skipped`). Exclude with `exclude_list={name}` (GET for status, DELETE to remove) |
| `POST /yamtrack/refresh` | Pull the configured Yamtrack instance's anime + manga lists into the `yamtrack` exclusion list (configure via the Accounts panel or `YAMTRACK_URL`/`YAMTRACK_TOKEN`) |
| `POST /kitsu/refresh` | Pull the configured Kitsu user's public anime + manga library into the `kitsu` exclusion list via Kitsu's MAL/AniList id mappings (set the Kitsu username in the Accounts panel; no token needed) |
| `GET` / `PUT /settings` | Account settings (AniList/MAL/Kitsu usernames, Yamtrack endpoint and token) persisted server-side; the token is write-only |
| `GET /dub/coverage` | How far the English dub backfill has got over anime rows: `total`, `checked`, `dubbed`, `without_cast`, and `complete`. While `complete` is false the `dubbed` filter is still hiding unchecked titles |
| `GET /healthz` | Liveness check |

Medium groups match Anibrain's recommenders: `anime`, `manga` (includes
manhwa/manhua), `light_novel`, `one_shot`.

`/recommend/{id}` and `/random` share the filter/weight query params:

- `w_semantic`, `w_tags`, `w_genres` — slider weights, normalized server-side
  (recommend only)
- `w_taste` — optional fourth weight (default 0): boosts candidates similar
  to the user's own rating-weighted taste profile, built from the list
  named by `anilist_user`, `mal_user`, or `exclude_list`. Rating weights
  use each title's percentile among the user's own rated entries (with a
  floor), so the boost works the same whether someone rates 70-90 or 1-10.
  The user's own ratings only - never anyone else's, never popularity
- `cross_media=true` — include all mediums in the candidate pool (recommend
  only, off by default)
- `format` (repeatable), `year_min`, `year_max`, `min_score`, `country`
  (repeatable), `status` (repeatable)
- `genre_in` / `genre_ex` (repeatable) — require or exclude genres;
  `tag_in` / `tag_ex` (repeatable) — same for AniList tags
- `max_popularity=<n>` — obscurity cap: only titles with at most n members
  (a filter the user opts into; popularity still never enters ranking)
- `max_episodes=<n>` / `max_chapters=<n>` / `min_episodes=<n>` /
  `min_chapters=<n>` — length bounds; combine a floor and a cap for a range.
  Unknown lengths pass both: an absent episode count is not evidence a title
  is short, nor that it is long, and excluding it from a floor while a cap
  keeps it would hide every still-releasing series from "at least 50
  episodes" while leaving them in "at most 26".
- `dub_lang=<code>` — only anime known to be dubbed in that language.
  Lowercase, ISO-639-1 where one exists, with a language-region tag only
  where it separates real dub markets (`pt-br`, `zh-cn`, `zh-hk`, `zh-tw`).
  **Do not hardcode the list**: `GET /dub/languages` returns the languages
  the catalog actually has with a title count each, and the web picker is
  built from it. A hardcoded list drifted once already - the UI offered nine
  options while the database held 29.
  A title's own language is never listed: a Japanese cast on a Japanese show
  is the original, not a dub, so the language implied by `country_of_origin`
  is stripped - which is why `ja` appears at all, on the Chinese and Korean
  originals where a Japanese cast genuinely is a dub.
  `dubbed=true` is shorthand for `dub_lang=en`. Positive only: there is no
  sub-only option, because an absent cast is not evidence of one.
- Dub languages come from two sources that are **complementary, not
  redundant**, measured over the live 22,667-title catalog:
  - **AniList**, measured per entry. One request per fifty titles returns
    *every* language at once: `voiceActors` is asked with no language
    argument and each actor's `languageV2` is read, so ~444 requests cover
    the whole catalog in about an hour. Filtering by language instead would
    cost that per language, and aliasing several languages into one request
    silently returns the same list under every alias.
  - **ANN**, matched on title. Returns every language at once too, in ~45
    minutes, but only for the 31.8% of titles it can be matched to by name -
    12,882 ANN entries against 22,667 catalog rows, so 43% have no entry at
    all and no matcher can exceed 56.8%.

  AniList wins on 18 languages (`en`, `de`, `it`, `pt`, `ko`, `pl`, `hu`,
  `no`, `sv`, `he`, `id`, `zh`, `th`, `hi`, `fi`, `tr`, `ca`, `vi`) and ANN
  on 8 (`tl` 566 vs 20, `ar` 213 vs 9, `nl`, `ru`, `da`, and the Chinese
  variants), with `es` and `fr` effectively tied and each knowing titles the
  other does not. The union beats the better single source by 24%. So both
  are kept, and a language either knows about satisfies the filter.
- English additionally has **franchise propagation**, which infers a dub from
  a dubbed sequel, prequel, parent or side story - one query, no network, and
  worth +2,537 titles, which is more than ANN's net contribution to English.
  `dub_source` on each result says which answered (`measured`, `ann` or
  `franchise`); a measured language outranks a name match even when they
  agree.
- Measured English coverage is **31.0% of the catalog** (7,016 of 22,667).
  That is a floor rather than a rate: 38.2% of the catalog has no cast
  recorded anywhere, so nothing says those are undubbed. Among titles where a
  cast exists at all, 29-35% have an English one, confirmed by two
  independent measurements (the live catalog and a random 1,000-title
  sample).
- Measured against ANN, AniList's silence is right for standalone entries
  and wrong for side entries: 2 of 30 exactly-matched silent titles turned
  out to be dubbed (~7%), and the misses were seasons and OVAs of dubbed
  franchises. See `pipeline.probe_dub_crosscheck`.
  Positive only, by design: there is no sub-only option, because an empty
  English cast is not evidence of one. Measured coverage runs 100% at the
  top of the catalog but ~60-67% around rank 3000-5000, and the misses are
  real dubs (Berserk 2 (2017): 21 characters, 0 English voice actors), so
  inverting it would hide dubbed titles from exactly the obscure end this
  engine exists to surface. Titles the backfill has not reached yet are
  excluded while the filter is on - see `GET /dub/coverage`
- `adult=true` — include adult entries (excluded at the SQL level by default)
- `mal_user=<username>` — exclude everything on the cached MAL anime + manga
  lists (any status)
- `anilist_user=<username>` — same, for a cached AniList list (matched by
  AniList id directly)
- `exclude_list=<name>` (repeatable) — exclude everything on the named
  generic exclusion lists, combined (see `POST /exclusions/{name}`; any
  tracker that can export AniList or MAL ids can feed one, the Yamtrack
  integration fills the `yamtrack` list, and the Kitsu integration fills
  the `kitsu` list)
- `keep_planned=true` — plan-to-watch/plan-to-read entries stop excluding
  (so planned titles can still be recommended); started and finished
  entries (watching/reading, completed, on hold, dropped) always exclude.
  Applies to all list sources; ids posted manually to a generic list carry
  no status and always exclude. Kitsu/Yamtrack lists synced before this
  feature need one re-sync to pick up the planned flags.

Search also matches alternate titles (AniList synonyms) once entries carry
them; synonyms arrive via the nightly incremental sync, or immediately for
the whole catalog by re-running the full sync.

Displayed similarity percentages are calibrated (monotonic, order-preserving)
so top matches read on an Anibrain-like scale; raw per-component scores are
returned alongside each result.

## Configuration

All settings come from environment variables (see `.env.example`):

| variable | default | purpose |
| -------- | ------- | ------- |
| `DATABASE_URL` | `postgresql://mangabrain:mangabrain@localhost:5432/mangabrain` | Postgres connection |
| `EMBED_MODEL` | `avsolatorio/GIST-Embedding-v0` | embedding model (384/768-dim indexed; other dims need a schema index pair); changing it re-embeds via `pipeline.embed`, old rows serve until done, then `--prune-stale` |
| `HTTP_CACHE_DIR` | empty | on-disk response cache for pipeline scripts (dev) |
| `ANILIST_MIN_INTERVAL` | `2.0` | seconds between AniList requests |
| `JIKAN_MIN_INTERVAL` | `0.5` | seconds between Jikan requests |
| `SYNC_INTERVAL_HOURS` | `24` | worker pass interval |
| `DUB_BACKFILL_PER_PASS` | `400` | titles the nightly pass checks for an English dub (one AniList request each); 0 disables. At this rate a ~20k anime catalog takes weeks to fill, so the initial fill is a one-off `python -m pipeline.backfill_dub` |
| `DUB_RECHECK_DAYS` | `90` | age at which an already-checked title becomes eligible again, since dubs are often added months after airing; never-checked titles are always walked first. 0 disables rechecks |
| `MANGABRAIN_EMBED_THREADS` | `0` | torch threads while embedding; 0 = cores minus one (the worker also runs at reduced CPU weight so other apps win under contention) |
| `AUTH_TOKEN` | empty | when set, every API request must carry `Authorization: Bearer <token>`; the web app asks once and remembers it. Set this before exposing the app publicly |
| `RATE_LIMIT_PER_MINUTE` | `0` (off) | per-client-IP cap on API requests (reads `CF-Connecting-IP` behind Cloudflare) |

## Android and phone use

The web app is an installable PWA: open it in Chrome on Android (or Safari
on iOS), choose "Add to Home Screen" / "Install app", and it runs as a
standalone full-screen app with its own icon. There is no separate native
client to install or update - the app on your phone is always exactly as
current as your server.

## Exposing publicly (Cloudflare Tunnel)

The stack is designed for LAN use; before pointing a tunnel at it, harden:

1. **Set an access token**: `MANGABRAIN_AUTH_TOKEN=<long random string>` in
   `.env` (e.g. `openssl rand -hex 32`), then `docker compose up -d`. Every
   API request now requires the token; the web app prompts for it once on
   first use and stores it in the browser. Without this, anyone with the
   URL can read your lists, rewrite your settings (including pointing the
   Yamtrack URL at arbitrary hosts), and delete your exclusion lists.
2. **Rate limit**: `MANGABRAIN_RATE_LIMIT=120` caps API requests per client
   IP per minute (recommend/foryou queries are the expensive ones). Real
   client IPs are read from `CF-Connecting-IP`.
3. **Tunnel only the API port**: point `cloudflared` at
   `http://localhost:8009`. Postgres stays bound to `127.0.0.1` and must
   never be tunneled.
4. **Prefer Cloudflare Access on top**: a free Zero Trust policy (email
   OTP) in front of the tunnel keeps unauthenticated traffic off your box
   entirely; the app token then acts as defense in depth. In the Cloudflare
   dashboard: Zero Trust > Access > Applications > add your hostname.
5. The app also sends hardening headers (CSP restricting scripts to the
   app itself, nosniff, frame denial) on every response, and adult content
   remains excluded unless explicitly toggled per request.

Minimal `cloudflared` config:

```
tunnel: <tunnel-id>
credentials-file: /root/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: mangabrain.example.com
    service: http://localhost:8009
  - service: http_status:404
```

## Backups

The weekly published dataset covers the catalog, but not your personal data
(MAL/AniList list caches). For full backups, `backup.sh` dumps everything
(catalog, embeddings, user lists) to a timestamped file and keeps the newest
eight:

```
./backup.sh /path/to/backup/dir
```

Schedule it weekly (OpenMediaVault: Services, Scheduled Jobs). Restore with:

```
docker cp <file> mangabrain-worker-1:/tmp/restore.dump
docker exec mangabrain-worker-1 python -m pipeline.restore --file /tmp/restore.dump --force
```

## Tests and linting

```
pytest
ruff check .
```

`tests/test_scoring.py` is the popularity firewall: it asserts the ranking
function's inputs and the scoring module source contain no audience-size
signals (popularity, favourites, members, trending).

## Project structure

```
/api        FastAPI app: routers /search, /recommend/{id}, /random, /mal/{username}
/pipeline   sync_anilist.py, embed.py, backfill_jikan.py, backfill_dub.py,
            backfill_dub_ann.py, propagate_dub.py, ann.py, nightly.py (worker loop)
/web        vanilla SPA: medium tabs, search, sliders, filter panel, surprise button
/db         schema.sql
```

## Non-goals (v1)

- Multi-user accounts, ratings, or collaborative filtering.
- Streaming/reading links or any scraping; official/public APIs only.
- Character/people/studio similarity (title-level only).

## Credits

- App icon: [Psychology icon](https://www.flaticon.com/free-icon/psychology_9566463)
  from Flaticon, used under the Flaticon Free License (attribution required).
