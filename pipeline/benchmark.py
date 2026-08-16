"""Offline quality benchmark from AniList's human recommendation pairs.

AniList users submit "if you liked X, try Y" pairs, community-voted via a
rating counter. The top-rated pairs are a ground truth for what humans
consider good recommendations, and this script turns them into a metric:
for each pair (seed, target), rank the target among a candidate pool by
embedding similarity and report recall@10 / recall@50 / MRR. Every future
algorithm question ("would model X be better?") becomes a measured number
instead of an eyeballed table.

The pairs are used for EVALUATION ONLY. The ranking function itself stays
purely content-based; nothing from this dataset enters scoring.

The bench isolates the embedding model variable: candidates are ranked by
semantic cosine only, over texts built with the exact production recipe
(pipeline.embed.embed_text). Tag and genre components are identical across
models, so a model that wins here wins in the blended score too.

Franchise-linked pairs are dropped (the engine excludes a seed's franchise
from results, so it could never retrieve them), and each seed's franchise
is likewise removed from its ranking pool.

Usage (needs the database and, for fetch, AniList access):
    python -m pipeline.benchmark fetch --out benchmark_pairs.json
    python -m pipeline.benchmark run --pairs-file benchmark_pairs.json \
        --model BAAI/bge-small-en-v1.5 --model avsolatorio/GIST-small-Embedding-v0
"""

import argparse
import json
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

from api.config import settings
from pipeline.client import RateLimitedClient
from pipeline.embed import embed_text

ANILIST_URL = "https://graphql.anilist.co"
PER_PAGE = 50
# AniList caps page offsets at 5000 rows, same as media pagination.
MAX_PAGES = 100

RECS_QUERY = """
query ($page: Int!, $perPage: Int!) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    recommendations(sort: RATING_DESC) {
      rating
      media { id }
      mediaRecommendation { id }
    }
  }
}
"""

# Same franchise semantics as the recommender (api.routers.recommend), but
# rooted: returns (root, related) so each seed gets its own franchise set.
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

ROOTED_FRANCHISE_SQL = """
    WITH RECURSIVE franchise(root, id, depth) AS (
        SELECT r.media_id, r.related_id, 1
        FROM media_relations r
        WHERE r.media_id = ANY(%(ids)s::int[]) AND r.relation_type = ANY(%(ftypes)s)
        UNION
        SELECT f.root, r.related_id, f.depth + 1
        FROM media_relations r
        JOIN franchise f ON r.media_id = f.id
        WHERE f.depth < 6 AND r.relation_type = ANY(%(ftypes)s)
    )
    SELECT DISTINCT root, id FROM franchise
"""

MEDIA_TEXT_SQL = """
    SELECT id, media_type, title_romaji, title_english, description_clean, genres, tags
    FROM media
    WHERE id = ANY(%(ids)s::int[])
"""

NEGATIVES_SQL = """
    SELECT id, media_type, title_romaji, title_english, description_clean, genres, tags
    FROM media
    WHERE description_clean IS NOT NULL AND description_clean <> ''
      AND NOT (id = ANY(%(exclude)s::int[]))
    ORDER BY random()
    LIMIT %(n)s
"""


def fetch_pairs(out_path: str, max_pairs: int, min_rating: int) -> None:
    """Pull the top community-rated recommendation pairs from AniList."""
    client = RateLimitedClient(
        min_interval=settings.anilist_min_interval, cache_dir=settings.http_cache_dir
    )
    pairs: list[dict] = []
    try:
        for page in range(1, MAX_PAGES + 1):
            payload = client.request(
                "POST",
                ANILIST_URL,
                json_body={
                    "query": RECS_QUERY,
                    "variables": {"page": page, "perPage": PER_PAGE},
                },
            )
            data = (payload.get("data") or {}).get("Page") or {}
            rows = data.get("recommendations") or []
            done = False
            for row in rows:
                rating = row.get("rating") or 0
                media = row.get("media") or {}
                rec = row.get("mediaRecommendation") or {}
                if rating < min_rating:
                    # Sorted by rating desc: everything after is below cut.
                    done = True
                    break
                if media.get("id") and rec.get("id"):
                    pairs.append({"media_id": media["id"], "rec_id": rec["id"], "rating": rating})
            print(f"[benchmark] page {page}: {len(pairs)} pairs")
            if (
                done
                or len(pairs) >= max_pairs
                or not (data.get("pageInfo") or {}).get("hasNextPage")
            ):
                break
    finally:
        client.close()
    pairs = pairs[:max_pairs]
    Path(out_path).write_text(json.dumps(pairs, indent=0))
    print(f"[benchmark] wrote {len(pairs)} pairs to {out_path}")


def load_franchises(conn, seed_ids: list[int]) -> dict[int, set[int]]:
    rows = conn.execute(
        ROOTED_FRANCHISE_SQL, {"ids": seed_ids, "ftypes": list(FRANCHISE_RELATIONS)}
    ).fetchall()
    franchises: dict[int, set[int]] = {}
    for row in rows:
        franchises.setdefault(row["root"], set()).add(row["id"])
    return franchises


