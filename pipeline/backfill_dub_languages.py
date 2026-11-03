"""Measure every dub language per entry from AniList, in one request each.

AniList was only ever asked about English. The walk requested
voiceActors(language: ENGLISH) and nothing else, which is why every
non-English dub in the database arrived via ANN - and ANN matches on title,
resolving 31.8% of the catalog with 9,785 rows having no ANN entry at all, so
its non-English coverage is capped there permanently.

The shape that works is to NOT filter. `voiceActors` without a language
argument returns every voice actor for the character, and Staff.languageV2
carries each one's language - which is the field the language: filter matches
on internally. Measured against the filtered queries per language and per
title: exact agreement on all of them.

That took two wrong turns worth recording, because both failed quietly.

Aliasing nine filtered fields into one request looked free and returns the
same list under every alias: Cowboy Bebop came back english=5, french=5,
hebrew=5 and so on, identical for all nine, while three titles with 20 to 66
English voice actors returned nothing at all. No error, plausible shape.

Then a pass per language was built on the conclusion that one request cannot
carry several languages. It can; the alias failure was specific to the
language: argument being ignored, and said nothing about reading the language
off the actor. That design cost ten times the requests - ~4,440 against ~444 -
and would have missed languages outright, because StaffLanguage has ten
values while one unfiltered request over five titles returned thirteen.
Chinese, Hindi and Indonesian are not in the enum: Jujutsu Kaisen credits 18
Indonesian and 5 Hindi voice actors, Koe no Katachi 8 Chinese.

So the pass is ~444 requests for the whole catalog and every language at
once, about an hour at the measured throughput, and it is not limited to a
fixed language list.

Provenance is kept, not merged. measured_dub_languages holds what AniList
credited for the id; dub_languages holds ANN's title match, which can in
principle match the wrong show. Either satisfies a dub_lang query, and
dub_source reports which answered.

Usage:
    python -m pipeline.backfill_dub_languages
    python -m pipeline.backfill_dub_languages --limit 500
    python -m pipeline.backfill_dub_languages --max-seconds 1800
    python -m pipeline.backfill_dub_languages --recheck-older-than 90
"""

import argparse
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
from psycopg.rows import dict_row

from api.config import settings
from pipeline.backfill_dub import (
    ANILIST_URL,
    CAST_CHUNK,
    CHARACTERS_PER_PAGE,
    NullVoiceActors,
)
from pipeline.backfill_dub_ann import NATIVE_LANGS
from pipeline.client import RateLimitedClient
from pipeline.dub_language_codes import code_for
from pipeline.embed import progress_line

# A second walk doubles the requests and halves each one's share of the rate
# limit. Distinct from the English walk's lock: the two are independent
# passes over different columns and may legitimately overlap.
LANGUAGE_LOCK_KEY = 727274

# No language argument, and languageV2 read off each voice actor instead.
#
# node is load-bearing. Without it AniList resolves voiceActors to null on
# every edge rather than erroring, which reads as "no cast at all" for the
# whole catalog - the bug that cost the English walk twelve hours a pass
# before it was found. A guard below raises instead of recording a zero.
CAST_QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      countryOfOrigin
      characters(perPage: CHARACTERS, sort: ROLE) {
        edges {
          node { id }
          voiceActors { id languageV2 }
        }
      }
    }
  }
}
""".replace("CHARACTERS", str(CHARACTERS_PER_PAGE))

# Unchecked rows first, newest first within them, so a freshly synced title
# gets its languages early rather than waiting out the backlog.
SELECT_SQL = """
    SELECT m.id
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND (m.measured_dub_checked_at IS NULL
           OR m.measured_dub_checked_at < %(stale_before)s)
    ORDER BY m.measured_dub_checked_at ASC NULLS FIRST, m.id DESC
"""

UPDATE_SQL = """
    UPDATE media
    SET measured_dub_languages = %(langs)s,
        measured_dub_checked_at = now()
    WHERE id = %(id)s
