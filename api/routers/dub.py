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

from fastapi import APIRouter, Query

from api.db import get_pool
from api.models import DubCoverageOut, DubLanguageOut, DubLanguagesOut
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


# Every source, unioned, counted by distinct title. They differ in kind -
# measured_dub_languages is AniList answering for the id it was given,
# dub_languages is ANN matched on title, mal_dub_languages is the MAL-derived
# cast API answering on an id_mal join - but a language is offerable if any of
# them knows about it, which is the same rule api/filters.py applies.
#
# That "same rule" is the point. This query and the filter have to read the
# same columns or the picker and the results disagree: when mal_dub_languages
# was missing here, the filter already matched it, so a language could be
# filterable and unofferable at once, and the counts next to the ones that did
# appear were short by whatever that column held.
LANGUAGES_SQL = """
    SELECT lang, count(DISTINCT id) AS titles
    FROM (
        SELECT id, unnest(dub_languages) AS lang
        FROM media WHERE media_type = 'ANIME'
        UNION ALL
        SELECT id, unnest(measured_dub_languages) AS lang
        FROM media WHERE media_type = 'ANIME'
        UNION ALL
        SELECT id, unnest(mal_dub_languages) AS lang
        FROM media WHERE media_type = 'ANIME'
    ) t
    WHERE lang <> ''
    GROUP BY lang
    HAVING count(DISTINCT id) >= %(min_titles)s
    ORDER BY titles DESC, lang
"""


@router.get("/dub/languages", response_model=DubLanguagesOut)
def dub_languages(
    min_titles: int = Query(
        10,
        ge=1,
        le=10000,
        description="omit languages with fewer titles than this",
    ),
) -> DubLanguagesOut:
    """Which dub languages the catalog actually has, and how many titles each.

    The UI picker offered nine hardcoded languages while the database held
    29, so no, th, he, id, zh, hi, fi, tr, ca and vi existed and could not be
    selected. A hardcoded list drifts the moment coverage changes, and it
    already had: the list was written when ANN was the only source and
    AniList had been asked about English alone.

    A floor rather than everything, because a language with one title is
    noise in a dropdown - vi and zh-tw each have exactly one. The caller
    chooses it, so a client that wants the full tail can ask for it.
    """
    with get_pool().connection() as conn:
        rows = conn.execute(LANGUAGES_SQL, {"min_titles": min_titles}).fetchall()
    return DubLanguagesOut(
        languages=[DubLanguageOut(code=row["lang"], titles=row["titles"] or 0) for row in rows]
    )
