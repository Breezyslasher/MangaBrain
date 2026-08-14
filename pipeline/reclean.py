"""Recompute description_clean from the stored raw descriptions.

No API calls: this re-runs clean_description over the local catalog. Useful
after the cleaning rules change (e.g. new markup stripped). Rows whose
cleaned text changed have their embedding dropped, so run
`python -m pipeline.embed` afterwards to re-embed them.

Usage:
    python -m pipeline.reclean
"""

import psycopg

from api.config import settings
from pipeline.clean import clean_description

BATCH_SIZE = 1000


def reclean(conn: psycopg.Connection) -> int:
    changed = 0
    scanned = 0
    last_id = 0
    while True:
        rows = conn.execute(
            "SELECT id, description, description_clean FROM media"
            " WHERE id > %s ORDER BY id LIMIT %s",
            (last_id, BATCH_SIZE),
        ).fetchall()
        if not rows:
            break
        for media_id, raw, old_clean in rows:
            new_clean = clean_description(raw)
            if new_clean != (old_clean or ""):
                conn.execute(
                    "UPDATE media SET description_clean = %s WHERE id = %s",
                    (new_clean, media_id),
                )
                conn.execute("DELETE FROM embeddings WHERE media_id = %s", (media_id,))
                changed += 1
        scanned += len(rows)
        last_id = rows[-1][0]
        conn.commit()
        print(f"[reclean] {scanned} rows scanned, {changed} updated")
    print(f"[reclean] done: {changed} descriptions re-cleaned; run pipeline.embed to re-embed")
    return changed


def main() -> None:
    with psycopg.connect(settings.database_url) as conn:
        reclean(conn)


if __name__ == "__main__":
    main()
