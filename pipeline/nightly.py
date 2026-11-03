"""Worker loop: incremental AniList sync for both media types, then an
embedding pass for anything new, then a bounded English-dub check for titles
that have never had one. Runs forever, sleeping SYNC_INTERVAL_HOURS between
passes; intended as the `worker` container's entry point.

Usage:
    python -m pipeline.nightly            # loop forever
    python -m pipeline.nightly --once     # single pass (cron-friendly)
"""

import argparse
import time
import traceback
from datetime import UTC, datetime, timedelta

import psycopg

from api.config import settings
from pipeline.backfill_dub import backfill as backfill_dub
from pipeline.client import RateLimitedClient
from pipeline.embed import embed_missing, prune_stale
from pipeline.propagate_dub import propagate as propagate_dub
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
    # Embedding is local work over rows that are already in the database, so
    # an upstream outage (AniList returning 403/5xx) must not stall it. A
    # failed sync is remembered and re-raised after the embedding pass, so
    # the caller still schedules its retry.
    sync_error: Exception | None = None
    with psycopg.connect(settings.database_url) as conn:
        empty = conn.execute("SELECT 1 FROM media LIMIT 1").fetchone() is None
        if empty:
            # Fresh database and no snapshot seeded it: a 7-day incremental
            # would leave the catalog nearly empty, so run the full scan.
            # Nothing to embed if this fails, so the error propagates.
            print("[nightly] catalog is empty; running the full sync")
            full_sync(client, conn, None)
            embed_missing()
            prune_stale()
            _dub_pass(client, conn)
            return
        try:
            for media_type in ("ANIME", "MANGA"):
                key = f"anilist_last_sync_{media_type.lower()}"
                state = get_state(conn, key) or {}
                since = int(state.get("ts", time.time() - DEFAULT_LOOKBACK_SECONDS))
                started = int(time.time())
                _, hit_cap = incremental_sync(
                    client, conn, media_type, max(0, since - OVERLAP_SECONDS)
                )
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
        except Exception as exc:  # noqa: BLE001
            sync_error = exc
            print(
                f"[nightly] sync failed ({type(exc).__name__}); the catalog keeps its current"
                " contents and the embedding pass runs anyway",
                flush=True,
            )
    # embed_missing returning means every media row has a current-version
    # embedding, so retiring older versions cannot leave a coverage gap.
    embed_missing()
    prune_stale()
    if sync_error is None:
        # Skipped after a failed sync: the dub walk shares the AniList client,
        # and if AniList just refused the sync it will refuse this too.
        with psycopg.connect(settings.database_url) as conn:
            _dub_pass(client, conn)
    if sync_error is not None:
        # Surfaces the traceback and puts the loop on its shorter retry.
        raise sync_error


def _dub_pass(client: RateLimitedClient, conn: psycopg.Connection) -> None:
    """Check a bounded slice of titles for an English dub.

    Bounded because it is one AniList request per title: the initial fill is
    a one-off `python -m pipeline.backfill_dub`, and this only keeps up with
    newly synced entries. A failure here must not cost the pass its sync or
    its embeddings, so it is caught and reported.
    """
    if settings.dub_backfill_per_pass <= 0:
        return
    stale_before = None
    if settings.dub_recheck_days > 0:
        # Never-checked titles still sort ahead of rechecks, so this only
        # spends leftover budget re-asking about titles that may have gained
        # a dub since they were last seen.
        stale_before = datetime.now(UTC) - timedelta(days=settings.dub_recheck_days)
    try:
        backfill_dub(
            client,
            conn,
            limit=settings.dub_backfill_per_pass,
            stale_before=stale_before,
        )
        # One query, so it runs every pass: a newly measured dub should reach
        # its franchise without waiting for a separate job.
        propagate_dub(conn)
    except Exception as exc:  # noqa: BLE001 - freshness of a filter, not of the catalog
        print(
            f"[nightly] dub backfill failed ({type(exc).__name__});"
            " the dubbed filter keeps its current coverage",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Nightly incremental sync + embed")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args()

    from api.db import ensure_schema

    ensure_schema()
    if settings.seed_snapshot_url:
        from pipeline.restore import seed_if_empty

        try:
            seed_if_empty(settings.seed_snapshot_url)
        except Exception:
            traceback.print_exc()
            print("[nightly] snapshot seed failed; continuing with a normal sync")
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
