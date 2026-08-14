-- MangaBrain schema. Applied automatically on first `docker compose up db`
-- via the initdb mount; apply manually with:
--   psql "$DATABASE_URL" -f db/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- One row per AniList media entry (both ANIME and MANGA types).
-- popularity and favourites are stored for display only; the ranking
-- function must never read them (enforced by tests/test_scoring.py).
CREATE TABLE IF NOT EXISTS media (
    id                  INTEGER PRIMARY KEY,        -- AniList id
    id_mal              INTEGER,                    -- MAL id, used for MAL list exclusion
    media_type          TEXT NOT NULL CHECK (media_type IN ('ANIME', 'MANGA')),
    medium              TEXT NOT NULL CHECK (medium IN
                            ('anime', 'manga', 'manhwa', 'manhua', 'light_novel', 'one_shot')),
    title_romaji        TEXT,
    title_english       TEXT,
    title_native        TEXT,
    description         TEXT,                       -- raw AniList description (may contain HTML)
    description_clean   TEXT,                       -- HTML-stripped, used for embeddings and display
    genres              TEXT[] NOT NULL DEFAULT '{}',
    tags                JSONB NOT NULL DEFAULT '[]',  -- [{"name": ..., "rank": 0-100, "is_adult": bool}]
    format              TEXT,
    episodes            INTEGER,
    chapters            INTEGER,
    volumes             INTEGER,
    country_of_origin   TEXT,
    start_year          INTEGER,
    status              TEXT,
    average_score       INTEGER,
    cover_image_medium  TEXT,
    cover_image_large   TEXT,
    is_adult            BOOLEAN NOT NULL DEFAULT FALSE,
    popularity          INTEGER,                    -- display only, never scored
    favourites          INTEGER,                    -- display only, never scored
    synonyms            TEXT[] NOT NULL DEFAULT '{}',  -- alternate titles, searchable
    updated_at          BIGINT,                     -- AniList updatedAt (unix seconds)
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Migration for installs created before the column existed; the schema is
-- applied idempotently on every service start.
ALTER TABLE media ADD COLUMN IF NOT EXISTS synonyms TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_media_medium ON media (medium);
CREATE INDEX IF NOT EXISTS idx_media_id_mal ON media (id_mal);
CREATE INDEX IF NOT EXISTS idx_media_is_adult ON media (is_adult);
CREATE INDEX IF NOT EXISTS idx_media_start_year ON media (start_year);
CREATE INDEX IF NOT EXISTS idx_media_title_romaji_trgm
    ON media USING gin (title_romaji gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_media_title_english_trgm
    ON media USING gin (title_english gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_media_title_native_trgm
    ON media USING gin (title_native gin_trgm_ops);

-- AniList relations (adaptation links etc). related_id may reference an entry
-- that has not been synced yet, so no foreign key on it.
CREATE TABLE IF NOT EXISTS media_relations (
    media_id        INTEGER NOT NULL REFERENCES media (id) ON DELETE CASCADE,
    related_id      INTEGER NOT NULL,
    relation_type   TEXT NOT NULL,
    PRIMARY KEY (media_id, related_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_media_relations_media ON media_relations (media_id);

-- One embedding per media row, shared vector space across all mediums.
-- embed_model versions the embedding; re-embedding with a new model is a
-- migration that replaces rows, never a silent overwrite.
CREATE TABLE IF NOT EXISTS embeddings (
    media_id    INTEGER PRIMARY KEY REFERENCES media (id) ON DELETE CASCADE,
    embed_model TEXT NOT NULL,
    embedding   vector(384) NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON embeddings USING hnsw (embedding vector_cosine_ops);

-- Cached MAL user lists pulled via Jikan. Everything on either list is
-- excluded from recommendation results when mal_user is passed.
CREATE TABLE IF NOT EXISTS mal_list_entries (
    username    TEXT NOT NULL,
    list_type   TEXT NOT NULL CHECK (list_type IN ('anime', 'manga')),
    id_mal      INTEGER NOT NULL,
    status      TEXT,
    PRIMARY KEY (username, list_type, id_mal)
);

CREATE INDEX IF NOT EXISTS idx_mal_list_entries_lookup ON mal_list_entries (username, id_mal);

CREATE TABLE IF NOT EXISTS mal_list_state (
    username    TEXT NOT NULL,
    list_type   TEXT NOT NULL CHECK (list_type IN ('anime', 'manga')),
    entry_count INTEGER NOT NULL DEFAULT 0,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, list_type)
);

-- Cached AniList user lists, fetched via MediaListCollection. Exclusion is a
-- direct match on the AniList media id, no MAL id join needed.
CREATE TABLE IF NOT EXISTS anilist_list_entries (
    username    TEXT NOT NULL,
    list_type   TEXT NOT NULL CHECK (list_type IN ('anime', 'manga')),
    media_id    INTEGER NOT NULL,
    status      TEXT,
    PRIMARY KEY (username, list_type, media_id)
);

CREATE INDEX IF NOT EXISTS idx_anilist_list_entries_lookup
    ON anilist_list_entries (username, media_id);

CREATE TABLE IF NOT EXISTS anilist_list_state (
    username    TEXT NOT NULL,
    list_type   TEXT NOT NULL CHECK (list_type IN ('anime', 'manga')),
    entry_count INTEGER NOT NULL DEFAULT 0,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, list_type)
);

-- Generic named exclusion lists: any tracker (or hand-curated list) can push
-- AniList ids or MAL ids (typed by media kind, since MAL anime and manga ids
-- are separate id spaces) and exclude them via exclude_list=<name>.
CREATE TABLE IF NOT EXISTS custom_exclusion_entries (
    list_name   TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('anilist', 'mal_anime', 'mal_manga')),
    ext_id      INTEGER NOT NULL,
    PRIMARY KEY (list_name, kind, ext_id)
);

CREATE INDEX IF NOT EXISTS idx_custom_exclusion_lookup
    ON custom_exclusion_entries (list_name);

CREATE TABLE IF NOT EXISTS custom_exclusion_state (
    list_name   TEXT PRIMARY KEY,
    entry_count INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-entry state for the Jikan synopsis backfill: entries that repeatedly
-- fail, 404, or have no synopsis on MAL are not retried on later runs.
-- Also created by pipeline/backfill_jikan.py itself for existing installs.
CREATE TABLE IF NOT EXISTS jikan_backfill_state (
    media_id     INTEGER PRIMARY KEY,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_status  TEXT,
    last_attempt TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Generic key/value store for pipeline checkpoints (resumable sync).
CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
