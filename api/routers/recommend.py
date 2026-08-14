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

from api.config import embed_model_id, settings
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

# Outgoing franchise relations of the candidate rows, for collapsing
# same-franchise duplicates WITHIN the results (three seasons of one series
# stacking three top-10 slots). Related ids outside the candidate set still
# matter: two seasons often link only through their shared parent entry, so
# the parent acts as the connecting node even when it is not a candidate.
CANDIDATE_EDGES_SQL = """
    SELECT r.media_id, r.related_id
    FROM media_relations r
    WHERE r.media_id = ANY(%(ids)s::int[]) AND r.relation_type = ANY(%(ftypes)s)
"""


def collapse_franchises(ordered_ids: list[int], edges: list[tuple[int, int]]) -> list[int]:
    """Keep only the best-ranked entry per franchise component.

    ordered_ids come sorted by score; edges are franchise relations from
    those ids (targets may be off-list connector nodes). Union-find groups
    connected entries and the first id seen per group survives.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)

    kept = []
    seen_roots: set[int] = set()
    for media_id in ordered_ids:
        root = find(media_id)
        if root not in seen_roots:
            seen_roots.add(root)
            kept.append(media_id)
    return kept


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

# Embeddings and scores of the user's own list, for the optional taste term
# (w_taste): the profile is the rating-weighted mean of these vectors. The
# random cap bounds work on very large lists without biasing any title kind.
TASTE_ANILIST_SQL = """
    SELECT e.embedding, al.score
    FROM anilist_list_entries al
    JOIN embeddings e ON e.media_id = al.media_id AND e.embed_model = %(model)s
    WHERE al.username = %(username)s
    ORDER BY random()
    LIMIT 2000
"""

TASTE_MAL_SQL = """
    SELECT e.embedding, l.score
    FROM mal_list_entries l
    JOIN media m ON m.id_mal = l.id_mal AND l.list_type = lower(m.media_type)
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE l.username = %(username)s
    ORDER BY random()
    LIMIT 2000
"""

TASTE_LIST_SQL = """
    SELECT e.embedding, ce.score
    FROM custom_exclusion_entries ce
    JOIN media m ON ((ce.kind = 'anilist' AND m.id = ce.ext_id)
        OR (ce.kind = 'mal_anime' AND m.media_type = 'ANIME' AND m.id_mal = ce.ext_id)
        OR (ce.kind = 'mal_manga' AND m.media_type = 'MANGA' AND m.id_mal = ce.ext_id))
    JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE ce.list_name = ANY(%(lists)s)
    ORDER BY random()
    LIMIT 2000
