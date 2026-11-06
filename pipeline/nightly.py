"""Worker loop: a check for a newer books release, incremental AniList
sync for both media types, then an embedding pass for anything new,
then bounded dub checks for titles that have never had one. Runs
forever, sleeping SYNC_INTERVAL_HOURS between passes; intended as the
`worker` container's entry point.

Usage:
    python -m pipeline.nightly            # loop forever
    python -m pipeline.nightly --once     # single pass (cron-friendly)
"""

import argparse
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta

import psycopg

from api.config import settings
from pipeline.backfill_dub import backfill as backfill_dub
from pipeline.backfill_dub_languages import backfill as backfill_dub_languages
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
    # Films and TV, when this install holds them: TMDB's changes feed and
    # the rolling refresh that keeps every film inside TMDB's six-month
    # cache limit, then a full read of TVmaze's index. Neither raises, and
    # both run before the embedding pass so what they add is embedded
    # tonight.
    from pipeline.sync_tmdb import nightly as tmdb_nightly
    from pipeline.sync_tvmaze import nightly as tvmaze_nightly

    tmdb_nightly()
    tvmaze_nightly()
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
    """Keep the dub columns current for newly synced titles.

    Three independent steps, each bounded and each guarded separately. A
    failure in one must not cost the others their slice, and none of them
    may cost the pass its sync or its embeddings - dub data is the
    freshness of a filter, not of the catalog.

    Both walks fetch fifty titles per AniList request, so the budgets are in
    titles rather than requests. The initial fill of either is a one-off
    run of its module; this only has to keep up with what the incremental
    sync adds.
    """
    if settings.dub_backfill_per_pass > 0:
        stale_before = None
        if settings.dub_recheck_days > 0:
            # Never-checked titles still sort ahead of rechecks, so this only
            # spends leftover budget re-asking about titles that may have
            # gained a dub since they were last seen.
            stale_before = datetime.now(UTC) - timedelta(days=settings.dub_recheck_days)
        try:
            backfill_dub(
                client,
                conn,
                limit=settings.dub_backfill_per_pass,
                stale_before=stale_before,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[nightly] dub backfill failed ({type(exc).__name__});"
                " the dubbed filter keeps its current coverage",
                flush=True,
            )

    # Independent of the English budget above, not nested under it: setting
    # dub_backfill_per_pass to 0 used to skip everything that followed,
    # which would have disabled the language walk and propagation by
    # accident. They answer different questions - english_va_count against
    # all 29 dub languages - and losing this one silently would leave every
    # non-English filter frozen at whatever a manual run last wrote.
    if settings.dub_language_per_pass > 0:
        try:
            backfill_dub_languages(client, conn, limit=settings.dub_language_per_pass)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[nightly] dub language pass failed ({type(exc).__name__});"
                " the language filters keep their current coverage",
                flush=True,
            )

    # Last, and outside both guards: one query, and it should see whatever
    # either walk just wrote. A newly measured dub reaches its franchise on
    # the same pass rather than waiting a day.
    try:
        propagate_dub(conn)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[nightly] dub propagation failed ({type(exc).__name__})",
            flush=True,
        )


# How often the sleep between passes looks for an install request. One
# minute, so switching a medium on in the settings page starts within a
# minute rather than at the next pass up to a day away; the check is one
# small SELECT.
INSTALL_POLL_SECONDS = 60.0


def watch_installs(
    run_queued, poll: float = INSTALL_POLL_SECONDS, rounds: int | None = None
) -> None:
    """Run every queued install, then look again every `poll` seconds.

    On its own thread, beside the passes rather than between them. Polling
    only between passes meant an install requested while a pass was
    embedding the books - days of work on a NAS - sat at "Queued" for all
    of it, while the settings page promised a minute (6 Nov 2026: TV and
    Films). An install and a pass do overlap now; both are mostly I/O
    (downloads, database writes) against an embedding pass that is CPU,
    and the worker's low CPU weight already yields to the rest of the box.

    rounds bounds the loop for tests; None runs for the life of the worker.
    """
    done = 0
    while rounds is None or done < rounds:
        run_queued()  # never raises
        done += 1
        if rounds is None or done < rounds:
            time.sleep(poll)


def start_install_watcher(run_queued) -> threading.Thread:
    thread = threading.Thread(
        target=watch_installs, args=(run_queued,), name="install-watcher", daemon=True
    )
    thread.start()
    return thread


def main() -> None:
    parser = argparse.ArgumentParser(description="Nightly incremental sync + embed")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args()

    from api.db import ensure_schema

    ensure_schema()
    from pipeline import dataset_install
    from pipeline.restore import catalog_is_empty

    # Decided before any seed runs, since the AniList seed is what makes
    # the catalogue non-empty.
    fresh = catalog_is_empty()
    if settings.seed_snapshot_url:
        from pipeline.restore import seed_if_empty

        try:
            seed_if_empty(settings.seed_snapshot_url)
        except Exception:
            traceback.print_exc()
            print("[nightly] snapshot seed failed; continuing with a normal sync")
    # After the AniList seed, never before: that restore runs pg_restore
    # --clean over sync_state, where install requests live, and over media.
    #
    # Fresh installs only. An existing install that upgrades must not
    # start a 3.5 GB download nobody asked for; it switches Books on in the
    # settings page, which makes the same request.
    dataset_install.recover_interrupted()
    if fresh and settings.seed_books:
        dataset_install.request("books", fresh=True)
    client = RateLimitedClient(min_interval=settings.anilist_min_interval)
    interval = settings.sync_interval_hours * 3600
    if not args.once:
        # Installs run beside the passes, so one requested mid-pass starts
        # within a minute instead of after the pass.
        start_install_watcher(dataset_install.run_queued)
    while True:
        succeeded = True
        # Books' counterpart of the AniList sync below: a newer monthly
        # release, if one is out, is queued here for the install watcher
        # (or run on the next line in --once mode). Neither raises, so an
        # install cannot cost the pass its sync.
        dataset_install.check_for_updates()
        if args.once:
            dataset_install.run_queued()
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
