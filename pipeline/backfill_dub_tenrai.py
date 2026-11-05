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
for the non-English languages too, and it is uncapped - about six hours
for 22,000 titles at one request a second.

EVERY language is written, not only English. dub_codes keeps whatever the
cast API credits, minus the title's own: a Japanese cast on a Japanese
show is the original, while a Japanese cast on a Chinese donghua is a
dub, which is why country_of_origin is passed. English is special only in
choosing WHICH titles to ask about, and in the dub badge.

The walk is resumable. mal_dub_checked_at records that a title was asked,
so a second run continues rather than restarting, and interrupting this
costs only the titles in flight.

That same stamp is why --remap exists. A language name the mapping did not
know is stored as a reversible slug, and because this will not ask about a
stamped title twice, the fix has to work on what is already in the column:

    docker exec mangabrain-worker-1 python -m pipeline.backfill_dub_tenrai --remap

It has now been needed twice. The cast API spells Brazilian Portuguese
"Portuguese (BR)" where AniList spells it "Portuguese (Brazil)", so the
first rows went in as the slug portuguese-(br), which no filter matched.
Mapping it then produced pt-br - a code neither ANN nor AniList writes,
measured at 0 rows against 830 and 1,637 carrying pt - so 331 titles that
already had a Portuguese dub on record were split across two codes and
dropped out of the Portuguese filter. Both are repairs to stored values,
and both are what --remap is for.
"""

import argparse

from api.config import settings
from pipeline.client import RateLimitedClient
from pipeline.dub_language_codes import canonical, code_for, is_code, unslug
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

# Repair, not re-fetch. A name the mapping did not know was stored as a
# reversible slug, so correcting it costs no requests - which is the only
# reason it can be corrected at all, since checked_at means this will not
# ask about those titles again.
REMAP_SCAN_SQL = """
    SELECT id, mal_dub_languages
    FROM media
    WHERE mal_dub_languages <> '{}'
    ORDER BY id
"""

REMAP_WRITE_SQL = """
    UPDATE media SET mal_dub_languages = %(langs)s WHERE id = %(id)s
"""


# A cap exists so a first run can be small on purpose. --all without one
# means the whole catalog, not the first 500 of it: a default that
# silently truncates a backfill is worse than no default, because the run
# looks complete and the next pass skips every row it stamped.
DEFAULT_GAP_LIMIT = 500
NO_LIMIT = 10_000_000


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
    scope = "every anime with an id_mal" if everything else "anime with no English answer"
    print(f"scope: {scope}; cap: {limit}; one request a second")
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
            fresh = unknown - unknown_names
            if fresh:
                # Named on sight rather than only in the summary: a six-hour
                # run that reports a vocabulary gap at the end reports it
                # after writing every row the gap affects.
                print(f"  NOTE  no code for {sorted(fresh)} - rerun with --remap once mapped")
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


def remap() -> int:
    """Re-map stored slugs now that the mapping knows the name.

    Nothing is requested and nothing is re-stamped: this only rewrites
    values the column already holds. Needed because the backfill records
    which titles it has asked about, so a mapping gap found afterwards
    cannot be fixed by running the backfill again.
    """
    import psycopg
    from psycopg.rows import dict_row

    changed = 0
    still_unknown: set[str] = set()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        rows = conn.execute(REMAP_SCAN_SQL).fetchall()
        for row in rows:
            stored = row["mal_dub_languages"] or []
            fixed: list[str] = []
            for value in stored:
                if is_code(value):
                    # A code can still be one the catalog has stopped
                    # using: pt-br was written for a market every other
                    # source files under pt, which split it in two.
                    fixed.append(canonical(value))
                    continue
                code, known = code_for(unslug(value))
                if known:
                    fixed.append(canonical(code))
                else:
                    still_unknown.add(value)
                    fixed.append(value)
            if sorted(set(fixed)) != sorted(set(stored)):
                conn.execute(REMAP_WRITE_SQL, {"langs": sorted(set(fixed)), "id": row["id"]})
                changed += 1
        conn.commit()

    print(f"scanned {len(rows)} rows with languages; remapped {changed}")
    if still_unknown:
        print(f"still no code for: {sorted(still_unknown)}")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill dub languages from the MAL cast API")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"cap the run (default {DEFAULT_GAP_LIMIT}, or no cap with --all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="every anime with an id_mal, not only those with no English answer",
    )
    parser.add_argument("--base-url", default=TENRAI_BASE)
    parser.add_argument(
        "--remap",
        action="store_true",
        help="re-map stored language slugs against the current mapping; sends no requests",
    )
    args = parser.parse_args()
    if args.remap:
        remap()
        return
    limit = args.limit
    if limit is None:
        limit = NO_LIMIT if args.all else DEFAULT_GAP_LIMIT
    backfill(limit, args.all, args.base_url)


if __name__ == "__main__":
    main()
