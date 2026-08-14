"""Title search within a medium group (trigram-ranked substring match), plus
the tag vocabulary used by the tag filter autocomplete."""

import time

from fastapi import APIRouter, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import SearchResponse, TagsResponse

router = APIRouter()

SEARCH_SQL = f"""
    SELECT {MEDIA_COLS},
           GREATEST(
               word_similarity(%(q)s, m.title_romaji),
               word_similarity(%(q)s, coalesce(m.title_english, '')),
               word_similarity(%(q)s, coalesce(m.title_native, '')),
               (SELECT max(word_similarity(%(q)s, syn)) FROM unnest(m.synonyms) syn)
           ) AS match_rank
    FROM media m
    WHERE m.medium = ANY(%(mediums)s)
      AND (m.title_romaji ILIKE %(pattern)s
           OR m.title_english ILIKE %(pattern)s
           OR m.title_native ILIKE %(pattern)s
           OR EXISTS (SELECT 1 FROM unnest(m.synonyms) syn WHERE syn ILIKE %(pattern)s))
      AND (%(adult)s OR m.is_adult = FALSE)
    ORDER BY match_rank DESC, m.id
    LIMIT %(limit)s
"""

TAGS_SQL = """
    SELECT DISTINCT t->>'name' AS name
    FROM media m, jsonb_array_elements(m.tags) t
    WHERE t->>'name' IS NOT NULL
    ORDER BY name
"""

_TAGS_TTL_SECONDS = 24 * 3600
_tags_cache: tuple[float, list[str]] | None = None


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    medium: MediumGroup | None = None,
    adult: bool = False,
    limit: int = Query(20, ge=1, le=100),
) -> SearchResponse:
    mediums = MEDIUM_GROUPS[medium] if medium else ALL_MEDIUMS
    with get_pool().connection() as conn:
        rows = conn.execute(
            SEARCH_SQL,
            {
                "q": q,
                "pattern": f"%{q}%",
                "mediums": mediums,
                "adult": adult,
                "limit": limit,
            },
        ).fetchall()
    return SearchResponse(results=[media_from_row(row) for row in rows])


@router.get("/tags", response_model=TagsResponse)
def tags() -> TagsResponse:
    """The distinct tag vocabulary (~800 names), cached in-process: the scan
    over every row's tags is too heavy to run per keystroke."""
    global _tags_cache
    now = time.monotonic()
    if _tags_cache is None or now - _tags_cache[0] > _TAGS_TTL_SECONDS:
        with get_pool().connection() as conn:
            names = [row["name"] for row in conn.execute(TAGS_SQL).fetchall()]
        _tags_cache = (now, names)
    return TagsResponse(tags=_tags_cache[1])
