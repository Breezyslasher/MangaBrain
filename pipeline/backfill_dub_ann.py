"""Fill dub languages from ANN, as a second source beside AniList.

The two sources divide cleanly. AniList is asked about one language per
aliased field and its enum covers ten, so it is the English source and is
measured per entry at one request per title - around 22k requests and twelve
hours for a full catalog. ANN tags every cast entry with its language in the
same response (58 of 58 on the sampled title, across nine languages), hands
over its entire anime list in roughly 22 paged requests, and returns 20 casts
per request. So ANN supplies the languages AniList was never asked about, and
backs up entries AniList left silent, in about three quarters of an hour.

ANN publishes no MyAnimeList or AniList id and offers no lookup by one, so
entries are matched on title against the bulk index (pipeline/ann.py). That
matching is the risk, not the cast data: measured against the strata sample it
resolves roughly 55-70% of titles, and a wrong match asserts another show's
dub. So an exact name match must be corroborated by year and format - this is
how a Suntory mineral-water advert once matched ANN's entry for 'your name.'
and was recorded as dubbed - while a base match, which resolves a season or
OVA to its parent series, is reported but never written here. Only exact
matches are stored, because a parent's dub languages are not the entry's.

AniList's own measurement is never overwritten. english_va_count and
english_dub_inferred stay as they were; this writes dub_languages,
dub_languages_source and ann_id.

Usage:
    python -m pipeline.backfill_dub_ann
    python -m pipeline.backfill_dub_ann --limit 500 --dry-run
"""

import argparse
from collections import Counter

import httpx
import psycopg
from psycopg.rows import dict_row

from api.config import settings
from pipeline.ann import UA, build_index, fetch_anime_index, fetch_casts, match

# Anime only, and only rows not already carrying ANN languages. Newest first,
# matching the AniList walk, so a fresh entry gets a language set early.
SELECT_SQL = """
    SELECT m.id, m.title_romaji, m.title_english, m.title_native, m.synonyms,
           m.start_year, m.format
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND m.dub_languages_source IS DISTINCT FROM 'ann'
    ORDER BY m.id DESC
"""

UPDATE_SQL = """
    UPDATE media
    SET dub_languages = %(langs)s,
        dub_languages_source = 'ann',
        ann_id = %(ann_id)s
    WHERE id = %(id)s
"""

CAST_CHUNK = 200


def candidate_titles(row: dict) -> list[str]:
    """Every name AniList knows, English first: ANN files under release title."""
    ordered = [row["title_english"], row["title_romaji"], row["title_native"]]
    ordered += row["synonyms"] or []
    seen: set[str] = set()
    out: list[str] = []
    for name in ordered:
        if not name:
            continue
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def backfill(
    conn: psycopg.Connection, limit: int | None = None, dry_run: bool = False
) -> tuple[int, int]:
    """Returns (written, unresolved)."""
    with httpx.Client(timeout=90.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        print("[dub-ann] fetching ANN's anime index", flush=True)
        entries = fetch_anime_index(client)
        index = build_index(entries)
        print(f"[dub-ann] {len(entries)} ANN entries, {len(index)} keys", flush=True)

        rows = conn.execute(SELECT_SQL).fetchall()
        if limit is not None:
            rows = rows[:limit]
        print(f"[dub-ann] {len(rows)} catalog rows to match", flush=True)

        # Match everything offline first, so the network is touched only for
        # the casts of entries that actually resolved.
        matched: list[tuple[int, str]] = []
        unresolved = 0
        verdicts: Counter[str] = Counter()
        for row in rows:
            verdict, entry = match(
                index,
                candidate_titles(row),
                year=row["start_year"],
                fmt=row["format"],
            )
            verdicts[verdict] += 1
            # Only exact: a base match resolves to a parent series, whose dub
            # languages are not this entry's.
            if verdict == "exact" and entry is not None:
                matched.append((row["id"], entry.ann_id))
            else:
                unresolved += 1
        pct = 100.0 * len(matched) / len(rows) if rows else 0.0
        print(
            f"[dub-ann] {len(matched)} resolved exactly ({pct:.0f}%), {unresolved} not",
            flush=True,
        )
        # The residue is not one thing, and the breakdown says whether it is
        # worth attacking: `base` entries did find a parent series and are the
        # franchise-propagation path, `rejected_*` are exact names the year or
        # format contradicted, `unmatched` are absent from ANN or filed under a
        # translated name. Collapsing all of it into one count hid which.
        for verdict, count in verdicts.most_common():
            share = 100.0 * count / len(rows) if rows else 0.0
            print(f"[dub-ann]   {verdict:<20} {count:>6} ({share:.1f}%)", flush=True)
        if dry_run or not matched:
            return 0, unresolved

        written = 0
        for start in range(0, len(matched), CAST_CHUNK):
            chunk = matched[start : start + CAST_CHUNK]
            casts = fetch_casts(client, [ann_id for _, ann_id in chunk])
            for media_id, ann_id in chunk:
                _english, langs = casts.get(ann_id, (0, set()))
                if not langs:
                    continue
                conn.execute(
                    UPDATE_SQL,
                    {
                        "id": media_id,
                        "ann_id": int(ann_id),
                        "langs": sorted(lang.lower() for lang in langs),
                    },
                )
                written += 1
            conn.commit()
            print(
                f"[dub-ann] {min(start + CAST_CHUNK, len(matched))}/{len(matched)}"
                f" casts fetched, {written} rows written",
                flush=True,
            )
    print(
        f"[dub-ann] done: {written} rows given dub languages, {unresolved} unresolved",
        flush=True,
    )
    return written, unresolved


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill dub languages from ANN")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="match only, write nothing")
    args = parser.parse_args()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        backfill(conn, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
