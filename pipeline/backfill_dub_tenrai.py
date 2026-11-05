"""Fill mal_dub_languages from the MAL-derived cast API.

Measured before it was built (pipeline.probe_dub_mal, 5 Nov 2026): the
endpoint answers per entry on an id_mal join, carries a language on every
voice actor, and returned nine languages for Cowboy Bebop in one second.
Against 36 titles whose English dub none of our sources knew about, it
found 12 - The Amazing 3 among them, which is the title this whole
investigation started from, and which it reports as en + ja +
portuguese-(br), agreeing with AniList's Portuguese and adding the
English that was missing.

Why a one-off backfill rather than a nightly source: the service is a
community successor about a month into the role, replacing one that was
switched off in October after years of being depended on. Running this
writes the answers into our own column, with their own provenance, so
the data survives the source. Re-run it when coverage matters again.

    docker exec mangabrain-worker-1 python -m pipeline.backfill_dub_tenrai --limit 200
    docker exec mangabrain-worker-1 python -m pipeline.backfill_dub_tenrai --all

Default scope is the English gap: anime with an id_mal and no English dub
answer from any existing source. --all walks every anime with an id_mal,
which is what makes this a candidate to replace ANN's 31.8% name match
for the non-English languages too.
"""

import argparse

from api.config import settings
from pipeline.client import RateLimitedClient
from pipeline.probe_dub_mal import TENRAI_BASE, dub_codes, mal_languages

# One request per title, and the service is somebody's goodwill.
MIN_INTERVAL = 1.0

GAP_SQL = """
    SELECT m.id, m.id_mal, m.title_romaji, m.title_english, m.country_of_origin
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND m.id_mal IS NOT NULL
      AND m.mal_dub_checked_at IS NULL
      AND NOT (m.dub_languages @> ARRAY['en']::text[]
               OR m.measured_dub_languages @> ARRAY['en']::text[]
               OR m.mal_dub_languages @> ARRAY['en']::text[]
               OR m.english_va_count > 0
               OR m.english_dub_inferred)
    ORDER BY m.id
    LIMIT %(limit)s
"""

ALL_SQL = """
    SELECT m.id, m.id_mal, m.title_romaji, m.title_english, m.country_of_origin
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND m.id_mal IS NOT NULL
      AND m.mal_dub_checked_at IS NULL
    ORDER BY m.id
    LIMIT %(limit)s
"""

# checked_at is stamped whether or not languages came back, so a re-run
# does not ask again about a title the source has nothing for. An empty
# array with a timestamp means "asked, nothing known", which is a
# different fact from NULL, meaning "never asked".
WRITE_SQL = """
    UPDATE media
    SET mal_dub_languages = %(langs)s, mal_dub_checked_at = now()
    WHERE id = %(id)s
"""


def backfill(limit: int, everything: bool, base_url: str) -> int:
    import psycopg
    from psycopg.rows import dict_row

    client = RateLimitedClient(min_interval=MIN_INTERVAL)
    # One known-good request first: a dead host would otherwise stamp
    # thousands of rows as "asked, nothing known", which is worse than
    # not running at all because the next run would skip them.
    _, status = mal_languages(client, 1, base_url)
    if status != "ok":
        print(f"the cast API did not answer for a known-good title (status={status}); stopping.")
        client.close()
        return 0

    written = with_english = 0
    unknown_names: set[str] = set()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        rows = conn.execute(ALL_SQL if everything else GAP_SQL, {"limit": limit}).fetchall()
        print(f"{len(rows)} titles to ask about")
        for row in rows:
            langs, status = mal_languages(client, row["id_mal"], base_url)
            if status == "failed":
                # Not stamped: a failed request is the probe's problem and
                # must not be recorded as the source having no answer.
                print(f"  FAILED #{row['id']} mal={row['id_mal']}")
                continue
            codes, unknown = dub_codes(langs, row["country_of_origin"])
            unknown_names |= unknown
            conn.execute(WRITE_SQL, {"langs": sorted(codes), "id": row["id"]})
            written += 1
            if "en" in codes:
                with_english += 1
                title = (row["title_english"] or row["title_romaji"] or "")[:46]
                print(f"  EN   #{row['id']:>7}  {title}  {sorted(codes)}")
            if written % 100 == 0:
                conn.commit()
                print(f"  ... {written}/{len(rows)}")
        conn.commit()
    client.close()

    print(f"\nwrote {written} rows; {with_english} now have an English dub on record")
    if unknown_names:
        print(f"language names with no code mapping: {sorted(unknown_names)}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill dub languages from the MAL cast API")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--all",
        action="store_true",
        help="every anime with an id_mal, not only those with no English answer",
    )
    parser.add_argument("--base-url", default=TENRAI_BASE)
    args = parser.parse_args()
    backfill(args.limit, args.all, args.base_url)


if __name__ == "__main__":
    main()
