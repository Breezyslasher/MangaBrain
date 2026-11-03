"""Measure dub languages per entry from AniList, one language per pass.

AniList was only ever asked about English. The walk requests
voiceActors(language: ENGLISH) and nothing else, which is why every
non-English dub in the database arrived via ANN - and ANN resolves 31.8% of
the catalog by title match, with 9,785 titles having no ANN entry at all. Its
non-English coverage is capped there, permanently.

AniList needs no matching. It answers for the id it was handed, so it reaches
all 22,667 rows. Measured on five titles with documented casts, its
non-English data is not an afterthought either: SPANISH returned more voice
actor credits than ENGLISH (154 against 144), with PORTUGUESE at 124 and
ITALIAN and GERMAN at 111 each.

StaffLanguage carries ten values, introspected rather than recalled:
JAPANESE, ENGLISH, KOREAN, ITALIAN, SPANISH, PORTUGUESE, FRENCH, GERMAN,
HEBREW, HUNGARIAN.

One language per request, not nine aliased into one. Aliasing looked free -
same request count, every language in one response - and it silently returns
the wrong data: AniList ignores the language argument on aliased fields and
serves one list under every alias. Cowboy Bebop came back as english=5,
french=5, hebrew=5 and so on, identical for all nine, while three other
titles returned nothing at all despite having 20 to 66 English voice actors.
No error, plausible shape. pipeline.probe_dub_languages pins both the failure
and the per-language shape that works.

So the cost is ceil(N/CAST_CHUNK) requests per language - about 444, or an
hour at the measured throughput, times the languages asked for. That is
affordable only because the pass is incremental and time-boxed: each run
takes a slice and the next continues it, exactly as the English walk does.

Provenance is kept, not merged. measured_dub_languages holds what AniList
credited; dub_languages holds ANN's name-matched set. A wrong ANN match
asserts another show's dub, so the two are not the same claim and the API
reports which one answered.

Usage:
    python -m pipeline.backfill_dub_languages
    python -m pipeline.backfill_dub_languages --language SPANISH
    python -m pipeline.backfill_dub_languages --max-seconds 1800
    python -m pipeline.backfill_dub_languages --languages SPANISH,FRENCH,GERMAN
"""

import argparse
import time

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
from pipeline.embed import progress_line

# Introspected from StaffLanguage, not recalled. JAPANESE is included on
# purpose: it is the original for a Japanese title and stripped there, but a
# Japanese cast on a Chinese donghua or a Korean webtoon adaptation genuinely
# is a dub, and that is the same rule the ANN pass applies.
LANGUAGE_CODES: dict[str, str] = {
    "JAPANESE": "ja",
    "ENGLISH": "en",
    "KOREAN": "ko",
    "ITALIAN": "it",
    "SPANISH": "es",
    "PORTUGUESE": "pt",
    "FRENCH": "fr",
    "GERMAN": "de",
    "HEBREW": "he",
    "HUNGARIAN": "hu",
}

# Ordered by measured yield, so a time-boxed run spends its budget on the
# languages most likely to return something. ENGLISH is omitted from the
# default: pipeline.backfill_dub already measures it into english_va_count
# and re-asking would cost an hour to learn nothing.
DEFAULT_LANGUAGES = (
    "SPANISH",
    "PORTUGUESE",
    "ITALIAN",
    "GERMAN",
    "FRENCH",
    "KOREAN",
    "HUNGARIAN",
    "HEBREW",
    "JAPANESE",
)

# A second walk doubles the requests and halves each one's share of the rate
# limit. Distinct from the English walk's lock, because the two are
# independent passes over different columns and may legitimately overlap.
LANGUAGE_LOCK_KEY = 727274

# node is load-bearing. Without it AniList resolves voiceActors to null on
# every edge rather than erroring, which reads as "no cast in this language"
# for the whole catalog - the bug that cost the English walk twelve hours a
# pass before it was found.
CAST_QUERY_TEMPLATE = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      characters(perPage: CHARACTERS, sort: ROLE) {
        edges {
          node { id }
          voiceActors(language: LANGUAGE) { id }
        }
      }
    }
  }
}
"""

# Rows not yet asked about this language, newest first so a freshly synced
# title gets a language early rather than waiting out the backlog.
SELECT_SQL = """
    SELECT m.id, m.country_of_origin
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND NOT (%(code)s = ANY(m.dub_languages_checked))
    ORDER BY m.id DESC
"""

# Both arrays are updated together and idempotently. array_append would
# duplicate on a re-run; this records the language as checked exactly once
# whether or not a cast was found.
UPDATE_SQL = """
    UPDATE media
    SET measured_dub_languages = (
            SELECT COALESCE(array_agg(DISTINCT lang ORDER BY lang), '{}')
            FROM unnest(
                measured_dub_languages
                || CASE WHEN %(found)s THEN ARRAY[%(code)s]::text[] ELSE '{}'::text[] END
            ) AS lang
        ),
        dub_languages_checked = (
            SELECT COALESCE(array_agg(DISTINCT lang ORDER BY lang), '{}')
            FROM unnest(dub_languages_checked || ARRAY[%(code)s]::text[]) AS lang
        )
    WHERE id = %(id)s
