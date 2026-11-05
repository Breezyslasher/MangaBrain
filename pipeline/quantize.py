"""Fill embedding_bits for rows embedded before the column existed.

pipeline.embed writes it alongside every vector it stores, so this is
only for the catalog that was already embedded - after which it finds
nothing and costs one indexed count.

    docker exec mangabrain-worker-1 python -m pipeline.quantize

Why the column exists at all is in db/schema.sql: a 768-dim float32
vector is over the TOAST threshold, so an exact scan pays an out-of-line
fetch and a decompression per row - measured at 4.0 s and 15 GB of
buffer traffic for one query. The quantized copy is 96 bytes, stores
inline, and puts the whole catalog in ~16 MB.

Batched rather than one UPDATE, because a single statement over 165,000
rows holds one transaction open for the duration, bloats the table with
dead tuples all at once, and gives no progress on a NAS where it may run
for minutes.
"""

import argparse
import time

import psycopg

from api.config import settings

PENDING_SQL = "SELECT count(*) FROM embeddings WHERE embedding_bits IS NULL"

# ctid, because there is no cheap ordering key: the primary key is
# (media_id, embed_model) and scanning it per batch would be a sort each
# time. LIMIT with no ORDER BY is fine here - every pending row is
# equivalent and the loop ends when none are left.
BATCH_SQL = """
    UPDATE embeddings
    SET embedding_bits = binary_quantize(embedding)
    WHERE ctid IN (
        SELECT ctid FROM embeddings WHERE embedding_bits IS NULL LIMIT %(batch)s
    )
"""


def quantize(batch: int = 5000) -> int:
    done = 0
    started = time.monotonic()
    with psycopg.connect(settings.database_url) as conn:
        pending = (conn.execute(PENDING_SQL).fetchone() or [0])[0]
        if not pending:
            print("every embedding already has a quantized copy")
            return 0
        print(f"{pending} embeddings to quantize")
        while True:
            cur = conn.execute(BATCH_SQL, {"batch": batch})
            written = cur.rowcount or 0
            if not written:
                break
            conn.commit()
            done += written
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed else 0
            print(f"  {done}/{pending}  {rate:.0f}/s", flush=True)
    print(f"quantized {done} embeddings in {time.monotonic() - started:.1f}s")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill embedding_bits")
    parser.add_argument("--batch", type=int, default=5000)
    args = parser.parse_args()
    quantize(args.batch)


if __name__ == "__main__":
    main()
