"""Backfill progress for the English dub signal.

The dubbed filter is a positive one: it keeps rows where AniList credits an
English voice actor. In SQL `english_va_count > 0` is NULL for a row that has
not been checked yet, so unchecked titles are excluded while the filter is on.
That is the right conservative behaviour, but it means the filter is only as
complete as the backfill, and the backfill is one AniList request per title
(hours for a full anime catalog). This endpoint exists so a client can say so
instead of silently showing a thin result set.
"""

from fastapi import APIRouter

from api.db import get_pool
from api.models import DubCoverageOut

router = APIRouter()

COVERAGE_SQL = """
    SELECT
        count(*)                                           AS total,
        count(english_va_count)                            AS checked,
        count(*) FILTER (WHERE english_va_count > 0)       AS dubbed,
        count(*) FILTER (WHERE cast_character_count = 0)   AS without_cast
    FROM media
    WHERE media_type = 'ANIME'
"""


@router.get("/dub/coverage", response_model=DubCoverageOut)
def dub_coverage() -> DubCoverageOut:
    with get_pool().connection() as conn:
        row = conn.execute(COVERAGE_SQL).fetchone()
    total = row["total"] or 0
    checked = row["checked"] or 0
    return DubCoverageOut(
        total=total,
        checked=checked,
        dubbed=row["dubbed"] or 0,
        without_cast=row["without_cast"] or 0,
        complete=total > 0 and checked >= total,
    )