"""


def build_query(language: str) -> str:
    """The per-language shape. Aliasing nine languages into one request
    returns the same list under every alias, so each gets its own."""
    if language not in LANGUAGE_CODES:
        raise ValueError(f"{language} is not a StaffLanguage value")
    return CAST_QUERY_TEMPLATE.replace("LANGUAGE", language).replace(
        "CHARACTERS", str(CHARACTERS_PER_PAGE)
    )


def has_cast(media: dict) -> bool:
    """Whether any voice actor is credited in the requested language."""
    edges = (media.get("characters") or {}).get("edges") or []
    for edge in edges:
        if edge.get("voiceActors") is None:
            raise NullVoiceActors("voiceActors resolved to null; the query lost node")
        if edge["voiceActors"]:
            return True
    return False


def is_native(code: str, country: str | None) -> bool:
    """A cast in the title's own language is the original, not a dub."""
    return code in NATIVE_LANGS.get((country or "").upper(), set())


def walk_language(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    language: str,
    limit: int | None = None,
    deadline: float | None = None,
) -> tuple[int, int]:
    """Measure one language across the catalog. Returns (checked, found)."""
    code = LANGUAGE_CODES[language]
    query = build_query(language)
    rows = conn.execute(SELECT_SQL, {"code": code}).fetchall()
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print(f"[dub-lang] {language}: nothing left to check", flush=True)
        return 0, 0

    print(f"[dub-lang] {language} ({code}): {len(rows)} rows to check", flush=True)
    by_id = {row["id"]: row["country_of_origin"] for row in rows}
    ids = list(by_id)
    checked = found = 0
    started = time.monotonic()

    for start in range(0, len(ids), CAST_CHUNK):
        if deadline is not None and time.monotonic() >= deadline:
            print(
                f"[dub-lang] {language}: time budget reached at {checked}"
                f"/{len(ids)}; the next run continues from here",
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
                json_body={"query": query, "variables": {"ids": chunk, "perPage": len(chunk)}},
            )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            print(
                f"[dub-lang] {language}: chunk of {len(chunk)} failed"
                f" ({type(exc).__name__}); it stays unchecked and will be retried",
                flush=True,
            )
            continue
        if payload.get("errors"):
            print(f"[dub-lang] {language}: chunk returned GraphQL errors, skipping", flush=True)
            continue

        page = (payload.get("data") or {}).get("Page") or {}
        returned = {m["id"]: m for m in page.get("media") or []}
        for media_id in chunk:
            media = returned.get(media_id)
            if media is None:
                # Not an anime any more, or deleted upstream. Mark it checked
                # so the pass terminates rather than retrying it forever.
                conn.execute(UPDATE_SQL, {"id": media_id, "code": code, "found": False})
                continue
            credited = has_cast(media) and not is_native(code, by_id.get(media_id))
            conn.execute(UPDATE_SQL, {"id": media_id, "code": code, "found": credited})
            checked += 1
            found += credited
        conn.commit()

        done = min(start + CAST_CHUNK, len(ids))
        pct = 100.0 * found / checked if checked else 0.0
        progress = progress_line(done, len(ids), time.monotonic() - started, label="dub-lang")
        print(f"{progress} - {found} with a {code} cast ({pct:.0f}%)", flush=True)

    print(
        f"[dub-lang] {language}: {checked} checked, {found} credit a {code} voice actor",
        flush=True,
    )
    return checked, found


def backfill(
    client: RateLimitedClient,
    conn: psycopg.Connection,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
    limit: int | None = None,
    max_seconds: float | None = None,
) -> dict[str, tuple[int, int]]:
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
        return {}
    deadline = time.monotonic() + max_seconds if max_seconds else None
    results: dict[str, tuple[int, int]] = {}
    try:
        for language in languages:
            if deadline is not None and time.monotonic() >= deadline:
                print(
                    f"[dub-lang] budget spent before {language}; the next run picks up from here",
                    flush=True,
                )
                break
            results[language] = walk_language(
                client, conn, language, limit=limit, deadline=deadline
            )
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LANGUAGE_LOCK_KEY,))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure dub languages from AniList")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--language", help="a single StaffLanguage value")
    group.add_argument("--languages", help="comma-separated StaffLanguage values")
    parser.add_argument("--limit", type=int, default=None, help="titles per language")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="stop after this long; the next run continues where this stopped",
    )
    parser.add_argument("--min-interval", type=float, default=settings.anilist_min_interval)
    parser.add_argument("--cache-dir", default="")
    args = parser.parse_args()

    if args.language:
        languages: tuple[str, ...] = (args.language.strip().upper(),)
    elif args.languages:
        languages = tuple(
            part.strip().upper() for part in args.languages.split(",") if part.strip()
        )
    else:
        languages = DEFAULT_LANGUAGES
    for language in languages:
        if language not in LANGUAGE_CODES:
            raise SystemExit(
                f"{language} is not a StaffLanguage value."
                f" Known: {', '.join(sorted(LANGUAGE_CODES))}"
            )

    client = RateLimitedClient(min_interval=args.min_interval, cache_dir=args.cache_dir)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        results = backfill(
            client,
            conn,
            languages=languages,
            limit=args.limit,
            max_seconds=args.max_seconds,
        )
    if results:
        print("[dub-lang] done:", flush=True)
        for language, (checked, found) in results.items():
            share = 100.0 * found / checked if checked else 0.0
            print(f"  {language:<12} {checked:>6} checked, {found:>6} dubbed ({share:.1f}%)")


if __name__ == "__main__":
    main()
