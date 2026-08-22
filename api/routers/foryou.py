"""Personal discovery feed: blends a random sample of titles from the user's
cached list (AniList or MAL username, or a synced tracker exclusion list
such as Kitsu or Yamtrack) into one multi-seed recommendation, always
excluding everything already on the list. Each request samples fresh seeds,
so the feed changes on every visit; sampling is weighted by the user's own
ratings (loved titles anchor the feed more often than ones merely logged)
unless use_ratings=false requests uniform sampling.

Seeds are restricted to the dominant embedding version so the feed keeps
working mid-way through a re-embed migration.
"""

from fastapi import APIRouter, HTTPException, Query

from api.config import embed_model_id
from api.db import get_pool
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import RecommendResponse
from api.routers.recommend import _recommend_for_seeds
from api.scoring import Weights

router = APIRouter()

# Ties broken toward the configured model: after a completed re-embed both
# versions cover the whole catalog until the old rows are pruned, and the
# feed must land on the same version the recommender prefers.
DOMINANT_MODEL_SQL = """
    SELECT embed_model FROM embeddings
    GROUP BY embed_model
    ORDER BY count(*) DESC, (embed_model = %(preferred)s) DESC, embed_model
    LIMIT 1
"""


def weighted_order(pct_col: str) -> str:
    """Rating-weighted sampling without replacement (Efraimidis-Spirakis
    A-Res): each row gets key u^(1/w) for u = random(), and the top-n keys
    are the sample. The weight comes from the entry's PERCENTILE among the
    user's own rated entries, not the absolute score: most people compress
    their ratings into a narrow band (say 70-90), and within one list only
    the ordering matters, so percentile gives every user the same effective
    spread regardless of rating habits. The percentile maps to an effective
    score of 40-100 (w = (eff/100)^2): the floor keeps a bottom-percentile
    title sampleable at 0.16 vs 1.0 for the top, and protects users who
    sincerely rate everything high from a manufactured spread. Unrated
    entries (NULL percentile) sit mid-range at 70.
    """
    return f"ORDER BY POWER(random(), 10000.0 / POWER(40 + 60 * COALESCE({pct_col}, 0.5), 2)) DESC"


ANILIST_SEEDS_SQL = """
    WITH list AS (
        SELECT al.media_id, al.score
        FROM anilist_list_entries al
        WHERE al.username = %(username)s
    ), ranked AS (
        SELECT media_id, PERCENT_RANK() OVER (ORDER BY score) AS pct
        FROM list WHERE score IS NOT NULL
    )
    SELECT m.id AS id
    FROM list l
    LEFT JOIN ranked r ON r.media_id = l.media_id
    JOIN media m ON m.id = l.media_id
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.medium = ANY(%(mediums)s)
    {order}
    LIMIT %(n)s
"""

MAL_SEEDS_SQL = """
    WITH list AS (
        SELECT l.id_mal, l.list_type, l.score
        FROM mal_list_entries l
        WHERE l.username = %(username)s
    ), ranked AS (
        SELECT id_mal, list_type, PERCENT_RANK() OVER (ORDER BY score) AS pct
        FROM list WHERE score IS NOT NULL
    )
    SELECT m.id AS id
    FROM list l
    LEFT JOIN ranked r ON r.id_mal = l.id_mal AND r.list_type = l.list_type
    JOIN media m ON m.id_mal = l.id_mal AND l.list_type = lower(m.media_type)
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.medium = ANY(%(mediums)s)
    {order}
    LIMIT %(n)s
"""

# Seeds from generic exclusion lists (Kitsu, Yamtrack, hand-posted): the same
# id-mapping join the exclusion filter uses. Lets For-you work for users who
# only sync a tracker list, without an AniList or MAL username.
LIST_SEEDS_SQL = """
    WITH list AS (
        SELECT ce.kind, ce.ext_id, ce.score
        FROM custom_exclusion_entries ce
        WHERE ce.list_name = ANY(%(lists)s)
    ), ranked AS (
        SELECT kind, ext_id, PERCENT_RANK() OVER (ORDER BY score) AS pct
        FROM list WHERE score IS NOT NULL
    )
    SELECT m.id AS id
    FROM list l
    LEFT JOIN ranked r ON r.kind = l.kind AND r.ext_id = l.ext_id
    JOIN media m ON ((l.kind = 'anilist' AND m.id = l.ext_id)
        OR (l.kind = 'mal_anime' AND m.media_type = 'ANIME' AND m.id_mal = l.ext_id)
        OR (l.kind = 'mal_manga' AND m.media_type = 'MANGA' AND m.id_mal = l.ext_id))
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.medium = ANY(%(mediums)s)
    {order}
    LIMIT %(n)s
"""


