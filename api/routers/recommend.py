"""Content-based recommendations for one or several seed titles.

Candidate retrieval is a semantic ANN query against the pgvector HNSW index
(top N within the seeds' medium groups, or all mediums when cross-media is
on), with filters applied in SQL. Candidates are then re-ranked in Python
with the full weighted score: semantic + tags + genres. Popularity never
participates in ranking. Direct adaptation/source relations are excluded
from results and returned in the separate "related" list; franchise entries
(sequels, spin-offs, transitively walked) are hidden by default.

Multi-seed requests average the seed embeddings into one profile, average
the tag maps, and union the genre sets, so results are "more like all of
these" rather than per-seed lists.
"""

from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from api.config import settings
from api.db import MEDIA_COLS, get_pool, hnsw_supports_iterative_scan, media_from_row
from api.filters import build_filters
from api.media_groups import ALL_MEDIUMS, group_for_medium
from api.models import (
    RecommendationItem,
    RecommendResponse,
    RelatedItem,
    ScoreComponents,
)
from api.scoring import (
    Weights,
    display_similarity,
    final_score,
    genre_similarity,
    merge_tag_maps,
    tag_similarity,
    tag_weight_map,
)

router = APIRouter()

ADAPTATION_RELATIONS = ("ADAPTATION", "SOURCE")

# Relation types that connect entries of the same franchise. CHARACTER and
# OTHER are deliberately absent: character cameos link across unrelated
# franchises and would bleed the traversal into the whole catalog.
FRANCHISE_RELATIONS = (
    "ADAPTATION",
    "SOURCE",
    "PREQUEL",
    "SEQUEL",
    "PARENT",
    "SIDE_STORY",
    "ALTERNATIVE",
    "SPIN_OFF",
    "SUMMARY",
    "COMPILATION",
    "CONTAINS",
)

# Franchises are chains (season 1 -> season 2 -> movie), so one hop from the
# seed misses most of them; walk the relation graph transitively. UNION
# dedups visited rows and the depth cap bounds cycles.
FRANCHISE_SQL = """
    WITH RECURSIVE franchise(id, depth) AS (
        SELECT r.related_id, 1
        FROM media_relations r
        WHERE r.media_id = ANY(%(ids)s::int[]) AND r.relation_type = ANY(%(ftypes)s)
        UNION
        SELECT r.related_id, f.depth + 1
        FROM media_relations r
        JOIN franchise f ON r.media_id = f.id
        WHERE f.depth < 6 AND r.relation_type = ANY(%(ftypes)s)
    )
    SELECT DISTINCT id FROM franchise
"""

# Candidates are pinned to the SEEDS' embedding version: mid-way through a
# re-embed migration the table holds vectors from two different embedding
# spaces, and comparing across them silently produces garbage similarities.
SEEDS_SQL = f"""
    SELECT {MEDIA_COLS}, e.embedding, e.embed_model
    FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id
    WHERE m.id = ANY(%(ids)s::int[])
"""

RELATED_SQL = f"""
    SELECT {MEDIA_COLS}, r.relation_type
    FROM media_relations r
    JOIN media m ON m.id = r.related_id
    WHERE r.media_id = ANY(%(ids)s::int[]) AND r.relation_type = ANY(%(relation_types)s)
    ORDER BY m.id
"""


def _embedding_array(value: Any) -> np.ndarray:
    """pgvector returns embeddings as np.ndarray in some versions and as a
    Vector object (with to_numpy) in others; np.asarray(dtype=float) chokes
    on the latter. Normalize both to a float32 array."""
    to_numpy = getattr(value, "to_numpy", None)
    if to_numpy is not None:
        return np.asarray(to_numpy(), dtype=np.float32)
    return np.asarray(value, dtype=np.float32)


