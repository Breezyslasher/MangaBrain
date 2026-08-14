"""Title search within a medium group (trigram-ranked substring match)."""

from fastapi import APIRouter, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import SearchResponse

router = APIRouter()

SEARCH_SQL = f"""
    SELECT {MEDIA_COLS},
           GREATEST(
               word_similarity(%(q)s, m.title_romaji),
               word_similarity(%(q)s, coalesce(m.title_english, '')),
               word_similarity(%(q)s, coalesce(m.title_native, ''))
           ) AS match_rank
    FROM media m
    WHERE m.medium = ANY(%(mediums)s)
      AND (m.title_romaji ILIKE %(pattern)s
           OR m.title_english ILIKE %(pattern)s
           OR m.title_native ILIKE %(pattern)s)
      AND (%(adult)s OR m.is_adult = FALSE)
    ORDER BY match_rank DESC, m.id
    LIMIT %(limit)s
"""


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
