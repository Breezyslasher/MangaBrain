"""Random discovery: a surprise title that passes the active filters."""

from fastapi import APIRouter, HTTPException, Query

from api.db import MEDIA_COLS, get_pool, media_from_row
from api.filters import build_filters
from api.media_groups import MEDIUM_GROUPS, MediumGroup
from api.models import MediaOut

router = APIRouter()


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
    mal_user: str | None = None,
) -> MediaOut:
    filter_sql, filter_params = build_filters(
        adult=adult,
        formats=formats,
        year_min=year_min,
        year_max=year_max,
        min_score=min_score,
        countries=countries,
        statuses=statuses,
        mal_user=mal_user,
    )
    sql = f"""
        SELECT {MEDIA_COLS}
        FROM media m
        WHERE m.medium = ANY(%(mediums)s)
          {filter_sql}
        ORDER BY random()
        LIMIT 1
    """
    with get_pool().connection() as conn:
        row = conn.execute(sql, {"mediums": MEDIUM_GROUPS[medium], **filter_params}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no title matches the active filters")
    return media_from_row(row)
