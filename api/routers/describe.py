"""Search the catalog by describing what you want, in prose.

Every title is already embedded; a typed sentence embedded into the same
space ranks against them directly. The engine's other entry points all
start from a title the user has already found, and this one does not,
which is the whole reason it exists.

What this is not: a replacement for /search. That matches names. This
matches content, and it can only match what synopses actually say - plot,
premise, setting, relationships. Narration, art style and pacing are not
in the text, so they are not findable here.

A comma-separated query is treated as facets and fused by rank (see
pipeline.describe). Prose stays one vector, and is the better input.
"""

from fastapi import APIRouter, HTTPException, Query

from api.db import get_pool, media_from_row
from api.embedding import encode_queries
from api.filters import build_filters
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import DescribeItem, DescribeResponse
from pipeline.describe import MAX_FACETS, rank, split_facets

router = APIRouter()


@router.get("/describe", response_model=DescribeResponse)
def describe(
    q: str = Query(..., min_length=3, max_length=500),
    medium: MediumGroup | None = None,
    limit: int = Query(30, ge=1, le=100),
    # Every facet is an independent ANN scan, so this is the one knob that
    # multiplies the cost of a request rather than adding to it.
    adult: bool = False,
    formats: list[str] | None = Query(None, alias="format"),
    year_min: int | None = Query(None, ge=1900, le=2200),
    year_max: int | None = Query(None, ge=1900, le=2200),
    min_score: int | None = Query(None, ge=0, le=100),
    countries: list[str] | None = Query(None, alias="country"),
    statuses: list[str] | None = Query(None, alias="status"),
    genres_include: list[str] | None = Query(None, alias="genre_in"),
    genres_exclude: list[str] | None = Query(None, alias="genre_ex"),
    tags_include: list[str] | None = Query(None, alias="tag_in"),
    tags_exclude: list[str] | None = Query(None, alias="tag_ex"),
    max_popularity: int | None = Query(None, ge=0),
    max_episodes: int | None = Query(None, ge=1),
    max_chapters: int | None = Query(None, ge=1),
    min_episodes: int | None = Query(None, ge=1),
    min_chapters: int | None = Query(None, ge=1),
    dubbed: bool = False,
    dub_lang: str | None = Query(None),
    mal_user: str | None = Query(None),
    anilist_user: str | None = Query(None),
    exclude_list: list[str] | None = Query(None),
    keep_planned: bool = False,
    # Titles with no synopsis are embedded from their own name, so they
    # match a description by coincidence of wording while saying nothing
    # about their content. Off by default here; the recommender keeps them,
    # where a seed's genres and tags still carry real signal.
    include_title_only: bool = False,
) -> DescribeResponse:
    facets = split_facets(q)
    if len(facets) > MAX_FACETS:
        raise HTTPException(
            status_code=422,
            detail=f"at most {MAX_FACETS} comma-separated facets;"
            " a sentence works better than a long list",
        )
    vectors = encode_queries(facets)
    filter_sql, filter_params = build_filters(
        adult=adult,
        formats=formats,
        year_min=year_min,
        year_max=year_max,
        min_score=min_score,
        countries=countries,
        statuses=statuses,
        genres_include=genres_include,
        genres_exclude=genres_exclude,
        tags_include=tags_include,
        tags_exclude=tags_exclude,
        max_popularity=max_popularity,
        max_episodes=max_episodes,
        max_chapters=max_chapters,
        min_episodes=min_episodes,
        min_chapters=min_chapters,
        dubbed=dubbed,
        dub_lang=dub_lang,
        mal_user=mal_user,
        anilist_user=anilist_user,
        exclude_lists=exclude_list,
        keep_planned=keep_planned,
    )
    with get_pool().connection() as conn:
        rows = rank(
            conn,
            vectors,
            mediums=MEDIUM_GROUPS[medium] if medium else ALL_MEDIUMS,
            limit=limit,
            filter_sql=filter_sql,
            filter_params=filter_params,
            require_synopsis=not include_title_only,
        )
    return DescribeResponse(
        facets=facets,
        results=[
            DescribeItem(
                media=media_from_row(row),
                similarity=row.get("similarity"),
                facets_hit=row["facets_hit"],
                facet_count=row["facet_count"],
            )
            for row in rows
        ],
    )
