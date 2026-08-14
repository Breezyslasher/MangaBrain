"""Connection pool, schema bootstrap, and row helpers shared by all routers."""

from pathlib import Path
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.config import settings
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
    """Apply db/schema.sql when the media table is missing, so the prebuilt
    image runs against a fresh database without a repo checkout or initdb
    mount. The schema is fully IF NOT EXISTS, so this is idempotent. Must run
    before the pool opens: register_vector fails until the extension exists.

    Serialized with an advisory lock: api and worker both call this at first
    boot, and two concurrent CREATE TABLE IF NOT EXISTS runs can still
    collide. The second caller waits, re-checks, and finds the schema done."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_KEY,))
        try:
            row = conn.execute("SELECT to_regclass('media')").fetchone()
            if row is not None and row[0] is not None:
                return
            schema = _schema_path()
            if schema is None:
                raise RuntimeError("media table missing and db/schema.sql not found")
            print(f"[db] applying schema from {schema}")
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


# Canonical column list for anything returning media rows. The cleaned
# description is what the API exposes; tags stay internal (used for scoring).
MEDIA_COLS = """
    m.id, m.id_mal, m.medium, m.title_romaji, m.title_english, m.title_native,
    m.description_clean AS description, m.genres, m.tags, m.format,
    m.episodes, m.chapters, m.volumes, m.country_of_origin, m.start_year,
    m.status, m.average_score, m.cover_image_medium, m.cover_image_large,
    m.is_adult, m.popularity, m.favourites
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
        popularity=row["popularity"],
        favourites=row["favourites"],
    )
