"""Title search within a medium group (trigram-ranked substring match), plus
the tag vocabulary used by the tag filter autocomplete."""

import time

from fastapi import APIRouter, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import SearchResponse, TagsResponse

router = APIRouter()

# All searchable title text for one row, so multi-word queries can match with
# words in any order and across title variants ("titan attack" finds Attack
# on Titan). Not indexable, but a filtered scan over the catalog stays well
# inside interactive latency for a single-user instance.
HAYSTACK_SQL = (
    "(coalesce(m.title_romaji, '') || ' ' || coalesce(m.title_english, '') || ' ' ||"
    " coalesce(m.title_native, '') || ' ' || array_to_string(m.synonyms, ' '))"
)

MAX_QUERY_WORDS = 8


def word_filter(q: str) -> tuple[str, dict[str, str]]:
    """AND-of-ILIKE clauses requiring every query word to appear somewhere in
    the row's combined title text, in any order."""
    words = [w for w in q.split() if w][:MAX_QUERY_WORDS]
    if not words:
        return "TRUE", {}
    clauses = []
    params: dict[str, str] = {}
    for i, word in enumerate(words):
        key = f"word_{i}"
        clauses.append(f"{HAYSTACK_SQL} ILIKE %({key})s")
        params[key] = f"%{word}%"
    return " AND ".join(clauses), params


SEARCH_SQL_TEMPLATE = """
    SELECT {cols},
           GREATEST(
               word_similarity(%(q)s, m.title_romaji),
               word_similarity(%(q)s, coalesce(m.title_english, '')),
               word_similarity(%(q)s, coalesce(m.title_native, '')),
               (SELECT max(word_similarity(%(q)s, syn)) FROM unnest(m.synonyms) syn)
           ) AS match_rank
    FROM media m
    WHERE m.medium = ANY(%(mediums)s)
      AND ({word_sql})
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
    word_sql, word_params = word_filter(q)
    sql = SEARCH_SQL_TEMPLATE.format(cols=MEDIA_COLS, word_sql=word_sql)
    with get_pool().connection() as conn:
        rows = conn.execute(
            sql,
            {
                "q": q,
                "mediums": mediums,
                "adult": adult,
                "limit": limit,
                **word_params,
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