def _recommend_for_seeds(
    seed_ids: list[int],
    *,
    weights: Weights,
    cross_media: bool,
    exclude_franchise: bool,
    limit: int,
    filter_kwargs: dict[str, Any],
) -> RecommendResponse:
    with get_pool().connection() as conn:
        rows = conn.execute(SEEDS_SQL, {"ids": seed_ids}).fetchall()
        by_id = {row["id"]: row for row in rows}
        missing = [i for i in seed_ids if i not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"unknown media id(s): {missing}")
        seeds = [by_id[i] for i in seed_ids]

        not_embedded = [s["id"] for s in seeds if s["embedding"] is None]
        if not_embedded:
            raise HTTPException(
                status_code=409,
                detail=f"media {not_embedded} have no embedding yet;"
                " run `python -m pipeline.embed`",
            )
        models = {s["embed_model"] for s in seeds}
        if len(models) > 1:
            raise HTTPException(
                status_code=409,
                detail="seeds span different embedding versions"
                " (a re-embed is in progress); try again later",
            )
        embed_model = models.pop()

        vectors = np.array([_embedding_array(s["embedding"]) for s in seeds])
        seed_vec = vectors.mean(axis=0)
        norm = np.linalg.norm(seed_vec)
        if norm > 0:
            seed_vec = seed_vec / norm

        related_rows = conn.execute(
            RELATED_SQL, {"ids": seed_ids, "relation_types": list(ADAPTATION_RELATIONS)}
        ).fetchall()
        exclude_ids = [*seed_ids, *(row["id"] for row in related_rows)]
        if exclude_franchise:
            franchise_rows = conn.execute(
                FRANCHISE_SQL, {"ids": seed_ids, "ftypes": list(FRANCHISE_RELATIONS)}
            ).fetchall()
            exclude_ids.extend(row["id"] for row in franchise_rows)

        if cross_media:
            mediums = ALL_MEDIUMS
        else:
            mediums = sorted({m for s in seeds for m in group_for_medium(s["medium"])})
        filter_sql, filter_params = build_filters(**filter_kwargs)

        # The WHERE clauses post-filter the HNSW index scan, and pgvector's
        # default ef_search (40) would cap the scan long before candidate_pool
        # rows survive. Raise ef_search to the pool size and, on pgvector
        # 0.8+, let the scan iterate until the LIMIT is satisfied.
        # relaxed_order may return neighbors slightly out of distance order,
        # which is fine: the full weighted re-rank below reorders everything.
        # set_config with is_local=true scopes both to this transaction.
        ef_search = min(max(settings.candidate_pool, 40), 1000)
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
        if hnsw_supports_iterative_scan(conn):
            conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")

        candidate_sql = f"""
            SELECT {MEDIA_COLS},
                   1 - (e.embedding <=> %(seed_vec)s) AS semantic
            FROM media m
            JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
            WHERE m.medium = ANY(%(mediums)s)
              AND m.id <> ALL(%(exclude_ids)s::int[])
              {filter_sql}
            ORDER BY e.embedding <=> %(seed_vec)s
            LIMIT %(pool_size)s
        """
        candidates = conn.execute(
            candidate_sql,
            {
                "seed_vec": seed_vec,
                "model": embed_model,
                "mediums": mediums,
                "exclude_ids": exclude_ids,
                "pool_size": settings.candidate_pool,
                **filter_params,
            },
        ).fetchall()

    seed_tags = merge_tag_maps([tag_weight_map(s["tags"]) for s in seeds])
    seed_genres = sorted({g for s in seeds for g in (s["genres"] or [])})

    scored = []
    for cand in candidates:
        semantic = min(max(float(cand["semantic"]), 0.0), 1.0)
        tag_sim = tag_similarity(seed_tags, tag_weight_map(cand["tags"]))
        genre_sim = genre_similarity(seed_genres, cand["genres"] or [])
        total = final_score(semantic, tag_sim, genre_sim, weights)
        scored.append((total, semantic, tag_sim, genre_sim, cand))
    scored.sort(key=lambda item: item[0], reverse=True)

    results = [
        RecommendationItem(
            media=media_from_row(cand),
            similarity=display_similarity(total),
            components=ScoreComponents(
                semantic=round(semantic, 4), tags=round(tag_sim, 4), genres=round(genre_sim, 4)
            ),
        )
        for total, semantic, tag_sim, genre_sim, cand in scored[:limit]
    ]
    seen_related: set[int] = set()
    related = []
    for row in related_rows:
        if row["id"] in seen_related:
            continue
        seen_related.add(row["id"])
        related.append(RelatedItem(media=media_from_row(row), relation_type=row["relation_type"]))
    return RecommendResponse(
        seed=media_from_row(seeds[0]),
        seeds=[media_from_row(s) for s in seeds],
        weights=ScoreComponents(
            semantic=weights.semantic, tags=weights.tags, genres=weights.genres
        ),
        results=results,
        related=related,
    )


@router.get("/recommend", response_model=RecommendResponse)
def recommend_multi(
    ids: list[int] = Query(..., min_length=1, max_length=5),
    w_semantic: float = Query(0.5, ge=0),
    w_tags: float = Query(0.3, ge=0),
    w_genres: float = Query(0.2, ge=0),
    cross_media: bool = False,
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
    mal_user: str | None = None,
    anilist_user: str | None = None,
    exclude_list: str | None = None,
) -> RecommendResponse:
    unique_ids = list(dict.fromkeys(ids))
    return _recommend_for_seeds(
        unique_ids,
        weights=Weights(w_semantic, w_tags, w_genres).normalized(),
        cross_media=cross_media,
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
            "exclude_list": exclude_list,
        },
    )


@router.get("/recommend/{media_id}", response_model=RecommendResponse)
def recommend(
    media_id: int,
    w_semantic: float = Query(0.5, ge=0),
    w_tags: float = Query(0.3, ge=0),
    w_genres: float = Query(0.2, ge=0),
    cross_media: bool = False,
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
    mal_user: str | None = None,
    anilist_user: str | None = None,
    exclude_list: str | None = None,
) -> RecommendResponse:
    return _recommend_for_seeds(
        [media_id],
        weights=Weights(w_semantic, w_tags, w_genres).normalized(),
        cross_media=cross_media,
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
            "exclude_list": exclude_list,
        },
    )
