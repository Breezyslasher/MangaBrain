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
  binary  an exact Hamming scan over the quantized copy, reranked on the
          real vectors. Exact over the whole catalog, like the first,
          but reading 96 bytes a row instead of a TOAST fetch.

Measured once already, on 165,000 embeddings: exact 4.0 s, with
EXPLAIN naming 1,880,352 shared buffer hits - about 15 GB for a few
hundred MB of data, because a 3,072-byte vector is over the TOAST
threshold and every row costs an out-of-line fetch. That is what the
binary column is for, and this is what tells you whether it worked.

Recall is reported beside the time, because a fast answer that is not
the right answer is not an answer. The number to watch is binary's
recall of the exact top N: if it is at or near 100%, /describe can scan
the whole catalog and the facet pool can go.
"""

import argparse
import statistics
import time

import psycopg
from pgvector.psycopg import register_vector
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


# Two stage: an exact Hamming scan narrows to RERANK_DEPTH, then the real
# vectors decide the order. Only the reranked rows are detoasted, so the
# cost that made the exact scan 4 s is paid on a few thousand rows rather
# than all 165,000.
def binary_sql(dim: int, rerank: int) -> str:
    return f"""
        WITH coarse AS (
            SELECT e.media_id
            FROM media m
            JOIN embeddings e ON e.media_id = m.id
            WHERE m.medium = ANY(%(mediums)s)
              AND length(e.embedding_bits) = {dim}
              AND coalesce(m.description_clean, '') <> ''
            ORDER BY e.embedding_bits <~> binary_quantize(%(query_vec)s::vector({dim}))
            LIMIT {rerank}
        )
        SELECT c.media_id AS id
        FROM coarse c
        JOIN embeddings e ON e.media_id = c.media_id
        ORDER BY e.embedding::vector({dim}) <=> %(query_vec)s
        LIMIT %(limit)s
    """


QUANTIZED_SQL = "SELECT count(*) AS n FROM embeddings WHERE embedding_bits IS NOT NULL"

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
    parser.add_argument("--rerank", type=int, default=2000, help="binary stage depth")
    args = parser.parse_args()

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        # Without this a sampled embedding comes back as text and goes
        # back in untyped, which binary_quantize cannot resolve: it is
        # overloaded on vector and halfvec. The casts below make the
        # queries correct either way; this makes the values numpy again.
        register_vector(conn)
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

        quantized = (conn.execute(QUANTIZED_SQL).fetchone() or {})["n"]
        if quantized < total:
            print(
                f"  {total - quantized} embeddings have no quantized copy;"
                " run python -m pipeline.quantize first"
            )

        exact, ann, served, binary = [], [], [], []
        agreement, binary_recall = [], []
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
            if quantized:
                ms_binary, ids_binary = time_query(
                    conn,
                    binary_sql(args.dim, args.rerank),
                    {**base, "limit": args.limit},
                    args.runs,
                )
                binary.append(ms_binary)
                binary_recall.append(len(set(ids_binary) & set(ids_exact)) / max(len(ids_exact), 1))
            # What the index misses: how much of the exact top-N the
            # approximate scan found at the same depth.
            top = set(ids_ann[: args.limit])
            agreement.append(len(top & set(ids_exact)) / max(len(ids_exact), 1))

        print()
        print(f"exact  full scan, top {args.limit:<5} median {statistics.median(exact):8.1f} ms")
        print(f"ann    pool of {FACET_POOL:<10} median {statistics.median(ann):8.1f} ms")
        print(f"ann    top {args.limit:<13} median {statistics.median(served):8.1f} ms")
        if binary:
            print(
                f"binary rerank {args.rerank:<7} median {statistics.median(binary):8.1f} ms"
                f"   recall {statistics.mean(binary_recall):.1%}"
            )
        print(f"ann recall of the exact top {args.limit}: {statistics.mean(agreement):.1%}")
        print()
        # Three facets is the shape that motivated this.
        print(f"three facets cost about {statistics.median(ann) * 3:.0f} ms today (pooled,")
        print(f"truncated), {statistics.median(exact) * 3:.0f} ms exact by sequential scan", end="")
        if binary:
            print(f", {statistics.median(binary) * 3:.0f} ms exact over the quantized copy.")
            print()
            if statistics.mean(binary_recall) >= 0.99:
                print("binary recall is at or above 99%: the facet pool can go.")
            else:
                print(f"binary recall is {statistics.mean(binary_recall):.1%};")
                print("raise --rerank and measure again before trusting it.")
        else:
            print(".")


if __name__ == "__main__":
    main()