@router.get("/foryou", response_model=RecommendResponse)
def foryou(
    medium: MediumGroup,
    anilist_user: str | None = None,
    mal_user: str | None = None,
    seeds: int = Query(8, ge=1, le=20),
    w_semantic: float = Query(0.5, ge=0),
    w_tags: float = Query(0.3, ge=0),
    w_genres: float = Query(0.2, ge=0),
    exclude_franchise: bool = True,
    limit: int = Query(50, ge=1, le=200),
    adult: bool = False,
    formats: list[str] | None = Query(None, alias="format"),
    year_min: int | None = None,
    year_max: int | None = None,
    min_score: int | None = None,
    countries: list[str] | None = Query(None, alias="country"),
    statuses: list[str] | None = Query(None, alias="status"),
    genres_include: list[str] | None = Query(None, alias="genre_in"),
    genres_exclude: list[str] | None = Query(None, alias="genre_ex"),
    tags_include: list[str] | None = Query(None, alias="tag_in"),
    tags_exclude: list[str] | None = Query(None, alias="tag_ex"),
    max_popularity: int | None = Query(None, ge=0),
    max_episodes: int | None = Query(None, ge=1),
    max_chapters: int | None = Query(None, ge=1),
    exclude_list: list[str] | None = Query(None),
    keep_planned: bool = False,
    use_ratings: bool = True,
) -> RecommendResponse:
    list_names = [n.strip().lower() for n in (exclude_list or []) if n.strip()]
    if not anilist_user and not mal_user and not list_names:
        raise HTTPException(
            status_code=422,
            detail="provide anilist_user, mal_user, or exclude_list"
            " (a synced tracker list can seed the feed)",
        )

    if anilist_user:
        template = ANILIST_SEEDS_SQL
    elif mal_user:
        template = MAL_SEEDS_SQL
    else:
        template = LIST_SEEDS_SQL
    # use_ratings=false reverts to uniform sampling: every list entry has
    # the same chance of anchoring the feed, ratings ignored entirely.
    order = weighted_order("r.pct") if use_ratings else "ORDER BY random()"
    seeds_sql = template.format(order=order)
    username = (anilist_user or mal_user or "").strip().lower()
    mediums = MEDIUM_GROUPS[medium]

    with get_pool().connection() as conn:
        model_row = conn.execute(DOMINANT_MODEL_SQL, {"preferred": embed_model_id()}).fetchone()
        if model_row is None:
            raise HTTPException(status_code=409, detail="no embeddings yet; run pipeline.embed")
        params = {
            "model": model_row["embed_model"],
            "username": username,
            "lists": list_names,
            "mediums": mediums,
            "n": seeds,
        }
        rows = conn.execute(seeds_sql, params).fetchall()
        if not rows:
            # Nothing from the list in this medium; sample across all mediums.
            rows = conn.execute(seeds_sql, {**params, "mediums": ALL_MEDIUMS}).fetchall()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="no titles from your cached list are embedded in the catalog;"
            " refresh your list first",
        )

    seed_ids = list(dict.fromkeys(row["id"] for row in rows))
    return _recommend_for_seeds(
        seed_ids,
        weights=Weights(w_semantic, w_tags, w_genres).normalized(),
        cross_media=False,
        exclude_franchise=exclude_franchise,
        limit=limit,
        filter_kwargs={
            "adult": adult,
            "formats": formats,
            "year_min": year_min,
            "year_max": year_max,
            "min_score": min_score,
            "countries": countries,
            "statuses": statuses,
            "genres_include": genres_include,
            "genres_exclude": genres_exclude,
            "tags_include": tags_include,
            "tags_exclude": tags_exclude,
            "max_popularity": max_popularity,
            "max_episodes": max_episodes,
            "max_chapters": max_chapters,
            "mal_user": mal_user,
            "anilist_user": anilist_user,
            "exclude_lists": exclude_list,
            "keep_planned": keep_planned,
        },
    )