"""


def taste_weights(scores: list[float | None]) -> np.ndarray:
    """Per-title weights for the taste profile, from percentile rank among
    the user's own rated titles (same scheme as For-you sampling: effective
    score 40-100 from the percentile, unrated neutral at 70, then squared).
    """
    rated = sorted(s for s in scores if s is not None)
    weights = np.empty(len(scores))
    for i, score in enumerate(scores):
        if score is None or len(rated) <= 1:
            eff = 70.0
        else:
            pct = sum(1 for r in rated if r < score) / (len(rated) - 1)
            eff = 40.0 + 60.0 * min(pct, 1.0)
        weights[i] = (eff / 100.0) ** 2
    return weights


def _taste_profile(
    conn: Any,
    embed_model: str,
    *,
    anilist_user: str | None,
    mal_user: str | None,
    exclude_lists: list[str] | None,
) -> np.ndarray | None:
    """Rating-weighted mean of the user's list embeddings (L2-normalized),
    or None when no list source is configured or nothing is embedded."""
    names = [n.strip().lower() for n in (exclude_lists or []) if n.strip()]
    if anilist_user:
        sql, params = TASTE_ANILIST_SQL, {"username": anilist_user.strip().lower()}
    elif mal_user:
        sql, params = TASTE_MAL_SQL, {"username": mal_user.strip().lower()}
    elif names:
        sql, params = TASTE_LIST_SQL, {"lists": names}
    else:
        return None
    rows = conn.execute(sql, {**params, "model": embed_model}).fetchall()
    if not rows:
        return None
    vectors = np.array([_embedding_array(row["embedding"]) for row in rows])
    weights = taste_weights([row["score"] for row in rows])
    profile = (vectors * weights[:, np.newaxis]).sum(axis=0) / weights.sum()
    norm = np.linalg.norm(profile)
    return profile / norm if norm > 0 else None


def _choose_embed_model(
    models_per_seed: list[set[str]], counts_by_model: dict[str, int], preferred: str
) -> str | None:
    """Pick the embedding version to score with, deterministically.

    Mid-way through a re-embed migration a seed can carry vectors from two
    embedding spaces. The candidate pool is limited to entries embedded with
    the chosen version, so pick the version with the most catalog coverage
    among those every seed has. The configured model wins once its coverage
    is within 1% of the best: old-version rows linger after a migration, and
    a few permanently failed re-embeds must not pin scoring to the old space
    forever. Returns None when the seeds share no version.
    """
    common = set.intersection(*models_per_seed) if models_per_seed else set()
    if not common:
        return None
    best = max(counts_by_model.get(m, 0) for m in common)
    if preferred in common and counts_by_model.get(preferred, 0) >= 0.99 * best:
        return preferred
    return max(common, key=lambda m: (counts_by_model.get(m, 0), m))


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
        # The join returns one row per (seed, embedding version): during a
        # re-embed migration a seed can have vectors in two embedding spaces.
        rows = conn.execute(SEEDS_SQL, {"ids": seed_ids}).fetchall()
        by_id: dict[int, Any] = {}
        vectors_by_id: dict[int, dict[str, Any]] = {}
        for row in rows:
            by_id[row["id"]] = row
            if row["embed_model"] is not None:
                vectors_by_id.setdefault(row["id"], {})[row["embed_model"]] = row["embedding"]
        missing = [i for i in seed_ids if i not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"unknown media id(s): {missing}")
        seeds = [by_id[i] for i in seed_ids]

        not_embedded = [i for i in seed_ids if not vectors_by_id.get(i)]
        if not_embedded:
            raise HTTPException(
                status_code=409,
                detail=f"media {not_embedded} have no embedding yet;"
                " run `python -m pipeline.embed`",
            )
        model_sets = [set(vectors_by_id[i]) for i in seed_ids]
        counts: dict[str, int] = {}
        if any(len(s) > 1 for s in model_sets):
            # Only mid-migration: coverage decides which version to score in.
            counts = {
                r["embed_model"]: r["n"]
                for r in conn.execute(
                    "SELECT embed_model, count(*) AS n FROM embeddings GROUP BY embed_model"
                ).fetchall()
            }
        embed_model = _choose_embed_model(model_sets, counts, embed_model_id())
        if embed_model is None:
            raise HTTPException(
                status_code=409,
                detail="seeds span different embedding versions"
                " (a re-embed is in progress); try again later",
            )

        vectors = np.array([_embedding_array(vectors_by_id[i][embed_model]) for i in seed_ids])
        seed_vec = vectors.mean(axis=0)
        norm = np.linalg.norm(seed_vec)
        if norm > 0:
            seed_vec = seed_vec / norm

        taste_vec = None
        if weights.taste > 0:
            taste_vec = _taste_profile(
                conn,
                embed_model,
                anilist_user=filter_kwargs.get("anilist_user"),
                mal_user=filter_kwargs.get("mal_user"),
                exclude_lists=filter_kwargs.get("exclude_lists"),
            )
            if taste_vec is None:
                # No usable list source: drop the taste weight so the other
                # components reclaim it instead of silently shrinking scores.
                weights = Weights(weights.semantic, weights.tags, weights.genres).normalized()

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

        taste_select = (
            ", 1 - (e.embedding <=> %(taste_vec)s) AS taste" if taste_vec is not None else ""
        )
        candidate_sql = f"""
            SELECT {MEDIA_COLS},
                   1 - (e.embedding <=> %(seed_vec)s) AS semantic
                   {taste_select}
            FROM media m
            JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
            WHERE m.medium = ANY(%(mediums)s)
              AND m.id <> ALL(%(exclude_ids)s::int[])
              {filter_sql}
            ORDER BY e.embedding <=> %(seed_vec)s
            LIMIT %(pool_size)s
        """
        candidate_params = {
            "seed_vec": seed_vec,
            "model": embed_model,
            "mediums": mediums,
            "exclude_ids": exclude_ids,
            "pool_size": settings.candidate_pool,
            **filter_params,
        }
        if taste_vec is not None:
            candidate_params["taste_vec"] = taste_vec
        candidates = conn.execute(candidate_sql, candidate_params).fetchall()

        franchise_edges: list[tuple[int, int]] = []
        if exclude_franchise and candidates:
            edge_rows = conn.execute(
                CANDIDATE_EDGES_SQL,
                {"ids": [c["id"] for c in candidates], "ftypes": list(FRANCHISE_RELATIONS)},
            ).fetchall()
            franchise_edges = [(r["media_id"], r["related_id"]) for r in edge_rows]

    seed_tags = merge_tag_maps([tag_weight_map(s["tags"]) for s in seeds])
    seed_genres = sorted({g for s in seeds for g in (s["genres"] or [])})

    scored = []
    for cand in candidates:
        semantic = min(max(float(cand["semantic"]), 0.0), 1.0)
        tag_sim = tag_similarity(seed_tags, tag_weight_map(cand["tags"]))
        genre_sim = genre_similarity(seed_genres, cand["genres"] or [])
        taste_sim = min(max(float(cand["taste"]), 0.0), 1.0) if taste_vec is not None else 0.0
        total = final_score(semantic, tag_sim, genre_sim, weights, taste_sim)
        scored.append((total, semantic, tag_sim, genre_sim, taste_sim, cand))
    scored.sort(key=lambda item: item[0], reverse=True)

    if exclude_franchise and scored:
        # One entry per franchise in the results: without this, several
        # seasons of the same series stack multiple top-10 slots.
        keep = set(collapse_franchises([item[5]["id"] for item in scored], franchise_edges))
        scored = [item for item in scored if item[5]["id"] in keep]

    results = [
        RecommendationItem(
            media=media_from_row(cand),
            similarity=display_similarity(total),
            components=ScoreComponents(
                semantic=round(semantic, 4),
                tags=round(tag_sim, 4),
                genres=round(genre_sim, 4),
                taste=round(taste_sim, 4) if taste_vec is not None else None,
            ),
        )
        for total, semantic, tag_sim, genre_sim, taste_sim, cand in scored[:limit]
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
            semantic=weights.semantic,
            tags=weights.tags,
            genres=weights.genres,
            taste=weights.taste or None,
        ),
        results=results,
        related=related,
    )


@router.get("/recommend", response_model=RecommendResponse)
def recommend_multi(
    # Up to 20: the For-you feed re-runs slider changes through this endpoint
    # with its sampled seeds (max 20) pinned, so the feed stays stable.
    ids: list[int] = Query(..., min_length=1, max_length=20),
    w_semantic: float = Query(0.5, ge=0),
    w_tags: float = Query(0.3, ge=0),
    w_genres: float = Query(0.2, ge=0),
    w_taste: float = Query(0.0, ge=0),
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
    exclude_list: list[str] | None = Query(None),
    keep_planned: bool = False,
) -> RecommendResponse:
    unique_ids = list(dict.fromkeys(ids))
    return _recommend_for_seeds(
        unique_ids,
        weights=Weights(w_semantic, w_tags, w_genres, w_taste).normalized(),
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
            "exclude_lists": exclude_list,
            "keep_planned": keep_planned,
        },
    )


@router.get("/recommend/{media_id}", response_model=RecommendResponse)
def recommend(
    media_id: int,
    w_semantic: float = Query(0.5, ge=0),
    w_tags: float = Query(0.3, ge=0),
    w_genres: float = Query(0.2, ge=0),
    w_taste: float = Query(0.0, ge=0),
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
    exclude_list: list[str] | None = Query(None),
    keep_planned: bool = False,
) -> RecommendResponse:
    return _recommend_for_seeds(
        [media_id],
        weights=Weights(w_semantic, w_tags, w_genres, w_taste).normalized(),
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
            "exclude_lists": exclude_list,
            "keep_planned": keep_planned,
        },
    )
