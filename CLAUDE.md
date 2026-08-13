# CLAUDE.md — AniBrain Rebuild (Full-Scope Recommendation Engine)

## Project Overview

A self-hosted, content-based recommendation engine covering **anime, manga, manhwa, manhua, light novels, and one-shots**, rebuilding the full scope of the now-defunct anibrain.ai. The defining principle: **similarity is computed from content (synopsis, tags, genres, themes), never from popularity**. Obscure titles with high content similarity must rank above popular titles with lower similarity.

Deployment target: Docker Compose on OpenMediaVault (linux/amd64). Single-user first, no auth required initially.

## Core Requirements

1. Separate recommenders per medium, matching Anibrain's structure: `/anime`, `/manga` (includes manhwa/manhua), `/light-novel`, `/one-shot`. Search any title within a medium, get a ranked list of similar titles with a similarity percentage.
2. Cross-media suggestions: given an anime, optionally surface similar manga/LNs and vice versa (toggle, off by default). Source-adaptation pairs (the manga an anime adapts) are linked via AniList relations and shown as "related", not scored as recommendations.
3. Adjustable weighting sliders: genre vs. themes/tags vs. synopsis (semantic) similarity. Recompute rankings via query params without re-embedding.
4. Filters: format (TV/movie/OVA/ONA/special for anime; manga/manhwa/manhua/LN/one-shot for print), year range, minimum score, country of origin, airing/publishing status, adult content toggle (default off, sign-in-free).
5. Random discovery: a "surprise me" endpoint per medium that returns a random title passing the active filters, Anibrain-style.
6. MAL integration: pull the user's **anime list and manga list** via Jikan user endpoints and exclude everything on them (any status) from results. Cache both lists; manual refresh button.
7. Popularity must never enter the ranking function. Member counts and favorites may be displayed but never scored.

## Data Sources

- **AniList GraphQL API** (`https://graphql.anilist.co`) — primary catalog source. Pull BOTH media types (ANIME and MANGA): id, idMal, title (romaji/english/native), description, genres, tags (with rank), format, episodes/chapters/volumes, countryOfOrigin, startDate, status, averageScore, coverImage, isAdult, relations (for adaptation links). Respect rate limits (read X-RateLimit headers, back off). Paginate with perPage=50.
- **Jikan v4** (`https://api.jikan.moe/v4`) — MAL enrichment and user list retrieval (`/users/{username}/animelist`, `/users/{username}/mangalist`) without OAuth. Rate limit: 3 req/sec, 60 req/min.
- Store idMal on every entry so MAL exclusion is a simple join.
- Full catalog sync is a one-time batch job (~20k anime + ~150k manga-family entries); nightly incremental sync by `updatedAt`.

## Architecture

```
services:
  api        FastAPI (Python 3.12) — search, recommend, filters, random, MAL sync
  worker     Same image, runs sync + embedding jobs (invoked via cron or CLI)
  db         PostgreSQL 16 + pgvector
  web        Static SPA (vanilla or Svelte) served by Caddy or the API itself
```

- One `media` table with a `medium` discriminator (anime | manga | manhwa | manhua | light_novel | one_shot), derived from AniList format + countryOfOrigin. One embedding space shared across media — this is what makes cross-media similarity free.
- Embeddings: `sentence-transformers` with `all-MiniLM-L6-v2` (384-dim, CPU-friendly) over `title + cleaned description`. Strip HTML from AniList descriptions before embedding.
- Genre/tag similarity: weighted Jaccard over genres; cosine over a tag vector weighted by AniList tag rank.
- Final score: `w1 * semantic_cos + w2 * tag_sim + w3 * genre_sim`, weights normalized from slider values, defaults 0.5 / 0.3 / 0.2.
- pgvector HNSW index on the embedding column; candidate retrieval by semantic ANN (top 500, filtered to the requested medium unless cross-media is on), then re-rank with the full weighted score and apply filters. Exclude direct adaptations of the seed title from recommendation results (they go in "related").

## Project Structure

```
/api        FastAPI app, routers: /search, /recommend/{id}, /random, /mal/{username}
/pipeline   sync_anilist.py, embed.py, backfill_jikan.py
/web        SPA: medium tabs, search bar, result grid, sliders, filter panel
/db         schema.sql, migrations
docker-compose.yml
```

## Conventions

- Python: ruff + type hints, pydantic models for all API responses.
- All external API calls go through a rate-limited client wrapper with exponential backoff and on-disk response cache (avoid re-hitting AniList during development).
- Embeddings are versioned: store `embed_model` per row; re-embedding is a migration, not an overwrite.
- No popularity, no member counts, no trending signals anywhere in scoring. Add a test that asserts the ranking function's inputs.
- Adult content excluded by default at the SQL level (`is_adult = false` unless the toggle is on).
- Commit style: conventional commits. No emojis anywhere in code, docs, or release notes.

## Commands

```
docker compose up -d db
python -m pipeline.sync_anilist                  # resumable id_in scan, both types in one pass
python -m pipeline.embed                         # generate/update embeddings
uvicorn api.main:app --reload                    # dev server
docker compose up --build                        # full stack
```

## Milestones

1. Catalog sync + schema + embeddings for anime and manga.
2. /recommend endpoint with fixed weights; verify hidden-gem behavior manually against known Anibrain pairings (e.g. Jujutsu Kaisen -> Blue Exorcist ~93% for anime; high-similarity obscure matches, not top-50 spam).
3. SPA with medium tabs, search, sliders, filters.
4. Random discovery endpoint + MAL anime/manga list exclusion.
5. Light novels + one-shots as first-class media; cross-media toggle; nightly incremental sync container.

## Non-Goals (v1)

- Multi-user accounts, ratings, or collaborative filtering.
- Streaming/reading links or any scraping; official/public APIs only.
- Characters/people/studios similarity (title-level only for v1).
