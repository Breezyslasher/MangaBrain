"""Backfill progress for the English dub signal.

The dubbed filter is a positive one: it keeps rows where AniList credits an
English voice actor. In SQL `english_va_count > 0` is NULL for a row that has
not been checked yet, so unchecked titles are excluded while the filter is on.
That is the right conservative behaviour, but it means the filter is only as
complete as the backfill. This endpoint exists so a client can say so instead
of silently showing a thin result set.

"Complete" cannot mean "every row has a value". A title deleted upstream, or
reclassified out of ANIME, is recorded as a permanent failure and keeps a NULL
english_va_count for good, so a flag defined as checked >= total is held false
forever by one such row - observed at 22,666 of 22,667, one short, with the
walk genuinely finished. Rows whose retries are exhausted are therefore
counted as accounted for rather than pending, and reported separately so the
number is not simply hidden.
"""

from fastapi import APIRouter

from api.db import get_pool
from api.models import DubCoverageOut
from pipeline.backfill_dub import MAX_ATTEMPTS

router = APIRouter()

COVERAGE_SQL = """
    SELECT
        count(*)                                            AS total,
        count(m.english_va_count)                           AS checked,
        count(*) FILTER (WHERE m.english_va_count > 0)      AS dubbed,
        count(*) FILTER (WHERE m.cast_character_count = 0)  AS without_cast,
        count(*) FILTER (
            WHERE m.english_va_count IS NULL
              AND COALESCE(s.attempts, 0) >= %(max_attempts)s
        )                                                   AS unreachable
    FROM media m
    LEFT JOIN dub_backfill_state s ON s.media_id = m.id
    WHERE m.media_type = 'ANIME'
"""


@router.get("/dub/coverage", response_model=DubCoverageOut)
def dub_coverage() -> DubCoverageOut:
    with get_pool().connection() as conn:
        row = conn.execute(COVERAGE_SQL, {"max_attempts": MAX_ATTEMPTS}).fetchone()
    total = row["total"] or 0
    checked = row["checked"] or 0
    unreachable = row["unreachable"] or 0
    return DubCoverageOut(
        total=total,
        checked=checked,
        dubbed=row["dubbed"] or 0,
        without_cast=row["without_cast"] or 0,
        unreachable=unreachable,
        # A row AniList will never answer for is settled, not outstanding.
        complete=total > 0 and checked + unreachable >= total,
    )
