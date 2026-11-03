"""Backfill the English dub signal from AniList's cast-by-language data.

AniList has no dub field. It does record each character's voice actors
tagged by language, so "an English voice actor is credited" stands in for
"an English dub exists". Measured coverage (pipeline.probe_dub_coverage):
100% of the most popular titles, ~93% at rank 1000, ~60-67% at rank
3000-5000, and 0% in the least popular strata, where the entries have no cast
recorded at all.

That asymmetry is the whole design constraint. A positive is reliable; a zero
is not. Berserk 2 (2017) returns 21 characters and no English voice actors
despite having a dub, so english_va_count = 0 must never be served as "sub
only" - only english_va_count > 0 as "has an English dub". NULL (not yet
checked) stays distinct from 0 (checked, none found) for exactly this reason.

Casts are fetched a page of titles at a time. This was one request per
title - 22,167 of them against a two-second floor, so 12.3 hours and far
too long for CI - on the belief that AniList would not populate a nested
connection across a page of parents. That was wrong. The probe behind it
(pipeline.probe_paged_cast) omitted `node { id }`, without which
voiceActors resolves to null on every edge, and the fix changed both the
paging and the node selection at once, so the per-title shape got credit
that belonged to the node. Measured since: a page of fifty returns
byte-identical cast data to fifty single requests, and omitting node
zeroes the single-title shape too. The pass is ceil(N/CAST_CHUNK)
requests, about fifteen minutes.

The walk is resumable: every checked row gets dub_checked_at, so a rerun
picks up where it stopped, and failures are recorded per entry in
dub_backfill_state so one bad id cannot stall the pass. A chunk that
fails as a whole is retried one title at a time, so a single bad id
cannot cost its forty-nine neighbours their turn.

Only ANIME rows are considered. Manga has no dub.

Usage:
    python -m pipeline.backfill_dub
    python -m pipeline.backfill_dub --limit 500
    python -m pipeline.backfill_dub --recheck-older-than 90
    python -m pipeline.backfill_dub --retry-failed
"""

import argparse
import time
from datetime import UTC, datetime, timedelta

import httpx
import psycopg

from api.config import settings
from pipeline.client import RateLimitedClient
from pipeline.embed import progress_line

ANILIST_URL = "https://graphql.anilist.co"

CHARACTERS_PER_PAGE = 25
MAX_ATTEMPTS = 3
CONSECUTIVE_5XX_ABORT = 25

# Titles per cast request. Measured against AniList rather than assumed: see
# pipeline.probe_paged_cast, which checks that a batch this size comes back
# whole and with non-null casts. Too large and the response is truncated or
# the query exceeds AniList's complexity limit, either of which would skip
# titles while looking like success.
CAST_CHUNK = 50

# A second walk started while the first is running does the same work twice
# and halves the share of AniList's rate limit each one gets, so both crawl
# through 429 backoff for no extra progress. Re-running after a container
# restart that did not actually kill the first one produces exactly this.
# Observed in the wild as two interleaved progress lines, one counting to
# 22167 and one to 11717, the second having computed its candidate list once
# the first was 10450 titles in. That was the per-title walk, which ran for
# twelve hours and so had a wide window in which to be started twice; the
# paged walk takes minutes, but the lock is a single query and the failure
# has actually happened.
#
# A session-scoped advisory lock makes the second invocation say so and exit
# instead. Distinct from the schema lock in api/db.py.
WALK_LOCK_KEY = 727273

# node is load-bearing. Without a node selection AniList resolves voiceActors
# to null on every edge instead of erroring, which reads as "no English cast"
# for the entire catalog - and it does so whether one title is requested or
# fifty, which is what made the original diagnosis go wrong. A guard below
# fails loudly if that ever regresses.
_CAST_FIELDS = """
      edges {
        node { id }
        voiceActors(language: ENGLISH) { id }
        voiceActorRoles(language: ENGLISH) { dubGroup }
      }
"""

# Many titles per request. perPage is passed rather than fixed so it always
# matches the chunk actually sent: a perPage below the id count silently
# truncates the response, which would skip titles while reporting success.
CAST_PAGE_QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      characters(perPage: CHARACTERS_PER_PAGE, sort: ROLE) {
CAST_FIELDS
      }
    }
  }
}
""".replace("CHARACTERS_PER_PAGE", str(CHARACTERS_PER_PAGE)).replace("CAST_FIELDS", _CAST_FIELDS)

# Kept for the per-title retry of a chunk that failed as a whole.
CAST_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    characters(perPage: CHARACTERS_PER_PAGE, sort: ROLE) {
CAST_FIELDS
    }
  }
}
""".replace("CHARACTERS_PER_PAGE", str(CHARACTERS_PER_PAGE)).replace("CAST_FIELDS", _CAST_FIELDS)

STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dub_backfill_state (
    media_id        INTEGER PRIMARY KEY REFERENCES media (id) ON DELETE CASCADE,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_status     TEXT,
    last_attempt    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Unchecked rows first, then the stalest, so an interrupted run resumes
# instead of restarting and a recheck pass works oldest-first.
#
# The id DESC tiebreak matters more than it looks. Every unchecked row has a
# NULL dub_checked_at, so they all tie on the first key and the tiebreak picks
# the order the catalog is filled in. Ascending by id walks the oldest entries
# first, which starves newly synced titles of a dub flag for as long as the
# backlog lasts - at a few hundred titles per nightly pass, weeks. AniList ids
# increase over time, so descending checks the newest titles first, which is
# also the half a dub filter is most likely to be pointed at.
SELECT_SQL = """
    SELECT m.id
    FROM media m
    LEFT JOIN dub_backfill_state s ON s.media_id = m.id
    WHERE m.media_type = 'ANIME'
      AND (m.dub_checked_at IS NULL OR m.dub_checked_at < %(stale_before)s)
      AND COALESCE(s.attempts, 0) < %(max_attempts)s
    ORDER BY m.dub_checked_at ASC NULLS FIRST, m.id DESC
"""

UPDATE_SQL = """
    UPDATE media
    SET english_va_count = %(english)s,
        cast_character_count = %(characters)s,
        dub_groups = %(groups)s,
        dub_checked_at = now()
    WHERE id = %(id)s
"""

RECORD_SQL = """
    INSERT INTO dub_backfill_state (media_id, attempts, last_status, last_attempt)
    VALUES (%(id)s, %(attempts)s, %(status)s, now())
    ON CONFLICT (media_id) DO UPDATE SET
        attempts = GREATEST(dub_backfill_state.attempts + 1, EXCLUDED.attempts),
        last_status = EXCLUDED.last_status,
        last_attempt = EXCLUDED.last_attempt
"""


class NullVoiceActors(RuntimeError):
    """AniList returned null voice actors despite a node selection.

    This is the failure mode that produced a catalog-wide false zero while
    the probe was being written, so it is an exception rather than a count.
    """


def parse_cast(media: dict) -> tuple[int, int, list[str]]:
    """Return (english_va_count, cast_character_count, dub_groups)."""
    edges = media["characters"]["edges"]
    actors: set[int] = set()
    groups: set[str] = set()
    for edge in edges:
        if edge.get("voiceActors") is None:
            raise NullVoiceActors("voiceActors resolved to null; the query lost node")
        for actor in edge["voiceActors"]:
            actors.add(actor["id"])
        for role in edge.get("voiceActorRoles") or []:
            if role.get("dubGroup"):
                groups.add(role["dubGroup"])
    return len(actors), len(edges), sorted(groups)


def _record(conn: psycopg.Connection, media_id: int, status: str, permanent: bool) -> None:
    conn.execute(
        RECORD_SQL,
        {"id": media_id, "attempts": MAX_ATTEMPTS if permanent else 1, "status": status},
    )


def _walk_singly(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    ids: list[int],
    apply_media,
) -> tuple[int, int]:
    """Retry a failed chunk one title at a time. Returns (checked, skipped).

    A chunk request covers fifty titles, so a failure that is really about
    one id would otherwise deny the other forty-nine. Falling back per title
    costs at most CAST_CHUNK extra requests and only on a chunk that already
    failed.
    """
    checked = skipped = 0
    for media_id in ids:
        try:
            payload = client.request(
                "POST",
                ANILIST_URL,
                json_body={"query": CAST_QUERY, "variables": {"id": media_id}},
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            skipped += 1
            if status_code == 404:
                _record(conn, media_id, "404", permanent=True)
            else:
                _record(conn, media_id, f"http_{status_code}", permanent=False)
            continue
        except httpx.TransportError as exc:
            skipped += 1
            _record(conn, media_id, type(exc).__name__, permanent=False)
            continue

        if payload.get("errors"):
            skipped += 1
            _record(conn, media_id, "graphql_error", permanent=False)
            continue
        media = (payload.get("data") or {}).get("Media")
        if not media:
            _record(conn, media_id, "no_media", permanent=True)
            skipped += 1
            continue
        apply_media(media)
        checked += 1
    conn.commit()
    return checked, skipped


def backfill(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    limit: int | None = None,
    retry_failed: bool = False,
    stale_before: datetime | None = None,
) -> int:
    held = conn.execute("SELECT pg_try_advisory_lock(%s)", (WALK_LOCK_KEY,)).fetchone()[0]
    if not held:
        print(
            "[dub] another dub walk is already running against this database;"
            " leaving it to finish rather than duplicating its requests",
            flush=True,
        )
        return 0
    try:
        return _walk(client, conn, limit, retry_failed, stale_before)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (WALK_LOCK_KEY,))


def _walk(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    limit: int | None,
    retry_failed: bool,
    stale_before: datetime | None,
) -> int:
    conn.execute(STATE_TABLE_SQL)
    conn.commit()
    max_attempts = 10_000_000 if retry_failed else MAX_ATTEMPTS
    # A bound timestamp rather than an interpolated interval: with no
    # --recheck-older-than, nothing is older than the epoch, so only rows
    # that have never been checked are selected.
    rows = conn.execute(
        SELECT_SQL,
        {
            "max_attempts": max_attempts,
            "stale_before": stale_before or datetime.fromtimestamp(0, UTC),
        },
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]

    checked = dubbed = skipped = 0
    consecutive_5xx = 0
    started = time.monotonic()
    ids = [row[0] for row in rows]

    def apply_media(media: dict) -> None:
        nonlocal checked, dubbed
        english, characters, groups = parse_cast(media)
        conn.execute(
            UPDATE_SQL,
            {
                "id": media["id"],
                "english": english,
                "characters": characters,
                "groups": groups,
            },
        )
        conn.execute("DELETE FROM dub_backfill_state WHERE media_id = %s", (media["id"],))
        checked += 1
        dubbed += english > 0

    for start_at in range(0, len(ids), CAST_CHUNK):
        chunk = ids[start_at : start_at + CAST_CHUNK]
        try:
            payload = client.request(
                "POST",
                ANILIST_URL,
                json_body={
                    "query": CAST_PAGE_QUERY,
                    # perPage must equal the chunk or the response is
                    # truncated without saying so.
                    "variables": {"ids": chunk, "perPage": len(chunk)},
                },
            )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code is not None and status_code >= 500:
                consecutive_5xx += 1
                if consecutive_5xx >= CONSECUTIVE_5XX_ABORT:
                    conn.commit()
                    print(
                        f"[dub] {consecutive_5xx} server errors in a row:"
                        " AniList appears to be down, aborting this run."
                        " Rerun later; nothing was lost.",
                        flush=True,
                    )
                    break
            # One bad id must not cost the rest of its chunk their turn, so
            # the chunk is retried a title at a time and only the ids that
            # actually fail are recorded as failures.
            reason = f"HTTP {status_code}" if status_code is not None else type(exc).__name__
            print(
                f"[dub] chunk of {len(chunk)} failed ({reason}); retrying it one title at a time",
                flush=True,
            )
            single_checked, single_skipped = _walk_singly(client, conn, chunk, apply_media)
            skipped += single_skipped
            if single_checked == 0 and single_skipped == len(chunk):
                consecutive_5xx += 1
            continue
        consecutive_5xx = 0

        if payload.get("errors"):
            print(
                f"[dub] chunk of {len(chunk)} returned GraphQL errors;"
                " retrying it one title at a time",
                flush=True,
            )
            _, single_skipped = _walk_singly(client, conn, chunk, apply_media)
            skipped += single_skipped
            continue

        page = (payload.get("data") or {}).get("Page") or {}
        returned = {m["id"]: m for m in page.get("media") or []}
        for media_id in chunk:
            media = returned.get(media_id)
            if media is None:
                # Requested and not returned: no longer an anime, or deleted
                # upstream. Never retried.
                _record(conn, media_id, "no_media", permanent=True)
                skipped += 1
                continue
            apply_media(media)
        conn.commit()

        done = min(start_at + CAST_CHUNK, len(ids))
        pct = 100.0 * dubbed / checked if checked else 0.0
        progress = progress_line(done, len(ids), time.monotonic() - started, label="dub")
        print(f"{progress} - {dubbed} with an English cast ({pct:.0f}%)", flush=True)

    conn.commit()
    print(
        f"[dub] done: {checked} checked of {len(ids)} candidates,"
        f" {dubbed} credit an English voice actor ({skipped} skipped)",
        flush=True,
    )
    return checked


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill the English dub signal from AniList")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many titles")
    parser.add_argument(
        "--retry-failed", action="store_true", help="ignore recorded per-entry failures"
    )
    parser.add_argument(
        "--recheck-older-than",
        type=int,
        default=None,
        metavar="DAYS",
        help="also recheck titles last checked more than DAYS ago",
    )
    parser.add_argument("--min-interval", type=float, default=settings.anilist_min_interval)
    parser.add_argument("--cache-dir", default="")
    args = parser.parse_args()

    stale_before = None
    if args.recheck_older_than is not None:
        stale_before = datetime.now(UTC) - timedelta(days=args.recheck_older_than)

    client = RateLimitedClient(min_interval=args.min_interval, cache_dir=args.cache_dir)
    try:
        with psycopg.connect(settings.database_url) as conn:
            backfill(
                client,
                conn,
                limit=args.limit,
                retry_failed=args.retry_failed,
                stale_before=stale_before,
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
