"""Measure what description search actually costs, before tuning it.

The open question is whether /describe needs its 2,000-row facet pool at
all. The pool exists because reciprocal rank fusion needs ranks, and the
HNSW index is there because an exact scan was assumed to be too slow -
assumed, not measured. If an exact scan over the whole catalog is fast
enough, the pool is solving a problem that does not exist, and every
truncation artefact it causes goes away with it.

    docker exec mangabrain-worker-1 python -m pipeline.bench_describe

Three timings per query vector, warm:

  exact   a sequential scan over every embedded title. Exact by
          construction: the partial HNSW index is a per-dimension
          expression index, so dropping the ::vector(dim) cast from the
          ORDER BY makes the planner unable to use it.
  ann     what /describe does now - the same query with the cast, at the
          facet pool depth.
  served  the single-facet path, which asks only for what it returns.

It also reports the pgvector version, because binary quantization - the
fallback if exact is too slow - needs 0.7 or newer.
"""

import argparse
import statistics
import time

import psycopg
from psycopg.rows import dict_row

from api.config import settings
from pipeline.describe import FACET_POOL

VERSION_SQL = "SELECT extversion FROM pg_extension WHERE extname = 'vector'"

SAMPLE_SQL = """
    SELECT e.embedding
    FROM embeddings e
    JOIN media m ON m.id = e.media_id
    WHERE m.media_type = 'ANIME' AND vector_dims(e.embedding) = %(dim)s
    ORDER BY random()
    LIMIT %(n)s
"""

COUNT_SQL = """
    SELECT count(*) AS n
    FROM embeddings e
    JOIN media m ON m.id = e.media_id
    WHERE m.media_type = 'ANIME' AND vector_dims(e.embedding) = %(dim)s
"""


def exact_sql(dim: int) -> str:
    """No cast in the ORDER BY, so the partial index cannot match."""
    return f"""
        SELECT m.id
        FROM media m
        JOIN embeddings e ON e.media_id = m.id
        WHERE m.medium = ANY(%(mediums)s)
          AND vector_dims(e.embedding) = {dim}
          AND coalesce(m.description_clean, '') <> ''
        ORDER BY e.embedding <=> %(query_vec)s
        LIMIT %(limit)s
    """


def ann_sql(dim: int) -> str:
    """The cast repeated exactly as the index defines it."""
    return f"""
        SELECT m.id
        FROM media m
        JOIN embeddings e ON e.media_id = m.id
        WHERE m.medium = ANY(%(mediums)s)
          AND vector_dims(e.embedding) = {dim}
          AND coalesce(m.description_clean, '') <> ''
        ORDER BY e.embedding::vector({dim}) <=> %(query_vec)s
        LIMIT %(limit)s
    """


ANIME_MEDIUMS = ["anime"]


def time_query(conn, sql: str, params: dict, runs: int) -> tuple[float, list[int]]:
    """Median milliseconds over `runs`, discarding the first as cold."""
    ids: list[int] = []
    timings: list[float] = []
    for attempt in range(runs + 1):
        started = time.perf_counter()
        rows = conn.execute(sql, params).fetchall()
        elapsed = (time.perf_counter() - started) * 1000
        if attempt:
            timings.append(elapsed)
        ids = [row["id"] for row in rows]
    return statistics.median(timings), ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Time exact vs indexed description search")
    parser.add_argument("--dim", type=int, default=768)
    parser.add_argument("--queries", type=int, default=5, help="random query vectors")
    parser.add_argument("--runs", type=int, default=3, help="timed runs per query")
    parser.add_argument("--limit", type=int, default=60, help="results served")
    args = parser.parse_args()

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        version = (conn.execute(VERSION_SQL).fetchone() or {}).get("extversion", "unknown")
        total = (conn.execute(COUNT_SQL, {"dim": args.dim}).fetchone() or {})["n"]
        print(f"pgvector {version}; {total} embedded anime at {args.dim} dimensions")
        if version != "unknown" and tuple(int(p) for p in version.split(".")[:2]) < (0, 7):
            print("  (binary quantization needs pgvector 0.7+; this build cannot do it)")

        vectors = [
            row["embedding"]
            for row in conn.execute(SAMPLE_SQL, {"dim": args.dim, "n": args.queries}).fetchall()
        ]
        if not vectors:
            print("no embeddings at that dimension; nothing to measure")
            return

        exact, ann, served = [], [], []
        agreement = []
        for vector in vectors:
            base = {"query_vec": vector, "mediums": ANIME_MEDIUMS}
            ms_exact, ids_exact = time_query(
                conn, exact_sql(args.dim), {**base, "limit": args.limit}, args.runs
            )
            ms_ann, ids_ann = time_query(
                conn, ann_sql(args.dim), {**base, "limit": FACET_POOL}, args.runs
            )
            ms_served, _ = time_query(
                conn, ann_sql(args.dim), {**base, "limit": args.limit}, args.runs
            )
            exact.append(ms_exact)
            ann.append(ms_ann)
            served.append(ms_served)
            # What the index misses: how much of the exact top-N the
            # approximate scan found at the same depth.
            top = set(ids_ann[: args.limit])
            agreement.append(len(top & set(ids_exact)) / max(len(ids_exact), 1))

        print()
        print(f"exact  full scan, top {args.limit:<5} median {statistics.median(exact):8.1f} ms")
        print(f"ann    pool of {FACET_POOL:<10} median {statistics.median(ann):8.1f} ms")
        print(f"ann    top {args.limit:<13} median {statistics.median(served):8.1f} ms")
        print(f"recall of the exact top {args.limit}: {statistics.mean(agreement):.1%}")
        print()
        # Three facets is the shape that motivated this.
        print(f"a three-facet query costs about {statistics.median(ann) * 3:.0f} ms today,")
        print(f"and {statistics.median(exact) * 3:.0f} ms exact with no pool and no truncation.")


if __name__ == "__main__":
    main()
