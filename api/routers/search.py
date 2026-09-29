"""Title search within a medium group (trigram-ranked substring match), the
tag vocabulary used by the tag filter autocomplete, and the two exact-match
lookups a library client needs to map its own records onto catalog ids."""

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import (
    MediaOut,
    ResolveMatch,
    ResolveRequest,
    ResolveResponse,
    SearchResponse,
    TagsResponse,
)

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


# Lowercase, then drop ASCII punctuation and whitespace while keeping every
# non-ASCII character. [:alnum:] is locale-dependent, so the range is
# explicit: "Kaguya-sama: Love is War" equals "Kaguya-sama - Love Is War",
# and Japanese, Korean and Chinese titles still compare exactly.
NORMALIZE = r"regexp_replace(lower({0}), '[^a-z0-9\u0080-\U0010ffff]+', '', 'g')"

# Ordinality 1-3 are the main titles (romaji, english, native); anything
# past them came from the synonyms array.
MAIN_TITLE_ORDS = 3

# Titles beyond this per item are ignored rather than rejected: a client
# sending a long alias list still gets its best-known names resolved.
MAX_RESOLVE_TITLES = 8

RESOLVE_SQL = f"""
    WITH wanted AS (
        SELECT DISTINCT raw, {NORMALIZE.format("raw")} AS norm
        FROM unnest(%(titles)s::text[]) AS t(raw)
    ), candidates AS (
        SELECT m.id, v.ord, {NORMALIZE.format("v.title")} AS norm
        FROM media m
        CROSS JOIN LATERAL unnest(
            ARRAY[m.title_romaji, m.title_english, m.title_native] || m.synonyms
        ) WITH ORDINALITY AS v(title, ord)
        WHERE m.media_type = 'MANGA'
          AND (%(adult)s OR m.is_adult = FALSE)
          AND v.title IS NOT NULL
    )
    SELECT w.raw, c.id, min(c.ord) AS best_ord
    FROM wanted w
    JOIN candidates c ON c.norm = w.norm
    WHERE w.norm <> ''
    GROUP BY w.raw, c.id
"""

RESOLVE_BY_MAL_SQL = """
    SELECT m.id, m.id_mal
    FROM media m
    WHERE m.media_type = 'MANGA'
      AND m.id_mal = ANY(%(mal_ids)s::int[])
      AND (%(adult)s OR m.is_adult = FALSE)
"""

BY_MAL_SQL = f"""
    SELECT {MEDIA_COLS}
    FROM media m
    WHERE m.id_mal = %(mal_id)s
      AND m.media_type = %(media_type)s
      AND (%(adult)s OR m.is_adult = FALSE)
    ORDER BY m.id
    LIMIT 1
"""


@router.post("/search/resolve", response_model=ResolveResponse)
def resolve(body: ResolveRequest) -> ResolveResponse:
    """Batch-map library titles to catalog ids, exact matches only.

    One catalog pass for the whole batch, instead of a /search per title.
    Unlike /search this never returns a best guess: a single distinct entry
    is a match, several are "ambiguous", none is "none". Manga-family only,
    so an anime never resolves for a manga library.
    """
    titles: list[str] = []
    for item in body.items:
        titles.extend(item.titles[:MAX_RESOLVE_TITLES])
    mal_ids = sorted({i.mal_id for i in body.items if i.mal_id is not None})

    by_mal: dict[int, int] = {}
    hits: dict[str, dict[int, int]] = {}
    with get_pool().connection() as conn:
        if mal_ids:
            rows = conn.execute(
                RESOLVE_BY_MAL_SQL, {"mal_ids": mal_ids, "adult": body.adult}
            ).fetchall()
            by_mal = {row["id_mal"]: row["id"] for row in rows}
        if titles:
            rows = conn.execute(RESOLVE_SQL, {"titles": titles, "adult": body.adult}).fetchall()
            for row in rows:
                hits.setdefault(row["raw"], {})[row["id"]] = row["best_ord"]

    results: dict[str, ResolveMatch] = {}
    for item in body.items:
        media_id = by_mal.get(item.mal_id) if item.mal_id is not None else None
        if media_id is not None:
            results[item.key] = ResolveMatch(id=media_id, match="mal")
            continue
        # Best (lowest) ordinality per entry across this item's titles, so a
        # main-title hit outranks a synonym hit on the same entry.
        best: dict[int, int] = {}
        for title in item.titles[:MAX_RESOLVE_TITLES]:
            for candidate_id, ord_ in (hits.get(title) or {}).items():
                if candidate_id not in best or ord_ < best[candidate_id]:
                    best[candidate_id] = ord_
        if not best:
            results[item.key] = ResolveMatch(match="none")
        elif len(best) > 1:
            results[item.key] = ResolveMatch(match="ambiguous")
        else:
            candidate_id, ord_ = next(iter(best.items()))
            results[item.key] = ResolveMatch(
                id=candidate_id,
                match="title" if ord_ <= MAIN_TITLE_ORDS else "synonym",
            )
    return ResolveResponse(results=results)


@router.get("/search/by-mal/{mal_id}", response_model=MediaOut)
def by_mal(
    mal_id: int,
    media_type: Literal["manga", "anime"] = Query("manga", alias="type"),
    adult: bool = False,
) -> MediaOut:
    """The catalog entry for a MAL id. Typed because MAL's anime and manga
    ids are separate id spaces, so the same number means two things."""
    with get_pool().connection() as conn:
        row = conn.execute(
            BY_MAL_SQL,
            {
                "mal_id": mal_id,
                "media_type": media_type.upper(),
                "adult": adult,
            },
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no {media_type} with MAL id {mal_id}")
    return media_from_row(row)
