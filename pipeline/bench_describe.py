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


def as_array(value):
    """A sampled embedding as numpy, whatever pgvector handed back.

    register_vector returns a pgvector Vector in some versions and a
    numpy array in others. The raw SQL here does not care - psycopg
    adapts either back - but pipeline.describe.rank reads .shape, so the
    sweep died on the real code path while every query before it passed.
    """
    import numpy as np

    if hasattr(value, "to_numpy"):
        return value.to_numpy()
    if isinstance(value, np.ndarray):
        return value
    return np.asarray(list(value), dtype=np.float32)


ANIME_MEDIUMS = ["anime"]


def configure_scan(conn, pool: int) -> None:
    """Set the scan up exactly as pipeline.describe.rank does.

    Omitting this made the first run of this benchmark meaningless.
    pgvector's default ef_search is 40, so a LIMIT 2000 query returned
    about forty rows - which read as a 1.7 ms "pool of 2000" and an ANN
    recall of 48%, both of them measurements of the wrong query. A
    benchmark that does not configure the scan the way the caller does
    is not measuring the caller.
    """
    from api.db import hnsw_supports_iterative_scan

    ef_search = min(max(pool, 40), 1000)
    conn.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(ef_search),))
    if hnsw_supports_iterative_scan(conn):
        conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', false)")


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
    parser.add_argument(
        "--sweep",
        default="",
        help="comma-separated facet pool depths to compare, e.g. 200,400,800,2000",
    )
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
            configure_scan(conn, FACET_POOL)
            ms_ann, _ = time_query(
                conn, ann_sql(args.dim), {**base, "limit": FACET_POOL}, args.runs
            )
            configure_scan(conn, args.limit)
            ms_served, ids_served = time_query(
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
            # What the index misses, measured on what a caller is actually
            # served rather than on a slice of the pool.
            agreement.append(len(set(ids_served) & set(ids_exact)) / max(len(ids_exact), 1))

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

        if args.sweep:
            depths = sorted(
                (int(part) for part in args.sweep.split(",") if part.strip()), reverse=True
            )
            # Three facets, because that is the shape whose cost is in
            # question; one facet never uses the pool at all.
            sweep(conn, vectors[:3], depths, args.limit, args.runs)


def sweep(conn, vectors, depths: list[int], limit: int, runs: int) -> None:
    """Time a real multi-facet query at each pool depth, and say how much
    the answer changes.

    Through pipeline.describe.rank, not a copy of its SQL: the question
    is what the endpoint would do, and a reimplementation would drift
    from it exactly where the answer matters.

    The deepest depth is the baseline, because that is what ships today.
    Agreement is of the served ids, so a depth that returns the same 60
    titles is costing time for nothing.
    """
    import pipeline.describe as describe_module

    original = describe_module.FACET_POOL
    baseline: list[int] = []
    print()
    print(f"facet pool sweep, {len(vectors)} facets, top {limit}")
    try:
        for depth in depths:
            describe_module.FACET_POOL = depth
            timings = []
            served: list[int] = []
            for attempt in range(runs + 1):
                started = time.perf_counter()
                rows = describe_module.rank(
                    conn,
                    vectors,
                    mediums=ANIME_MEDIUMS,
                    limit=limit,
                    filter_sql="",
                    filter_params={},
                )
                elapsed = (time.perf_counter() - started) * 1000
                if attempt:
                    timings.append(elapsed)
                served = [row["id"] for row in rows]
            if not baseline:
                baseline = served
            same = len(set(served) & set(baseline)) / max(len(baseline), 1)
            order = "same order" if served == baseline else "reordered"
            print(
                f"  pool {depth:<6} {statistics.median(timings):8.1f} ms"
                f"   {same:6.1%} of the deepest pool's results, {order}"
            )
    finally:
        describe_module.FACET_POOL = original


if __name__ == "__main__":
    main()
