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
    media_type          TEXT NOT NULL CHECK (media_type IN ('ANIME', 'MANGA', 'BOOK', 'MOVIE', 'TV')),
    medium              TEXT NOT NULL CHECK (medium IN
                            ('anime', 'manga', 'manhwa', 'manhua', 'light_novel',
                             'one_shot', 'book', 'movie', 'tv')),
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

-- English dub signal, backfilled by pipeline.backfill_dub from AniList's
-- cast-by-voice-actor-language data (AniList has no dub field). Measured
-- coverage: 100% of the most popular titles credit an English voice actor,
-- ~93% at rank 1000, ~60-67% at rank 3000-5000, and 0% in the least popular
-- strata, where entries have no cast recorded at all.
--
-- This supports a POSITIVE filter only. english_va_count > 0 is a reliable
-- "has an English dub"; english_va_count = 0 is NOT a reliable "sub only",
-- because midlist titles with real dubs come back empty (Berserk 2 (2017):
-- 21 characters, 0 English voice actors). Never invert this column into a
-- sub-only filter. NULL means not yet checked, which is distinct from 0.
ALTER TABLE media ADD COLUMN IF NOT EXISTS english_va_count INTEGER;
-- Cast edges seen when checking, so "no cast recorded" (0) is
-- distinguishable from "cast recorded, no English voice actor".
ALTER TABLE media ADD COLUMN IF NOT EXISTS cast_character_count INTEGER;
-- AniList dubGroup values, e.g. {4Kids,Odex} on One Piece. Usually empty.
ALTER TABLE media ADD COLUMN IF NOT EXISTS dub_groups TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE media ADD COLUMN IF NOT EXISTS dub_checked_at TIMESTAMPTZ;

-- Set by pipeline.propagate_dub when a franchise relative has a measured
-- English cast. Kept separate from english_va_count so "AniList credits an
-- English voice actor for this entry" and "a relative of this entry is
-- dubbed" never collapse into one claim. Measured because AniList's silence
-- is right per entry and wrong for side entries: of the AniList-silent
-- titles that resolved exactly against ANN, 0 of 7 had an English cast,
-- while BEASTARS FINAL SEASON Part 2 (25 characters, no English voice actor
-- on AniList) belongs to a series ANN credits 18 English cast entries to.
ALTER TABLE media ADD COLUMN IF NOT EXISTS english_dub_inferred BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_media_english_dub_inferred
    ON media (id) WHERE english_dub_inferred;

-- Every language ANN credits a cast in, lowercase ISO-639-1 ('en', 'de',
-- 'es', 'fr', 'it', 'ja', 'ko', 'pt', 'ru', 'tl'). AniList can only be asked
-- about one language per aliased field and its enum covers ten, while ANN
-- tags every cast entry with its language in the same response: 58 of 58 on
-- the sampled title, across nine languages. So AniList stays the English
-- source, measured per entry, and ANN fills in the rest.
--
-- It is also the cheaper source by a wide margin. AniList will not populate a
-- nested connection across a page of parents, so its cast costs one request
-- per title (~22k, over twelve hours), while ANN hands over its whole anime
-- list in ~22 paged requests and then 20 casts per request.
ALTER TABLE media ADD COLUMN IF NOT EXISTS dub_languages TEXT[] NOT NULL DEFAULT '{}';
-- Which source supplied dub_languages, so a thin ANN match stays
-- distinguishable from a measured AniList cast.
ALTER TABLE media ADD COLUMN IF NOT EXISTS dub_languages_source TEXT;
-- The matched ANN entry, kept so a questionable row can be traced back to
-- the record it came from rather than being taken on faith.
ALTER TABLE media ADD COLUMN IF NOT EXISTS ann_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_media_dub_languages
    ON media USING gin (dub_languages);

-- Per-entry dub languages from the MAL-derived cast API (pipeline
-- .backfill_dub_tenrai), joined on id_mal and therefore free of the name
-- matching that caps dub_languages at 31.8% of the catalog. Kept in its
-- own column rather than merged into measured_dub_languages, which means
-- "AniList answered for this id": provenance is what lets dub_source tell
-- a reader which database believes a title is dubbed.
ALTER TABLE media ADD COLUMN IF NOT EXISTS mal_dub_languages TEXT[] NOT NULL DEFAULT '{}';
-- When it was asked, so a resumable walk can pick up where it stopped and
-- a stale row can be re-asked later. NULL means never asked, which is not
-- the same as asked and found nothing.
ALTER TABLE media ADD COLUMN IF NOT EXISTS mal_dub_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_media_mal_dub_languages
    ON media USING gin (mal_dub_languages);