"""


def languages_for(media: dict) -> tuple[list[str], list[str]]:
    """Dub language codes for one title, and any unmapped languageV2 values.

    The title's own language is excluded: a Japanese cast on a Japanese show
    is the original, not a dub. Which language that is comes from
    countryOfOrigin, because a Japanese cast on a Chinese donghua or a Korean
    webtoon adaptation genuinely IS a dub - the same rule the ANN pass uses.
    """
    edges = (media.get("characters") or {}).get("edges") or []
    native = NATIVE_LANGS.get((media.get("countryOfOrigin") or "").upper(), set())
    codes: set[str] = set()
    unknown: set[str] = set()
    for edge in edges:
        actors = edge.get("voiceActors")
        if actors is None:
            raise NullVoiceActors("voiceActors resolved to null; the query lost node")
        for actor in actors:
            code, known = code_for(actor.get("languageV2"))
            if not known:
                unknown.add(str(actor.get("languageV2")))
            if code not in native:
                codes.add(code)
    return sorted(codes), sorted(unknown)


def backfill(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    limit: int | None = None,
    max_seconds: float | None = None,
    stale_before: datetime | None = None,
) -> tuple[int, Counter]:
    """Returns (rows checked, languages seen across them)."""
    # Aliased and unwrapped for either row factory: this module connects with
    # dict_row, so a positional index would raise instead of reading the lock.
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS held", (LANGUAGE_LOCK_KEY,)).fetchone()
    held = row["held"] if isinstance(row, dict) else (row[0] if row else False)
    if not held:
        print(
            "[dub-lang] another language walk is already running against this"
            " database; leaving it to finish rather than duplicating its requests",
            flush=True,
        )
        return 0, Counter()
    try:
        return _walk(client, conn, limit, max_seconds, stale_before)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LANGUAGE_LOCK_KEY,))


def _walk(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    limit: int | None,
    max_seconds: float | None,
    stale_before: datetime | None,
) -> tuple[int, Counter]:
    rows = conn.execute(
        SELECT_SQL,
        {"stale_before": stale_before or datetime.fromtimestamp(0, UTC)},
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    ids = [r["id"] if isinstance(r, dict) else r[0] for r in rows]
    if not ids:
        print("[dub-lang] nothing left to check", flush=True)
        return 0, Counter()

    print(f"[dub-lang] {len(ids)} rows to measure, every language per request", flush=True)
    deadline = time.monotonic() + max_seconds if max_seconds else None
    checked = 0
    with_any = 0
    seen: Counter = Counter()
    unmapped: Counter = Counter()
    started = time.monotonic()

    for start in range(0, len(ids), CAST_CHUNK):
        if deadline is not None and time.monotonic() >= deadline:
            print(
                f"[dub-lang] time budget reached at {checked}/{len(ids)};"
                " the next run continues from here",
                flush=True,
            )
            break
        chunk = ids[start : start + CAST_CHUNK]
        try:
            payload = client.request(
                "POST",
                ANILIST_URL,
                # perPage must equal the chunk, or the response is truncated
                # without saying so.
                json_body={"query": CAST_QUERY, "variables": {"ids": chunk, "perPage": len(chunk)}},
            )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            print(
                f"[dub-lang] chunk of {len(chunk)} failed ({type(exc).__name__});"
                " it stays unchecked and will be retried",
                flush=True,
            )
            continue
        if payload.get("errors"):
            print("[dub-lang] chunk returned GraphQL errors, skipping", flush=True)
            continue

        page = (payload.get("data") or {}).get("Page") or {}
        returned = {m["id"]: m for m in page.get("media") or []}
        for media_id in chunk:
            media = returned.get(media_id)
            if media is None:
                # Not an anime any more, or deleted upstream. Marked checked
                # so the pass terminates instead of retrying it forever.
                conn.execute(UPDATE_SQL, {"id": media_id, "langs": []})
                continue
            codes, unknown = languages_for(media)
            conn.execute(UPDATE_SQL, {"id": media_id, "langs": codes})
            checked += 1
            with_any += bool(codes)
            seen.update(codes)
            unmapped.update(unknown)
        conn.commit()

        done = min(start + CAST_CHUNK, len(ids))
        pct = 100.0 * with_any / checked if checked else 0.0
        progress = progress_line(done, len(ids), time.monotonic() - started, label="dub-lang")
        print(f"{progress} - {with_any} with any dub ({pct:.0f}%)", flush=True)

    conn.commit()
    print(
        f"[dub-lang] done: {checked} measured, {with_any} credit a dub voice actor",
        flush=True,
    )
    for code, count in seen.most_common():
        print(f"[dub-lang]   {code:<8} {count:>6}", flush=True)
    if unmapped:
        # Reported, never dropped: an unmapped language is missing from the
        # table in pipeline.dub_language_codes, not from AniList.
        print(
            "[dub-lang] languageV2 values with no mapping, stored as slugs"
            " - add them to LANGUAGE_V2_CODES:",
            flush=True,
        )
        for name, count in unmapped.most_common(20):
            print(f"[dub-lang]   {name!r} x{count}", flush=True)
    return checked, seen


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure dub languages from AniList")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many titles")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="stop after this long; the next run continues where this stopped",
    )
    parser.add_argument(
        "--recheck-older-than",
        type=int,
        default=None,
        metavar="DAYS",
        help="also re-measure titles last measured more than DAYS ago",
    )
    parser.add_argument("--min-interval", type=float, default=settings.anilist_min_interval)
    parser.add_argument("--cache-dir", default="")
    args = parser.parse_args()

    stale_before = None
    if args.recheck_older_than is not None:
        stale_before = datetime.now(UTC) - timedelta(days=args.recheck_older_than)

    client = RateLimitedClient(min_interval=args.min_interval, cache_dir=args.cache_dir)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        backfill(
            client,
            conn,
            limit=args.limit,
            max_seconds=args.max_seconds,
            stale_before=stale_before,
        )


if __name__ == "__main__":
    main()
