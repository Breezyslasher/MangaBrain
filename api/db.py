"""Connection pool and row helpers shared by all routers."""

from typing import Any

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.config import settings
from api.models import MediaOut

_pool: ConnectionPool | None = None


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
