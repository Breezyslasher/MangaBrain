"""Catalog search and browse within a medium group, the tag vocabulary used
by the tag filter autocomplete, and the two exact-match lookups a library
client needs to map its own records onto catalog ids.

/search started as a seed picker: it took a query, a medium and the adult
toggle, and nothing else. The filter panel reached /recommend, /random and
/foryou but not here, so the catalog could not be queried on its own terms -
you had to pick a title first and ask what resembles it. build_filters has
said "for recommend, random, and search" in its docstring the whole time;
search simply never called it.

It now does, and `q` is optional, which turns the same endpoint into browse:
filters with no query list everything that matches.

Ordering deliberately avoids popularity. With a query it is match rank; with
none it is newest first, which is deterministic and says nothing about how
many people watched a title. Sorting a browse list by member count would put
popularity back into the one place this project keeps it out of.
"""

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.filters import build_filters
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

# Punctuation dropped from both the titles and the query before matching, so
# a phone keyboard's curly apostrophe finds a title stored with a straight
# one ("Dog’s" finds "Dog's"), and "dogs", "jojos" or "spiderman" find
# "Dog's", "JoJo's" and "Spider-Man". Removed, not turned into spaces: words
# match as substrings, so "spider man" still finds "spiderman".
IGNORED_PUNCTUATION = "'\u2019\u2018\u02bc`\u00b4.,:;!?\"\u201c\u201d-\u2010\u2013\u2014()/"
_STRIP = str.maketrans("", "", IGNORED_PUNCTUATION)

# Once per row, not once per query word.
NORMALISED_SQL = (
    f"CROSS JOIN LATERAL (SELECT translate({HAYSTACK_SQL}, "
    + "'"
    + IGNORED_PUNCTUATION.replace("'", "''")
    + "', '') AS hay) h"
)

MAX_QUERY_WORDS = 8


def normalise(text: str) -> str:
    return text.translate(_STRIP)


def word_filter(q: str) -> tuple[str, dict[str, str]]:
    """AND-of-ILIKE clauses requiring every query word to appear somewhere in
    the row's combined title text, in any order, punctuation ignored."""
    words = [w for w in (normalise(w) for w in q.split()) if w][:MAX_QUERY_WORDS]
    if not words:
        return "TRUE", {}
    clauses = []
    params: dict[str, str] = {}
    for i, word in enumerate(words):
        key = f"word_{i}"
        clauses.append(f"h.hay ILIKE %({key})s")
        params[key] = f"%{word}%"
    return " AND ".join(clauses), params


# One template for both modes. The rank expression and the ordering swap out
# together: word_similarity against an absent query would rank everything
# equally, so a browse listing orders by recency instead.
SEARCH_SQL_TEMPLATE = """
    SELECT {cols},
           {rank_sql} AS match_rank
    FROM media m
    {normalised_sql}
    WHERE m.medium = ANY(%(mediums)s)
      AND ({word_sql}){filter_sql}
    ORDER BY {order_sql}
    LIMIT %(limit)s OFFSET %(offset)s
"""

RANK_SQL = """GREATEST(
               word_similarity(%(q)s, m.title_romaji),
               word_similarity(%(q)s, coalesce(m.title_english, '')),
               word_similarity(%(q)s, coalesce(m.title_native, '')),
               (SELECT max(word_similarity(%(q)s, syn)) FROM unnest(m.synonyms) syn)
           )"""

# Newest first, then id, so paging is stable. Not average score and above all
# not popularity: the project bars popularity from ranking, and a browse list
# ordered by member count would reintroduce it where it is least visible.
BROWSE_ORDER = "m.start_year DESC NULLS LAST, m.id DESC"

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
    # Optional, which is what makes this browse as well as search: filters
    # with no query list the catalog rather than requiring a seed title.
    q: str | None = Query(None, min_length=1),
    medium: MediumGroup | None = None,
    adult: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    # The same filter surface the recommender has. These reached /recommend,
    # /random and /foryou but never here, so the catalog could not be queried
    # on its own terms.
    format: list[str] | None = Query(None),
    year_min: int | None = Query(None, ge=1900, le=2200),
    year_max: int | None = Query(None, ge=1900, le=2200),
    min_score: int | None = Query(None, ge=0, le=100),
    country: list[str] | None = Query(None),
    status: list[str] | None = Query(None),
    genre_in: list[str] | None = Query(None),
    genre_ex: list[str] | None = Query(None),
    tag_in: list[str] | None = Query(None),
    tag_ex: list[str] | None = Query(None),
    max_popularity: int | None = Query(None, ge=0),
    max_episodes: int | None = Query(None, ge=1),
    max_chapters: int | None = Query(None, ge=1),
    min_episodes: int | None = Query(None, ge=1),
    min_chapters: int | None = Query(None, ge=1),
    dubbed: bool = False,
    dub_lang: str | None = Query(None),
    mal_user: str | None = Query(None),
    anilist_user: str | None = Query(None),
    exclude_lists: list[str] | None = Query(None),
    keep_planned: bool = False,
) -> SearchResponse:
    mediums = MEDIUM_GROUPS[medium] if medium else ALL_MEDIUMS
    word_sql, word_params = word_filter(q or "")
    filter_sql, filter_params = build_filters(
        adult=adult,
        formats=format,
        year_min=year_min,
        year_max=year_max,
        min_score=min_score,
        countries=country,
        statuses=status,
        genres_include=genre_in,
        genres_exclude=genre_ex,
        tags_include=tag_in,
        tags_exclude=tag_ex,
        max_popularity=max_popularity,
        max_episodes=max_episodes,
        max_chapters=max_chapters,
        min_episodes=min_episodes,
        min_chapters=min_chapters,
        dubbed=dubbed,
        dub_lang=dub_lang,
        mal_user=mal_user,
        anilist_user=anilist_user,
        exclude_lists=exclude_lists,
        keep_planned=keep_planned,
    )
    sql = SEARCH_SQL_TEMPLATE.format(
        cols=MEDIA_COLS,
        rank_sql=RANK_SQL if q else "0",
        word_sql=word_sql,
        normalised_sql=NORMALISED_SQL if word_params else "",
        filter_sql=filter_sql,
        order_sql="match_rank DESC, m.id" if q else BROWSE_ORDER,
    )
    params: dict = {
        "mediums": mediums,
        "limit": limit,
        "offset": offset,
        **word_params,
        **filter_params,
    }
    # Only bound when the rank expression is present; psycopg rejects a
    # parameter the statement does not use.
    if q:
        params["q"] = q
    with get_pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
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