-- Partial index: the filter only ever asks for titles known to be dubbed.
CREATE INDEX IF NOT EXISTS idx_media_english_dub
    ON media (id) WHERE english_va_count > 0;

-- Per-entry failure record for the dub backfill, mirroring
-- jikan_backfill_state: an entry that failed is skipped on the next pass
-- unless --retry-failed is given, so one bad id cannot stall a 20k-title
-- walk that takes hours.
CREATE TABLE IF NOT EXISTS dub_backfill_state (
    media_id        INTEGER PRIMARY KEY REFERENCES media (id) ON DELETE CASCADE,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_status     TEXT,
    last_attempt    TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

-- One embedding per (media row, embedding version), shared vector space
-- across all mediums. Re-embedding with a new model inserts rows alongside
-- the old version's, so recommendations keep serving from the complete old
-- space until the new one has coverage (the API picks the version per
-- request, see _choose_embed_model); `pipeline.embed --prune-stale` then
-- retires the old rows. The column is dimension-untyped so versions with
-- different dimensions (384-dim small models, 768-dim base models) can
-- coexist; ANN queries cast to a fixed dimension to use the partial
-- indexes below.
CREATE TABLE IF NOT EXISTS embeddings (
    media_id    INTEGER NOT NULL REFERENCES media (id) ON DELETE CASCADE,
    embed_model TEXT NOT NULL,
    embedding   vector NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (media_id, embed_model)
);

-- Migration for installs (and restored dumps) created when the table was
-- one row per media with a fixed vector(384) column. The old HNSW index
-- goes first: ALTER TYPE would otherwise rebuild it just to drop it.
DROP INDEX IF EXISTS idx_embeddings_hnsw;
DO $$
BEGIN
    IF (SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'embeddings'::regclass AND attname = 'embedding') <> -1 THEN
        ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector USING embedding::vector;
    END IF;
    IF (SELECT array_length(conkey, 1) FROM pg_constraint
        WHERE conrelid = 'embeddings'::regclass AND contype = 'p') = 1 THEN
        EXECUTE (SELECT format('ALTER TABLE embeddings DROP CONSTRAINT %I', conname)
                 FROM pg_constraint
                 WHERE conrelid = 'embeddings'::regclass AND contype = 'p');
        ALTER TABLE embeddings ADD PRIMARY KEY (media_id, embed_model);
    END IF;
END $$;

-- HNSW cannot index a dimension-untyped column directly; one partial
-- expression index per dimension in use covers it (pgvector's documented
-- recipe for mixed-dimension columns). Queries must repeat both the cast
-- and the vector_dims predicate to hit these. A future model with another
-- dimension needs its index added here.
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_384
    ON embeddings USING hnsw ((embedding::vector(384)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 384;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_768
    ON embeddings USING hnsw ((embedding::vector(768)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 768;

-- There was a binary-quantized copy of the embedding here, for a coarse
-- pass that could read the whole catalog exactly. Measured and removed;
-- the numbers are kept so nobody rebuilds it on the same reasoning.
--
-- The premise was sound. A 768-dim float32 vector is 3,072 bytes, over
-- the ~2 KB TOAST threshold, so an exact sequential scan pays an
-- out-of-line fetch and a decompression per row - EXPLAIN showed
-- 1,880,352 shared buffer hits, about 15 GB of traffic, for one query.
-- 768 bits is 96 bytes and stores inline, which should have made an
-- exact scan affordable.
--
-- It did not pay. Two-stage (exact Hamming scan, rerank 2,000 on the
-- real vectors) measured 232 ms at 77-85% recall of the exact top 60,
-- against the HNSW index at 5.8 ms and 96% on the path a caller
-- actually uses. binary_quantize thresholds at zero and this model's
-- dimensions are not centred there, which is the likely cause; centring
-- first might fix it, and would be a research task, not a tuning one.
--
-- The 4.0 s exact scan that motivated it was also partly my own error:
-- that query read all 165,000 embeddings because media_type was
-- filtered in a join above it. Restricted to anime it is ~713 ms. Still
-- too slow to serve, but not the emergency it looked like.
ALTER TABLE embeddings DROP COLUMN IF EXISTS embedding_bits;

-- dub_backfill_state holds a foreign key into media, and pipeline.restore
-- drops it so pg_restore --clean can replace the snapshot's tables -
-- pg_restore has no CASCADE, and one such key stopped a whole restore.
-- An idempotent table creation cannot put the constraint back on a
-- table that already exists, so it is re-stated here, the same way the
-- media CHECK constraints are.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dub_backfill_state_media_id_fkey'
          AND conrelid = 'dub_backfill_state'::regclass
    ) THEN
        ALTER TABLE dub_backfill_state
            ADD CONSTRAINT dub_backfill_state_media_id_fkey
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE;
    END IF;
END $$;

-- Provenance, so the catalogue can hold entries AniList does not have.
--
-- media.id has always been the AniList id, and it is in URLs, in
-- embeddings.media_id and in media_relations, so it cannot be
-- renumbered without breaking links people already hold. Instead the
-- id stays what it is for AniList rows, and every row now also records
-- WHERE it came from and WHAT it is called there.
--
-- Entries from other sources take ids from a sequence that starts above
-- anything AniList will issue. INTEGER, deliberately, not BIGINT: the
-- ceiling is 2,147,483,647, so starting at 1,000,000,000 leaves room
-- for 1.1 billion non-AniList entries against Open Library's ~40
-- million works - and widening the column would rewrite the table and
-- its TOAST under an exclusive lock, in ensure_schema(), at API
-- startup. That mistake was already made once here with a generated
-- column and reverted; adding a column is instant, retyping one is not.
ALTER TABLE media ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anilist';
ALTER TABLE media ADD COLUMN IF NOT EXISTS source_id TEXT;
UPDATE media SET source_id = id::text WHERE source_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_source ON media (source, source_id);
CREATE SEQUENCE IF NOT EXISTS media_external_id_seq START WITH 1000000000;

-- Ids a title carries on other sites, for telling the same show apart
-- when two sources hold it: TV comes from TVmaze first and TMDB second,
-- and both name a show's IMDb and TheTVDB ids. {"imdb": "tt0944947",
-- "tvdb": "121361"}. Empty for sources with nothing to match.
ALTER TABLE media ADD COLUMN IF NOT EXISTS external_ids JSONB NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_media_ext_imdb ON media ((external_ids->>'imdb'))
    WHERE external_ids ? 'imdb';
CREATE INDEX IF NOT EXISTS idx_media_ext_tvdb ON media ((external_ids->>'tvdb'))
    WHERE external_ids ? 'tvdb';

-- TMDB's keywords on TVmaze shows (pipeline.sync_tmdb tvmaze-keywords).
-- TVmaze has no keywords, so a TVmaze show's tags are the keywords of the
-- TMDB show it matches. Install-only, like everything from TMDB: these two
-- columns are not among the TV dataset's, and its export refuses a
-- database whose TVmaze rows carry tags. tmdb_match_id is the matched
-- TMDB show (NULL when none matched); tmdb_keywords_at is when it was last
-- looked up, kept inside TMDB's six-month cache limit.
ALTER TABLE media ADD COLUMN IF NOT EXISTS tmdb_match_id INTEGER;
ALTER TABLE media ADD COLUMN IF NOT EXISTS tmdb_keywords_at TIMESTAMPTZ;

-- The CHECK constraints are inline in CREATE TABLE, which only helps a
-- fresh database; an existing one keeps the constraint it was built
-- with, so they are re-stated here - but only when the stored definition
-- is missing a value, and NOT VALID.
--
-- Both halves matter. This script runs on every API and worker start, as
-- one transaction, and the old version dropped and re-added both
-- constraints every time: an ACCESS EXCLUSIVE lock on media plus a scan
-- of every row (1.6 million with books), held to the end of the script.
-- Behind a worker mid-import or mid-embed that stalled API startup until
-- Cloudflare answered 502 (6 Nov 2026, adding MOVIE and TV). Now it is a
-- catalogue lookup on a normal start, and on a vocabulary change a brief
-- lock with no scan: NOT VALID skips checking existing rows, every one of
-- which was written under the narrower rule, and still checks every new
-- row.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'media'::regclass AND conname = 'media_media_type_check'
          AND pg_get_constraintdef(oid) LIKE '%''TV''%'
    ) THEN
        ALTER TABLE media DROP CONSTRAINT IF EXISTS media_media_type_check;
        ALTER TABLE media ADD CONSTRAINT media_media_type_check
            CHECK (media_type IN ('ANIME', 'MANGA', 'BOOK', 'MOVIE', 'TV')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'media'::regclass AND conname = 'media_medium_check'
          AND pg_get_constraintdef(oid) LIKE '%''tv''%'
    ) THEN
        ALTER TABLE media DROP CONSTRAINT IF EXISTS media_medium_check;
        ALTER TABLE media ADD CONSTRAINT media_medium_check
            CHECK (medium IN ('anime', 'manga', 'manhwa', 'manhua', 'light_novel',
                              'one_shot', 'book', 'movie', 'tv')) NOT VALID;
    END IF;
END $$;

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

-- planned marks entries the tracker reports as plan-to-watch/plan-to-read;
-- the keep_planned query option skips excluding those. Existing rows default
-- to FALSE (always excluded) until the next tracker sync fills the flag.
ALTER TABLE custom_exclusion_entries
    ADD COLUMN IF NOT EXISTS planned BOOLEAN NOT NULL DEFAULT FALSE;

-- User scores normalized to 0-100 (NULL = unrated), used to weight the
-- For-you feed's seed sampling toward loved titles. Filled by tracker syncs.
ALTER TABLE custom_exclusion_entries ADD COLUMN IF NOT EXISTS score SMALLINT;
ALTER TABLE mal_list_entries ADD COLUMN IF NOT EXISTS score SMALLINT;
ALTER TABLE anilist_list_entries ADD COLUMN IF NOT EXISTS score SMALLINT;

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

-- Single-user application settings (account usernames, tracker endpoints),
-- editable from the web UI and read with env-variable fallback.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Generic key/value store for pipeline checkpoints (resumable sync).
CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dub languages measured per entry from AniList, as opposed to the
-- name-matched set in dub_languages that comes from ANN. Kept apart rather
-- than merged: ANN resolves 31.8% of the catalog by title and a wrong match
-- asserts another show's dub, while AniList answers for the id it was given,
-- so the two claims carry different weight and the API reports which is which.
ALTER TABLE media ADD COLUMN IF NOT EXISTS measured_dub_languages TEXT[] NOT NULL DEFAULT '{}';

-- Which languages have been ASKED about, so that "asked, no cast credited"
-- stays distinguishable from "never asked" - the same reason english_va_count
-- keeps NULL apart from 0. Without this a title with no Spanish dub and a
-- title nobody has checked for Spanish look identical, and a positive-only
-- filter cannot tell the user which it is looking at.
ALTER TABLE media ADD COLUMN IF NOT EXISTS dub_languages_checked TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_media_measured_dub_languages
    ON media USING gin (measured_dub_languages);

-- When a row was last measured for dub languages. One unfiltered request
-- returns every language at once (voiceActors without a language argument,
-- read via Staff.languageV2), so progress is a single timestamp per row
-- rather than a set of languages asked about.
--
-- This supersedes dub_languages_checked, which was sized for a pass that
-- asked one language per request. That column is left in place rather than
-- dropped, because dropping it would discard data on a deployment that has
-- already run the per-language pass; it is simply no longer written.
ALTER TABLE media ADD COLUMN IF NOT EXISTS measured_dub_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_media_measured_dub_unchecked
    ON media (id) WHERE measured_dub_checked_at IS NULL;

-- Title search, indexed. /search matches every query word as a substring of
-- a row's titles and synonyms run together, lowercased, with punctuation and
-- spaces removed ("ironman" finds "Iron Man", "dogs" finds "Dog's"). That
-- was computed per row on every search, a scan of ~2 million rows that took
-- over 25 seconds on a NAS once books arrived. A trigram index over the same
-- expression answers LIKE '%word%' directly. The function must stay in step
-- with IGNORED_PUNCTUATION in api/routers/search.py (a test compares them).
-- IMMUTABLE so it can be indexed; array_to_string is only STABLE because of
-- type output in general, and for text[] its result never changes.
CREATE OR REPLACE FUNCTION mb_title_search(
    romaji TEXT, english TEXT, native TEXT, synonyms TEXT[]
) RETURNS TEXT LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT lower(translate(
        coalesce(romaji, '') || coalesce(english, '') || coalesce(native, '')
            || coalesce(array_to_string(synonyms, ''), ''),
        '''’‘ʼ`´.,:;!?"“”-‐–—()/ ', ''))
$$;
CREATE INDEX IF NOT EXISTS idx_media_title_search
    ON media USING gin (
        mb_title_search(title_romaji, title_english, title_native, synonyms) gin_trgm_ops
    );
