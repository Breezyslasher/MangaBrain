"""Personal discovery feed: blends a random sample of titles from the user's
cached AniList or MAL list into one multi-seed recommendation, always
excluding everything already on the list. Each request samples fresh seeds,
so the feed changes on every visit.

Seeds are restricted to the dominant embedding version so the feed keeps
working mid-way through a re-embed migration.
"""

from fastapi import APIRouter, HTTPException, Query

from api.db import get_pool
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, MediumGroup
from api.models import RecommendResponse
from api.routers.recommend import _recommend_for_seeds
from api.scoring import Weights

router = APIRouter()

DOMINANT_MODEL_SQL = """
    SELECT embed_model FROM embeddings GROUP BY embed_model ORDER BY count(*) DESC LIMIT 1
"""

ANILIST_SEEDS_SQL = """
    SELECT m.id AS id
    FROM anilist_list_entries al
    JOIN media m ON m.id = al.media_id
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE al.username = %(username)s AND m.medium = ANY(%(mediums)s)
    ORDER BY random()
    LIMIT %(n)s
"""

MAL_SEEDS_SQL = """
    SELECT m.id AS id
    FROM mal_list_entries l
    JOIN media m ON m.id_mal = l.id_mal AND l.list_type = lower(m.media_type)
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE l.username = %(username)s AND m.medium = ANY(%(mediums)s)
    ORDER BY random()
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
) -> RecommendResponse:
    if not anilist_user and not mal_user:
        raise HTTPException(status_code=422, detail="provide anilist_user or mal_user")

    seeds_sql = ANILIST_SEEDS_SQL if anilist_user else MAL_SEEDS_SQL
    username = (anilist_user or mal_user or "").strip().lower()
    mediums = MEDIUM_GROUPS[medium]

    with get_pool().connection() as conn:
        model_row = conn.execute(DOMINANT_MODEL_SQL).fetchone()
        if model_row is None:
            raise HTTPException(status_code=409, detail="no embeddings yet; run pipeline.embed")
        params = {
            "model": model_row["embed_model"],
            "username": username,
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
        },
    )
