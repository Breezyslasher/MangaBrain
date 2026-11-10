"""Connection pool, schema bootstrap, and row helpers shared by all routers."""

from pathlib import Path
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.config import settings
from api.links import links_for
from api.models import MediaOut

_pool: ConnectionPool | None = None


def _schema_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent / "db" / "schema.sql",
        Path.cwd() / "db" / "schema.sql",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# Arbitrary app-wide advisory lock key for schema application.
_SCHEMA_LOCK_KEY = 727_272


def ensure_schema() -> None:
    """Apply db/schema.sql on every startup. The schema file is fully
    idempotent (IF NOT EXISTS tables/indexes plus ADD COLUMN IF NOT EXISTS
    migrations), so this both bootstraps a fresh database and migrates an
    existing one when new tables or columns land. Must run before the pool
    opens: register_vector fails until the extension exists.

    Serialized with an advisory lock: api and worker both call this at first
    boot, and two concurrent schema applies can collide. The second caller
    waits, re-runs the idempotent statements, and changes nothing."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_KEY,))
        try:
            schema = _schema_path()
            if schema is None:
                row = conn.execute("SELECT to_regclass('media')").fetchone()
                if row is not None and row[0] is not None:
                    return
                raise RuntimeError("media table missing and db/schema.sql not found")
            conn.execute(schema.read_text())
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_LOCK_KEY,))


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=8,
            configure=register_vector,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


_hnsw_iterative_scan: bool | None = None


def hnsw_supports_iterative_scan(conn: Any) -> bool:
    """pgvector 0.8+ can keep scanning the HNSW index until the LIMIT is
    satisfied even when WHERE clauses discard most neighbors. Cached: the
    extension version cannot change while the server is up."""
    global _hnsw_iterative_scan
    if _hnsw_iterative_scan is None:
        row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        version = (row["extversion"] if row else "") or "0.0"
        parts = version.split(".")
        try:
            major, minor = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            major, minor = 0, 0
        _hnsw_iterative_scan = (major, minor) >= (0, 8)
    return _hnsw_iterative_scan


# Alternate titles are returned for client-side matching, but a 50-result
# response must not balloon: a handful is enough to recognise an entry.
MAX_SYNONYMS = 10

# Canonical column list for anything returning media rows. The cleaned
# description is what the API exposes; tags stay internal (used for scoring).
MEDIA_COLS = """
    m.id, m.id_mal, m.medium, m.title_romaji, m.title_english, m.title_native,
    m.description_clean AS description, m.genres, m.tags, m.format,
    m.episodes, m.chapters, m.volumes, m.country_of_origin, m.start_year,
    m.status, m.average_score, m.cover_image_medium, m.cover_image_large,
    m.is_adult, m.synonyms, m.popularity, m.favourites,
    m.english_va_count, m.dub_groups, m.english_dub_inferred, m.dub_languages,
    m.measured_dub_languages, m.mal_dub_languages, m.source, m.source_id
"""

# Full rows for an already-decided set of ids.
#
# The pattern this serves: rank on a narrow query over a large pool, then
# widen to full rows for the few that survive. MEDIA_COLS carries every
# synopsis, so using it for the pool meant reading thousands of them per
# request and discarding almost all - /describe pools 2,000 per facet and
# /recommend 500, where both serve 60.
#
# Rows come back in index order, not the caller's, so the caller keys by id
# and re-applies its own ranking.
MEDIA_ROWS_SQL = f"""
    SELECT {MEDIA_COLS}
    FROM media m
    WHERE m.id = ANY(%(ids)s)
"""


def media_from_row(row: dict[str, Any]) -> MediaOut:
    return MediaOut(
        id=row["id"],
        id_mal=row["id_mal"],
        medium=row["medium"],
        title=row["title_romaji"] or row["title_english"] or row["title_native"],
        title_english=row["title_english"],
        title_native=row["title_native"],
        description=row["description"],
        genres=row["genres"] or [],
        tags=[
            t["name"]
            for t in sorted(row["tags"] or [], key=lambda t: t.get("rank") or 0, reverse=True)
            if t.get("name")
        ][:15],
        format=row["format"],
        episodes=row["episodes"],
        chapters=row["chapters"],
        volumes=row["volumes"],
        country_of_origin=row["country_of_origin"],
        start_year=row["start_year"],
        status=row["status"],
        average_score=row["average_score"],
        cover_image=row["cover_image_medium"],
        cover_image_large=row["cover_image_large"],
        is_adult=row["is_adult"],
        synonyms=(row["synonyms"] or [])[:MAX_SYNONYMS],
        popularity=row["popularity"],
        favourites=row["favourites"],
        has_english_dub=_has_dub(row),
        dub_groups=row.get("dub_groups") or [],
        dub_languages=row.get("dub_languages") or [],
        # Both per-entry measurements were selected and neither was served:
        # the model declared them and media_from_row never set them, so a
        # client asking which database knows what got an empty list.
        measured_dub_languages=row.get("measured_dub_languages") or [],
        mal_dub_languages=row.get("mal_dub_languages") or [],
        dub_source=_dub_source(row),
        links=links_for(row),
    )


def _has_dub(row: dict[str, Any]) -> bool | None:
    """Whether an English dub is known, from any source.

    None means nothing is known yet, which is distinct from a checked entry
    that has none; the filter excludes both, but a client can say which.
    """
    if "en" in (row.get("dub_languages") or []):
        return True
    if "en" in (row.get("measured_dub_languages") or []):
        return True
    if "en" in (row.get("mal_dub_languages") or []):
        return True
    if row.get("english_dub_inferred"):
        return True
    count = row.get("english_va_count")
    return None if count is None else count > 0


def _dub_source(row: dict[str, Any]) -> str | None:
    """Most direct evidence first: measured, then ANN, then inherited.

    Both measured forms outrank ANN. english_va_count and
    measured_dub_languages are AniList answering for the id it was given;
    ANN is a title match that resolves 31.8% of the catalog and can match
    the wrong show, so it is weaker evidence even when it agrees.
    """
    if (row.get("english_va_count") or 0) > 0:
        return "measured"
    if "en" in (row.get("measured_dub_languages") or []):
        return "measured"
    # Between AniList's own answer and ANN's title match: it is a
    # per-entry answer on an id join, so it cannot be about another show,
    # but it comes from a third-party aggregator rather than from the
    # database that owns the entry.
    if "en" in (row.get("mal_dub_languages") or []):
        return "mal"
    if "en" in (row.get("dub_languages") or []):
        return "ann"
    if row.get("english_dub_inferred"):
        return "franchise"
    return None
