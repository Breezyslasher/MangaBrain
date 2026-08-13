"""Backfill missing synopses from Jikan (MAL) for entries that have an id_mal
but no usable AniList description. Filled rows have their stale embedding
dropped, so run `python -m pipeline.embed` afterwards to re-embed them.

Usage:
    python -m pipeline.backfill_jikan
"""

import argparse

import httpx
import psycopg

from api.config import settings
from pipeline.clean import clean_description
from pipeline.client import RateLimitedClient

JIKAN_BASE = "https://api.jikan.moe/v4"

SELECT_SQL = """
    SELECT id, id_mal, media_type
    FROM media
    WHERE id_mal IS NOT NULL
      AND (description_clean IS NULL OR description_clean = '')
    ORDER BY id
"""


def backfill(client: RateLimitedClient, conn: psycopg.Connection, limit: int | None) -> int:
    rows = conn.execute(SELECT_SQL).fetchall()
    if limit is not None:
        rows = rows[:limit]
    filled = 0
    for i, (media_id, id_mal, media_type) in enumerate(rows, start=1):
        endpoint = "anime" if media_type == "ANIME" else "manga"
        try:
            data = client.request("GET", f"{JIKAN_BASE}/{endpoint}/{id_mal}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            raise
        synopsis = (data.get("data") or {}).get("synopsis")
        if not synopsis:
            continue
        conn.execute(
            "UPDATE media SET description = %s, description_clean = %s WHERE id = %s",
            (synopsis, clean_description(synopsis), media_id),
        )
        # The row was embedded from title-only text; drop the stale embedding
        # so the next embed pass re-embeds it with the new synopsis.
        conn.execute("DELETE FROM embeddings WHERE media_id = %s", (media_id,))
        filled += 1
        if i % 50 == 0:
            conn.commit()
            print(f"[backfill] {i}/{len(rows)} checked, {filled} filled")
    conn.commit()
    print(f"[backfill] done: {filled} synopses filled out of {len(rows)} candidates")
    return filled


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing synopses from Jikan")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-interval", type=float, default=settings.jikan_min_interval)
    parser.add_argument("--cache-dir", default=settings.http_cache_dir)
    args = parser.parse_args()

    client = RateLimitedClient(min_interval=args.min_interval, cache_dir=args.cache_dir)
    try:
        with psycopg.connect(settings.database_url) as conn:
            backfill(client, conn, args.limit)
    finally:
        client.close()


if __name__ == "__main__":
    main()
