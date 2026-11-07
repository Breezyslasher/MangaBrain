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


def run_bench(
    pairs_file: str,
    models: list[str],
    negatives: int,
    encode_batch_size: int,
    trust_remote_code: bool = False,
) -> None:
    from sentence_transformers import SentenceTransformer

    pairs = json.loads(Path(pairs_file).read_text())
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        kept, by_id, franchises, neg_rows = load_world(conn, pairs, negatives)

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
        print(f"[benchmark] embedding pool with {model_name}...", flush=True)
        # One broken model (unloadable config, out-of-memory encode, custom
        # code the installed libraries cannot run) must not kill the run;
        # the remaining models still get measured and reported.
        try:
            model = SentenceTransformer(model_name, trust_remote_code=trust_remote_code)
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
            m = results[model_name] = summarize(ranks)
            # Emit each row the moment it exists: a killed run (job timeout,
            # OOM) must not take every finished model's numbers with it.
            print(
                f"[benchmark] RESULT {model_name}: n={m['n']}"
                f" r@10={m['recall@10']:.3f} r@50={m['recall@50']:.3f} mrr={m['mrr']:.3f}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[benchmark] SKIPPED {model_name}: {exc}", flush=True)
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


# What the import rules let through, as description lengths: the old floor
# (200 characters), a typical short overview (130) and the new rule's floor
# (80, allowed only with five or more tags). "none" is a title with no
# description at all, embedded from its title, genres and tags.
DESCRIPTION_VARIANTS = [
    ("full", None, True),
    ("200 chars", 200, True),
    ("130 chars", 130, True),
    ("80 chars", 80, True),
    ("80 chars, no tags", 80, False),
    ("none", 0, True),
]
# The new rule passes if, on titles with 5+ tags, an 80-character
# description keeps at least this share of a 200-character one's r@10.
RULE_PASS_SHARE = 0.9
RULE_TAGS = 5


def variant_text(row: dict, chars: int | None, tags: bool) -> str:
    description = row["description_clean"] or ""
    if chars is not None:
        description = description[:chars]
    return embed_text(
        row["title_romaji"],
        row["title_english"],
        description or None,
        row["genres"],
        row["tags"] if tags else [],
    )


def run_description_bench(
    pairs_file: str, model_name: str, negatives: int, encode_batch_size: int
) -> bool:
    """Does matching survive the short descriptions the import rules allow?

    Every title in the pool - seeds, targets and negatives - is cut to the
    same length, and recall@10 is measured on all pairs and on the pairs
    whose seed and target both carry RULE_TAGS+ tags (the titles the new
    rule admits). Returns whether the rule passes."""
    from sentence_transformers import SentenceTransformer

    pairs = json.loads(Path(pairs_file).read_text())
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        kept, by_id, franchises, neg_rows = load_world(conn, pairs, negatives)
    pool_rows = list(by_id.values()) + list(neg_rows)
    index_of = {row["id"]: i for i, row in enumerate(pool_rows)}

    def tagged(media_id: int) -> bool:
        return len(by_id[media_id]["tags"] or []) >= RULE_TAGS

    rich = [p for p in kept if tagged(p["media_id"]) and tagged(p["rec_id"])]
    print(f"[benchmark] {len(rich)} of {len(kept)} pairs have {RULE_TAGS}+ tags on both sides")
    model = SentenceTransformer(model_name)
    results: dict[str, tuple[dict, dict]] = {}
    for label, chars, tags in DESCRIPTION_VARIANTS:
        texts = [variant_text(row, chars, tags) for row in pool_rows]
        vectors = np.asarray(
            model.encode(texts, batch_size=encode_batch_size, normalize_embeddings=True)
        )

        def ranks(subset: list[dict], vectors: np.ndarray = vectors) -> list[int]:
            out = []
            for p in subset:
                seed, target = p["media_id"], p["rec_id"]
                scores = vectors @ vectors[index_of[seed]]
                excluded = {
                    index_of[i] for i in ({seed} | franchises.get(seed, set())) if i in index_of
                }
                rank = rank_of_target(scores, index_of[target], excluded)
                if rank is not None:
                    out.append(rank)
            return out

        every, rich_m = summarize(ranks(kept)), summarize(ranks(rich))
        results[label] = (every, rich_m)
        print(
            f"[benchmark] RESULT {label}: all r@10={every['recall@10']:.3f}"
            f" r@50={every['recall@50']:.3f} | {RULE_TAGS}+ tags r@10={rich_m['recall@10']:.3f}"
            f" r@50={rich_m['recall@50']:.3f}",
            flush=True,
        )
    print(f"\n{'description':<20} {'all r@10':>9} {'all r@50':>9} {'tagged r@10':>12}")
    for label, (every, rich_m) in results.items():
        print(
            f"{label:<20} {every['recall@10']:>9.3f} {every['recall@50']:>9.3f}"
            f" {rich_m['recall@10']:>12.3f}"
        )
    floor = results["200 chars"][1]["recall@10"]
    short = results["80 chars"][1]["recall@10"]
    share = short / floor if floor else 0.0
    passed = share >= RULE_PASS_SHARE
    print(
        f"\nrule check: on titles with {RULE_TAGS}+ tags, 80 characters keeps {share:.0%} of"
        f" 200 characters' r@10 (needs {RULE_PASS_SHARE:.0%}): {'PASS' if passed else 'FAIL'}"
    )
    return passed


