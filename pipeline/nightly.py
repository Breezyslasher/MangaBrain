"""Worker loop: incremental AniList sync for both media types, then an
embedding pass for anything new. Runs forever, sleeping SYNC_INTERVAL_HOURS
between passes; intended as the `worker` container's entry point.

Usage:
    python -m pipeline.nightly            # loop forever
    python -m pipeline.nightly --once     # single pass (cron-friendly)
"""

import argparse
import time
import traceback

import psycopg

from api.config import settings
from pipeline.client import RateLimitedClient
from pipeline.embed import embed_missing
from pipeline.sync_anilist import full_sync, get_state, incremental_sync, set_state

# Overlap re-syncs a little history so entries updated while a pass was
# running are not missed. Default lookback bounds the very first pass.
OVERLAP_SECONDS = 3600
DEFAULT_LOOKBACK_SECONDS = 7 * 86400

# After a failed pass, retry within the hour instead of waiting the full
# interval: a transient outage should not cost a day of freshness.
FAILURE_RETRY_SECONDS = 3600.0


def run_once(client: RateLimitedClient) -> None:
    capped: list[str] = []
    with psycopg.connect(settings.database_url) as conn:
        for media_type in ("ANIME", "MANGA"):
            key = f"anilist_last_sync_{media_type.lower()}"
            state = get_state(conn, key) or {}
            since = int(state.get("ts", time.time() - DEFAULT_LOOKBACK_SECONDS))
            started = int(time.time())
            _, hit_cap = incremental_sync(client, conn, media_type, max(0, since - OVERLAP_SECONDS))
            if hit_cap:
                capped.append(media_type)
            set_state(conn, key, {"ts": started})
            conn.commit()
        if capped:
            # More entries changed than the offset-capped incremental walk
            # can reach; self-heal with a full id scan (resumable, and the
            # upserts skip unchanged rows cheaply).
            target = None if len(capped) == 2 else capped[0]
            print(f"[nightly] offset cap hit for {capped}; running full scan to catch up")
            full_sync(client, conn, target)
    embed_missing()


def main() -> None:
    parser = argparse.ArgumentParser(description="Nightly incremental sync + embed")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args()

    from api.db import ensure_schema

    ensure_schema()
    client = RateLimitedClient(min_interval=settings.anilist_min_interval)
    interval = settings.sync_interval_hours * 3600
    while True:
        succeeded = True
        try:
            run_once(client)
        except Exception:
            traceback.print_exc()
            succeeded = False
        if args.once:
            break
        delay = interval if succeeded else min(FAILURE_RETRY_SECONDS, interval)
        print(f"[nightly] sleeping {delay / 3600:.1f}h until next pass")
        time.sleep(delay)


if __name__ == "__main__":
    main()
