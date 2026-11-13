"""Random discovery: a surprise title that passes the active filters."""

from fastapi import APIRouter, HTTPException, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.filters import build_filters
from api.media_groups import MEDIUM_GROUPS, MediumGroup
from api.models import MediaOut

router = APIRouter()

# A random title used to be ORDER BY random() over every row of the medium,
# full rows included: 1.7 s for the 1.1 million books on a fast machine, far
# longer on a NAS. Picking from a random sample of the table's pages first
# reads a few percent of it. Pages are chosen at random and hold similar
# numbers of rows, so every title stays about equally likely. When the
# filters are narrow enough that the sample holds no match, the full pick
# runs, as before.
SAMPLE_PERCENT = 2


@router.get("/random", response_model=MediaOut)
def random_pick(
    medium: MediumGroup,
    adult: bool = False,
    formats: list[str] | None = Query(None, alias="format"),
    year_min: int | None = None,
    year_max: int | None = None,
    min_score: int | None = None,
    countries: list[str] | None = Query(None, alias="country"),
    statuses: list[str] | None = Query(None, alias="status"),
    genres_include: list[str] | None = Query(None, alias="genre_in"),
    genres_exclude: list[str] | None = Query(None, alias="genre_ex"),
    max_popularity: int | None = Query(None, ge=0),
    max_episodes: int | None = Query(None, ge=1),
    max_chapters: int | None = Query(None, ge=1),
    min_episodes: int | None = Query(None, ge=1),
    min_chapters: int | None = Query(None, ge=1),
    dubbed: bool = False,
    dub_lang: str | None = None,
    mal_user: str | None = None,
    anilist_user: str | None = None,
    exclude_list: list[str] | None = Query(None),
    keep_planned: bool = False,
) -> MediaOut:
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
    template = """
        SELECT {cols}
        FROM media m {sample}
        WHERE m.medium = ANY(%(mediums)s)
          {filters}
        ORDER BY random()
        LIMIT 1
    """
    params = {"mediums": MEDIUM_GROUPS[medium], "sample": SAMPLE_PERCENT, **filter_params}
    sampled = template.format(
        cols=MEDIA_COLS, sample="TABLESAMPLE SYSTEM (%(sample)s)", filters=filter_sql
    )
    full = template.format(cols=MEDIA_COLS, sample="", filters=filter_sql)
    with get_pool().connection() as conn:
        row = conn.execute(sampled, params).fetchone()
        if row is None:
            params.pop("sample")
            row = conn.execute(full, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no title matches the active filters")
    return media_from_row(row)
