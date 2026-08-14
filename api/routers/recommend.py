"""Content-based recommendations for a seed title.

Candidate retrieval is a semantic ANN query against the pgvector HNSW index
(top N within the seed's medium group, or all mediums when cross-media is on),
with filters applied in SQL. Candidates are then re-ranked in Python with the
full weighted score: semantic + tags + genres. Popularity never participates.
Direct adaptation/source relations are excluded from results and returned in
the separate "related" list.
"""

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
from api.scoring import Weights, final_score, genre_similarity, tag_similarity, tag_weight_map

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
        WHERE r.media_id = %(id)s AND r.relation_type = ANY(%(ftypes)s)
        UNION
        SELECT r.related_id, f.depth + 1
        FROM media_relations r
        JOIN franchise f ON r.media_id = f.id
        WHERE f.depth < 6 AND r.relation_type = ANY(%(ftypes)s)
    )
    SELECT DISTINCT id FROM franchise
"""

# Candidates are pinned to the SEED's embedding version: mid-way through a
# re-embed migration the table holds vectors from two different embedding
# spaces, and comparing across them silently produces garbage similarities.
# Matching the seed's own version keeps every comparison in one space while
# staying usable throughout the migration (old-space seeds rank against
# old-space candidates until pipeline.embed catches up).
SEED_SQL = f"""
    SELECT {MEDIA_COLS}, e.embedding, e.embed_model
    FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id
    WHERE m.id = %(id)s
"""

RELATED_SQL = f"""
    SELECT {MEDIA_COLS}, r.relation_type
    FROM media_relations r
    JOIN media m ON m.id = r.related_id
    WHERE r.media_id = %(id)s AND r.relation_type = ANY(%(relation_types)s)
    ORDER BY m.id
"""


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
    mal_user: str | None = None,
) -> RecommendResponse:
    with get_pool().connection() as conn:
        seed = conn.execute(SEED_SQL, {"id": media_id}).fetchone()
        if seed is None:
            raise HTTPException(status_code=404, detail="unknown media id")
        if seed["embedding"] is None:
            raise HTTPException(
                status_code=409,
                detail="media has no embedding yet; run `python -m pipeline.embed`",
            )

        related_rows = conn.execute(
            RELATED_SQL, {"id": media_id, "relation_types": list(ADAPTATION_RELATIONS)}
        ).fetchall()
        exclude_ids = [media_id, *(row["id"] for row in related_rows)]
        if exclude_franchise:
            franchise_rows = conn.execute(
                FRANCHISE_SQL, {"id": media_id, "ftypes": list(FRANCHISE_RELATIONS)}
            ).fetchall()
            exclude_ids.extend(row["id"] for row in franchise_rows)

        mediums = ALL_MEDIUMS if cross_media else group_for_medium(seed["medium"])
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

        # The WHERE clauses post-filter the HNSW index scan, and pgvector's
        # default ef_search (40) would cap the scan long before candidate_pool
        # rows survive. Raise ef_search to the pool size and, on pgvector 0.8+,
        # let the scan iterate until the LIMIT is satisfied. relaxed_order may
        # return neighbors slightly out of distance order, which is fine: the
        # full weighted re-rank below reorders everything anyway. set_config
        # with is_local=true scopes both settings to this transaction.
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
                "seed_vec": seed["embedding"],
                "model": seed["embed_model"],
                "mediums": mediums,
                "exclude_ids": exclude_ids,
                "pool_size": settings.candidate_pool,
                **filter_params,
            },
        ).fetchall()

    weights = Weights(w_semantic, w_tags, w_genres).normalized()
    seed_tags = tag_weight_map(seed["tags"])
    seed_genres = seed["genres"] or []

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
            similarity=round(total * 100, 1),
            components=ScoreComponents(
                semantic=round(semantic, 4), tags=round(tag_sim, 4), genres=round(genre_sim, 4)
            ),
        )
        for total, semantic, tag_sim, genre_sim, cand in scored[:limit]
    ]
    related = [
        RelatedItem(media=media_from_row(row), relation_type=row["relation_type"])
        for row in related_rows
    ]
    return RecommendResponse(
        seed=media_from_row(seed),
        weights=ScoreComponents(
            semantic=weights.semantic, tags=weights.tags, genres=weights.genres
        ),
        results=results,
        related=related,
    )
