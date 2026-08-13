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
- Synopses are embedded with `sentence-transformers/all-MiniLM-L6-v2`
  (384-dim, CPU-friendly) into a single vector space shared across all
  mediums, which makes cross-media similarity free.
- A recommendation request retrieves the top candidates by semantic ANN
  (pgvector HNSW index) within the seed's medium group, then re-ranks with
  the full weighted score:
  `w1 * semantic_cos + w2 * tag_sim + w3 * genre_sim`
  (defaults 0.5 / 0.3 / 0.2, adjustable per request via query params, no
  re-embedding needed).
- Direct adaptation/source relations of the seed are excluded from results
  and returned separately as "related".

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
| `GET /random?medium=manga` | A random title passing the active filters |
| `POST /mal/{username}/refresh` | Fetch and cache the user's MAL anime + manga lists via Jikan |
| `GET /mal/{username}` | Cached list status |
| `GET /healthz` | Liveness check |

Medium groups match Anibrain's recommenders: `anime`, `manga` (includes
manhwa/manhua), `light_novel`, `one_shot`.

`/recommend/{id}` and `/random` share the filter/weight query params:

- `w_semantic`, `w_tags`, `w_genres` — slider weights, normalized server-side
  (recommend only)
- `cross_media=true` — include all mediums in the candidate pool (recommend
  only, off by default)
- `format` (repeatable), `year_min`, `year_max`, `min_score`, `country`
  (repeatable), `status` (repeatable)
- `adult=true` — include adult entries (excluded at the SQL level by default)
- `mal_user=<username>` — exclude everything on the cached MAL anime + manga
  lists (any status)

## Configuration

All settings come from environment variables (see `.env.example`):

| variable | default | purpose |
| -------- | ------- | ------- |
| `DATABASE_URL` | `postgresql://mangabrain:mangabrain@localhost:5432/mangabrain` | Postgres connection |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | embedding model; changing it re-embeds via `pipeline.embed` |
| `HTTP_CACHE_DIR` | empty | on-disk response cache for pipeline scripts (dev) |
| `ANILIST_MIN_INTERVAL` | `2.0` | seconds between AniList requests |
| `JIKAN_MIN_INTERVAL` | `0.5` | seconds between Jikan requests |
| `SYNC_INTERVAL_HOURS` | `24` | worker pass interval |

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