def rank_of_target(scores: np.ndarray, target_idx: int, excluded_idx: set[int]) -> int | None:
    """1-based rank of the target among the pool (higher score = better).

    excluded_idx rows (the seed and its franchise) leave the pool entirely.
    Returns None when the target itself is excluded.
    """
    if target_idx in excluded_idx:
        return None
    target_score = scores[target_idx]
    mask = np.ones(len(scores), dtype=bool)
    if excluded_idx:
        mask[list(excluded_idx)] = False
    mask[target_idx] = False
    return int((scores[mask] > target_score).sum()) + 1


def summarize(ranks: list[int]) -> dict[str, float]:
    """recall@10 / recall@50 / MRR over the achieved target ranks."""
    if not ranks:
        return {"n": 0, "recall@10": 0.0, "recall@50": 0.0, "mrr": 0.0}
    arr = np.array(ranks, dtype=float)
    return {
        "n": len(ranks),
        "recall@10": float((arr <= 10).mean()),
        "recall@50": float((arr <= 50).mean()),
        "mrr": float((1.0 / arr).mean()),
    }


def run_bench(pairs_file: str, models: list[str], negatives: int, encode_batch_size: int) -> None:
    from sentence_transformers import SentenceTransformer

    pairs = json.loads(Path(pairs_file).read_text())
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        involved = sorted({p["media_id"] for p in pairs} | {p["rec_id"] for p in pairs})
        media_rows = conn.execute(MEDIA_TEXT_SQL, {"ids": involved}).fetchall()
        by_id = {row["id"]: row for row in media_rows}

        seed_ids = sorted({p["media_id"] for p in pairs})
        franchises = load_franchises(conn, seed_ids)

        kept = []
        dropped_missing = dropped_franchise = 0
        for p in pairs:
            seed, target = p["media_id"], p["rec_id"]
            if seed not in by_id or target not in by_id:
                dropped_missing += 1
                continue
            if target in franchises.get(seed, set()):
                dropped_franchise += 1
                continue
            kept.append(p)
        print(
            f"[benchmark] {len(kept)} pairs kept"
            f" ({dropped_missing} not in catalog, {dropped_franchise} franchise-linked)"
        )

        neg_rows = conn.execute(NEGATIVES_SQL, {"exclude": involved, "n": negatives}).fetchall()

    pool_rows = list(by_id.values()) + list(neg_rows)
    pool_ids = [row["id"] for row in pool_rows]
    texts = [
        embed_text(
            row["title_romaji"],
            row["title_english"],
            row["description_clean"],
            row["genres"],
            row["tags"],
        )
        for row in pool_rows
    ]
    print(f"[benchmark] pool: {len(pool_rows)} titles ({len(neg_rows)} negatives)")

    index_of = {media_id: i for i, media_id in enumerate(pool_ids)}
    results = {}
    for model_name in models:
        print(f"[benchmark] embedding pool with {model_name}...")
        # One broken model (unloadable config, out-of-memory encode, custom
        # code the installed libraries cannot run) must not kill the run;
        # the remaining models still get measured and reported.
        try:
            model = SentenceTransformer(model_name)
            vectors = model.encode(texts, batch_size=encode_batch_size, normalize_embeddings=True)
            vectors = np.asarray(vectors)

            ranks = []
            for p in kept:
                seed, target = p["media_id"], p["rec_id"]
                scores = vectors @ vectors[index_of[seed]]
                excluded_idx = {
                    index_of[i] for i in ({seed} | franchises.get(seed, set())) if i in index_of
                }
                rank = rank_of_target(scores, index_of[target], excluded_idx)
                if rank is not None:
                    ranks.append(rank)
            results[model_name] = summarize(ranks)
        except Exception as exc:  # noqa: BLE001
            print(f"[benchmark] SKIPPED {model_name}: {exc}")
            continue
        finally:
            # Free the model before the next candidate loads: two large
            # models resident at once would OOM small hosts.
            model = None

    print(f"\n{'model':<45} {'n':>5} {'r@10':>7} {'r@50':>7} {'mrr':>7}")
    for model_name, m in results.items():
        print(
            f"{model_name:<45} {m['n']:>5}"
            f" {m['recall@10']:>7.3f} {m['recall@50']:>7.3f} {m['mrr']:>7.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="AniList recommendation-pair benchmark")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download top-rated recommendation pairs")
    fetch.add_argument("--out", default="benchmark_pairs.json")
    fetch.add_argument("--pairs", type=int, default=5000)
    fetch.add_argument("--min-rating", type=int, default=5)

    run = sub.add_parser("run", help="score embedding models against the pairs")
    run.add_argument("--pairs-file", default="benchmark_pairs.json")
    run.add_argument(
        "--model",
        action="append",
        required=True,
        help="sentence-transformers model id; repeat to compare several",
    )
    run.add_argument("--negatives", type=int, default=5000)
    run.add_argument("--encode-batch-size", type=int, default=16)

    args = parser.parse_args()
    if args.command == "fetch":
        fetch_pairs(args.out, args.pairs, args.min_rating)
    else:
        run_bench(args.pairs_file, args.model, args.negatives, args.encode_batch_size)


if __name__ == "__main__":
    main()
