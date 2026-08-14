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
- Each title is embedded with `BAAI/bge-small-en-v1.5`
  (384-dim, CPU-friendly) over its genres, top ranked tags, and synopsis
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
snapshot (media, relations, embeddings, sync checkpoints) as the
`dataset-latest` release. To run without building or syncing anything, copy
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
| `GET /recommend/{id}` | Ranked similar titles with similarity percentage and per-component scores |
| `GET /recommend?ids=1&ids=2` | Multi-seed blend (up to 5 titles): averaged embedding and tag profile, unioned genres |
| `GET /random?medium=manga` | A random title passing the active filters |
| `POST /mal/{username}/refresh` | Fetch and cache the user's MAL anime + manga lists via Jikan (synchronous: a large list holds the request open for a while at Jikan's rate limit, so avoid aggressive proxy timeouts in front of it) |
| `GET /mal/{username}` | Cached MAL list status |
| `POST /anilist/{username}/refresh` | Fetch and cache the user's AniList anime + manga lists (a few chunked GraphQL queries, no API key) |
| `GET /anilist/{username}` | Cached AniList list status |
| `GET /tags` | Distinct tag vocabulary (for filter autocomplete) |
| `GET /foryou?medium=anime&anilist_user=...` | Personal discovery feed: blends a fresh sample of the user's list into one multi-seed recommendation, excluding everything already on it; seeds come from `anilist_user`, `mal_user`, or `exclude_list` (a synced Kitsu/Yamtrack list), sampled weighted by each title's percentile among the user's own ratings (`use_ratings=false` for uniform sampling; `seeds=N` controls the sample size, fewer = more specific) |
| `POST /exclusions/{name}` | Replace a generic named exclusion list: body `{"anilist_ids": [], "mal_anime_ids": [], "mal_manga_ids": []}`; exclude with `exclude_list={name}` (GET for status, DELETE to remove) |
| `POST /yamtrack/refresh` | Pull the configured Yamtrack instance's anime + manga lists into the `yamtrack` exclusion list (configure via the Accounts panel or `YAMTRACK_URL`/`YAMTRACK_TOKEN`) |
| `POST /kitsu/refresh` | Pull the configured Kitsu user's public anime + manga library into the `kitsu` exclusion list via Kitsu's MAL/AniList id mappings (set the Kitsu username in the Accounts panel; no token needed) |
| `GET` / `PUT /settings` | Account settings (AniList/MAL/Kitsu usernames, Yamtrack endpoint and token) persisted server-side; the token is write-only |
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
- `max_episodes=<n>` / `max_chapters=<n>` — length caps (unknown lengths stay
  included)
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
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | embedding model (must be 384-dim); changing it re-embeds via `pipeline.embed` |
| `HTTP_CACHE_DIR` | empty | on-disk response cache for pipeline scripts (dev) |
| `ANILIST_MIN_INTERVAL` | `2.0` | seconds between AniList requests |
| `JIKAN_MIN_INTERVAL` | `0.5` | seconds between Jikan requests |
| `SYNC_INTERVAL_HOURS` | `24` | worker pass interval |

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
/pipeline   sync_anilist.py, embed.py, backfill_jikan.py, nightly.py (worker loop)
/web        vanilla SPA: medium tabs, search, sliders, filter panel, surprise button
/db         schema.sql
```

## Non-goals (v1)

- Multi-user accounts, ratings, or collaborative filtering.
- Streaming/reading links or any scraping; official/public APIs only.
- Character/people/studio similarity (title-level only).