def load_world(conn, pairs: list[dict], negatives: int):
    """Pairs that survive filtering, plus the pool they are ranked in.

    Split out of run_bench so the score benchmark ranks over exactly
    the same world the model bake-off does, rather than a second
    arrangement of it that could drift.
    """
    involved = sorted({p["media_id"] for p in pairs} | {p["rec_id"] for p in pairs})
    by_id = {row["id"]: row for row in conn.execute(MEDIA_TEXT_SQL, {"ids": involved}).fetchall()}
    franchises = load_franchises(conn, sorted({p["media_id"] for p in pairs}))

    kept = []
    dropped_missing = dropped_franchise = 0
    for pair in pairs:
        seed, target = pair["media_id"], pair["rec_id"]
        if seed not in by_id or target not in by_id:
            dropped_missing += 1
            continue
        if target in franchises.get(seed, set()):
            dropped_franchise += 1
            continue
        kept.append(pair)
    print(
        f"[benchmark] {len(kept)} pairs kept"
        f" ({dropped_missing} not in catalog, {dropped_franchise} franchise-linked)"
    )
    neg_rows = conn.execute(NEGATIVES_SQL, {"exclude": involved, "n": negatives}).fetchall()
    return kept, by_id, franchises, neg_rows


def run_score_bench(
    pairs_file: str,
    model_name: str,
    negatives: int,
    encode_batch_size: int,
    weights_spec: str,
) -> None:
    """Rank by the REAL weighted score, not by raw cosine.

    The existing benchmark answers "which embedding model", and it is
    the only measurement this project has. Nothing measures the ranking
    function itself, so every change to the weights, to tag similarity
    or to the genre term has gone in on argument alone.

    That became untenable with a concrete change in hand: genre
    similarity over an empty set used to score zero, and a count found
    3,028 anime, 5,130 manga and 50,454 books with no genres at all -
    every one of them capped at 0.8 of the available score for a gap in
    someone's cataloguing rather than anything about the title.

    So this ranks each seed's pool by final_score and reports r@10 twice:
    once treating an absent genre list as undefined (the weight
    redistributes) and once as zero (what shipped before). The
    difference is the change, measured.
    """
    import numpy as np
    from sentence_transformers import SentenceTransformer

    from api.scoring import Weights, final_score, genre_similarity, tag_similarity, tag_weight_map

    parts = [float(x) for x in weights_spec.split(",")]
    weights = Weights(semantic=parts[0], tags=parts[1], genres=parts[2]).normalized()
    print(f"[benchmark] weights {weights.semantic:.2f}/{weights.tags:.2f}/{weights.genres:.2f}")

    pairs = json.loads(Path(pairs_file).read_text())
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        kept, by_id, franchises, neg_rows = load_world(conn, pairs, negatives)

    pool_rows = list(by_id.values()) + list(neg_rows)
    pool_ids = [row["id"] for row in pool_rows]
    index_of = {media_id: i for i, media_id in enumerate(pool_ids)}
    print(f"[benchmark] pool: {len(pool_rows)} titles ({len(neg_rows)} negatives)")

    model = SentenceTransformer(model_name)
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
    print(f"[benchmark] embedding pool with {model_name}...", flush=True)
    vectors = np.asarray(
        model.encode(texts, batch_size=encode_batch_size, normalize_embeddings=True)
    )

    # Precomputed once: the inner loop runs pool-size times per pair.
    tag_maps = [tag_weight_map(row["tags"]) for row in pool_rows]
    genre_sets = [list(row["genres"] or []) for row in pool_rows]
    empty = sum(1 for g in genre_sets if not g)
    print(f"[benchmark] {empty} of {len(pool_rows)} pool titles have no genres")

    ranks: dict[str, list[int]] = {"undefined": [], "zero": []}
    for done, pair in enumerate(kept, start=1):
        seed, target = pair["media_id"], pair["rec_id"]
        seed_idx = index_of[seed]
        semantic = vectors @ vectors[seed_idx]
        seed_tags, seed_genres = tag_maps[seed_idx], genre_sets[seed_idx]
        excluded = {index_of[i] for i in ({seed} | franchises.get(seed, set())) if i in index_of}

        for treatment in ("undefined", "zero"):
            scores = np.empty(len(pool_rows), dtype=np.float32)
            for i in range(len(pool_rows)):
                genre_sim = genre_similarity(seed_genres, genre_sets[i])
                if treatment == "zero" and genre_sim is None:
                    genre_sim = 0.0
                scores[i] = final_score(
                    float(semantic[i]), tag_similarity(seed_tags, tag_maps[i]), genre_sim, weights
                )
            rank = rank_of_target(scores, index_of[target], excluded)
            if rank is not None:
                ranks[treatment].append(rank)
        if done % 100 == 0:
            print(f"[benchmark]   {done}/{len(kept)} pairs scored", flush=True)

    print()
    print(f"{'absent genres treated as':<26} {'n':>6} {'r@10':>7} {'r@50':>7} {'mrr':>7}")
    for treatment in ("undefined", "zero"):
        m = summarize(ranks[treatment])
        label = "undefined (new)" if treatment == "undefined" else "zero (previous)"
        print(
            f"  {label:<24} {m['n']:>6} {m['recall@10']:>7.3f}"
            f" {m['recall@50']:>7.3f} {m['mrr']:>7.3f}"
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
    run.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="allow models that ship custom code (e.g. jina v5); executes"
        " third-party code from the model repository, so opt-in only",
    )
    run.add_argument("--encode-batch-size", type=int, default=16)

    score = sub.add_parser("score", help="rank by the real weighted score, not raw cosine")
    score.add_argument("--pairs-file", default="benchmark_pairs.json")
    score.add_argument("--model", default=settings.embed_model)
    score.add_argument("--negatives", type=int, default=2000)
    score.add_argument("--encode-batch-size", type=int, default=32)
    score.add_argument(
        "--weights",
        default="0.5,0.3,0.2",
        help="semantic,tags,genres - the API's defaults",
    )

    desc = sub.add_parser(
        "descriptions", help="does matching survive the short descriptions the rules allow?"
    )
    desc.add_argument("--pairs-file", default="benchmark_pairs.json")
    desc.add_argument("--model", default=settings.embed_model)
    desc.add_argument("--negatives", type=int, default=2000)
    desc.add_argument("--encode-batch-size", type=int, default=32)

    args = parser.parse_args()
    if args.command == "descriptions":
        if not run_description_bench(
            args.pairs_file, args.model, args.negatives, args.encode_batch_size
        ):
            raise SystemExit(1)
        return
    if args.command == "fetch":
        fetch_pairs(args.out, args.pairs, args.min_rating)
    elif args.command == "score":
        run_score_bench(
            args.pairs_file,
            args.model,
            args.negatives,
            args.encode_batch_size,
            args.weights,
        )
    else:
        run_bench(
            args.pairs_file,
            args.model,
            args.negatives,
            args.encode_batch_size,
            trust_remote_code=args.trust_remote_code,
        )


if __name__ == "__main__":
    main()
