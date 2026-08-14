"""Backfill missing synopses from Jikan (MAL) for entries that have an id_mal
but no usable AniList description. Filled rows have their stale embedding
dropped, so run `python -m pipeline.embed` afterwards to re-embed them.

Failures are recorded per entry in jikan_backfill_state: entries that failed
MAX_ATTEMPTS times (or 404, or have no synopsis on MAL) are not retried on
later runs, so each run only spends time on new candidates. A run also aborts
early when many requests in a row fail with server errors, which means Jikan
itself is down rather than individual entries being bad.

Usage:
    python -m pipeline.backfill_jikan
    python -m pipeline.backfill_jikan --retry-failed   # ignore recorded failures
"""

import argparse

import httpx
import psycopg

from api.config import settings
from pipeline.clean import clean_description
from pipeline.client import RateLimitedClient

JIKAN_BASE = "https://api.jikan.moe/v4"

MAX_ATTEMPTS = 3
CONSECUTIVE_5XX_ABORT = 25

# Created by the script itself (not only schema.sql) so existing installs
# pick it up without a manual migration.
STATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS jikan_backfill_state (
        media_id     INTEGER PRIMARY KEY,
        attempts     INTEGER NOT NULL DEFAULT 0,
        last_status  TEXT,
        last_attempt TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

SELECT_SQL = """
    SELECT m.id, m.id_mal, m.media_type
    FROM media m
    LEFT JOIN jikan_backfill_state s ON s.media_id = m.id
    WHERE m.id_mal IS NOT NULL
      AND (m.description_clean IS NULL OR m.description_clean = '')
      AND (s.media_id IS NULL OR s.attempts < %(max_attempts)s)
    ORDER BY m.id
"""

RECORD_SQL = """
    INSERT INTO jikan_backfill_state (media_id, attempts, last_status, last_attempt)
    VALUES (%(id)s, %(attempts)s, %(status)s, now())
    ON CONFLICT (media_id) DO UPDATE SET
        attempts = GREATEST(jikan_backfill_state.attempts + 1, EXCLUDED.attempts),
        last_status = EXCLUDED.last_status,
        last_attempt = now()
"""


def _record(conn: psycopg.Connection, media_id: int, status: str, permanent: bool) -> None:
    conn.execute(
        RECORD_SQL,
        {"id": media_id, "attempts": MAX_ATTEMPTS if permanent else 1, "status": status},
    )


def backfill(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    limit: int | None,
    retry_failed: bool = False,
) -> int:
    conn.execute(STATE_TABLE_SQL)
    conn.commit()
    max_attempts = 10_000_000 if retry_failed else MAX_ATTEMPTS
    rows = conn.execute(SELECT_SQL, {"max_attempts": max_attempts}).fetchall()
    if limit is not None:
        rows = rows[:limit]
    filled = 0
    skipped = 0
    consecutive_5xx = 0
    for i, (media_id, id_mal, media_type) in enumerate(rows, start=1):
        endpoint = "anime" if media_type == "ANIME" else "manga"
        try:
            data = client.request("GET", f"{JIKAN_BASE}/{endpoint}/{id_mal}")
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            skipped += 1
            if status_code == 404:
                # Removed from MAL: permanent, never retried.
                _record(conn, media_id, "404", permanent=True)
            else:
                _record(conn, media_id, f"http_{status_code}", permanent=False)
                print(f"[backfill] skipping mal {endpoint} {id_mal} (HTTP {status_code})")
            if status_code >= 500:
                consecutive_5xx += 1
                if consecutive_5xx >= CONSECUTIVE_5XX_ABORT:
                    conn.commit()
                    print(
                        f"[backfill] {consecutive_5xx} server errors in a row:"
                        " Jikan appears to be down, aborting this run."
                        " Rerun later; nothing was lost."
                    )
                    break
            continue
        except httpx.TransportError as exc:
            skipped += 1
            _record(conn, media_id, type(exc).__name__, permanent=False)
            print(f"[backfill] skipping mal {endpoint} {id_mal} ({type(exc).__name__})")
            continue
        consecutive_5xx = 0
        synopsis = (data.get("data") or {}).get("synopsis")
        if not synopsis:
            # Exists on MAL but has no synopsis: permanent, never retried.
            _record(conn, media_id, "no_synopsis", permanent=True)
            continue
        conn.execute(
            "UPDATE media SET description = %s, description_clean = %s WHERE id = %s",
            (synopsis, clean_description(synopsis), media_id),
        )
        # The row was embedded from title-only text; drop the stale embedding
        # so the next embed pass re-embeds it with the new synopsis.
        conn.execute("DELETE FROM embeddings WHERE media_id = %s", (media_id,))
        conn.execute("DELETE FROM jikan_backfill_state WHERE media_id = %s", (media_id,))
        filled += 1
        if i % 50 == 0:
            conn.commit()
            print(f"[backfill] {i}/{len(rows)} checked, {filled} filled")
    conn.commit()
    print(
        f"[backfill] done: {filled} synopses filled out of {len(rows)} candidates"
        f" ({skipped} skipped)"
    )
    return filled


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing synopses from Jikan")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="retry entries that previously failed or were marked permanent",
    )
    parser.add_argument("--min-interval", type=float, default=settings.jikan_min_interval)
    parser.add_argument("--cache-dir", default=settings.http_cache_dir)
    args = parser.parse_args()

    # Low retry cap: entries that 504 through Jikan usually do so persistently,
    # and skipping fast beats a minute of backoff per bad entry.
    client = RateLimitedClient(
        min_interval=args.min_interval, cache_dir=args.cache_dir, max_retries=2
    )
    try:
        with psycopg.connect(settings.database_url) as conn:
            backfill(client, conn, args.limit, retry_failed=args.retry_failed)
    finally:
        client.close()


if __name__ == "__main__":
    main()
