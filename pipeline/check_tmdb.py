"""Exercise every TMDB request pipeline.sync_tmdb makes, live, and print
what came back - so "is the request right" is answered by TMDB rather than
by reading its docs. Writes nothing; needs no database.

    TMDB_API_KEY=... python -m pipeline.check_tmdb
    TMDB_API_KEY=... python -m pipeline.check_tmdb --titles movie/68721 tv/1399

--titles reports, for each title, its overview length and keyword count
and whether the import keeps it: under the 200-character rule alone, and
with the short-overview rule (80+ characters with five or more keywords).
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

from pipeline import sync_tmdb

# Fight Club and Game of Thrones: long-lived ids with known external ids
# (tt0137523; tt0944947 and TheTVDB 121361) to check the mapping against.
SAMPLES = (
    ("movie", 550, {"imdb": "tt0137523"}),
    ("tv", 1399, {"imdb": "tt0944947", "tvdb": "121361"}),
)


# Well-known films for --titles with no ids: the Iron Man films that
# started this, and others across decades and studios.
FAMOUS = (
    "movie/1726 movie/10138 movie/68721 movie/24428 movie/155 movie/27205"
    " movie/862 movie/597 movie/19995 movie/603 movie/13 movie/680"
)


def titles(client: sync_tmdb.TmdbClient, refs: list[str]) -> None:
    for ref in refs:
        kind, _, raw = ref.partition("/")
        try:
            data = client.get(
                f"/{sync_tmdb.KINDS[kind][0]}/{int(raw)}",
                {"append_to_response": "keywords", "language": "en-US"},
            )
        except sync_tmdb.Gone:
            print(f"{ref}: not on TMDB")
            continue
        overview = sync_tmdb.clean(data.get("overview"))
        keywords = sync_tmdb.keyword_tags(data.get("keywords") or {})
        old = len(overview) >= sync_tmdb.MIN_OVERVIEW
        new = sync_tmdb.row_for(kind, data) is not None
        print(
            f"{ref} {data.get('title') or data.get('name')!r}: overview {len(overview)} chars,"
            f" {len(keywords)} keywords | 200-char rule: {'kept' if old else 'dropped'}"
            f" | with short-overview rule: {'kept' if new else 'dropped'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check TMDB requests, live")
    parser.add_argument(
        "--titles", nargs="*", help="kind/id refs (movie/68721); none = a set of famous films"
    )
    args = parser.parse_args()
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        sys.exit("Set TMDB_API_KEY.")
    client = sync_tmdb.TmdbClient(key)
    if args.titles is not None:
        try:
            titles(client, args.titles or FAMOUS.split())
        finally:
            client.close()
        return
    failures = 0
    try:
        for kind, tmdb_id, expected in SAMPLES:
            row = sync_tmdb.fetch(client, kind, tmdb_id)
            if row is None:
                print(f"FAIL {kind}/{tmdb_id}: no row (gone, or failed the gate)")
                failures += 1
                continue
            ok = all(row["external_ids"].get(k) == v for k, v in expected.items())
            failures += not ok
            print(
                f"{'ok  ' if ok else 'FAIL'} {kind}/{tmdb_id} {row['title']!r}:"
                f" overview {len(row['description'])} chars, {len(row['genres'])} genres,"
                f" {len(row['tags'])} keywords, format {row['format']}, status {row['status']},"
                f" year {row['start_year']}, country {row['country']},"
                f" external_ids {row['external_ids']} (expected {expected})"
            )
        today = datetime.now(UTC).date()
        for kind in sync_tmdb.KINDS:
            ids = sync_tmdb.changed_ids(client, kind, today - timedelta(days=1), today)
            failures += not ids
            print(f"{'ok  ' if ids else 'FAIL'} /{kind}/changes, last day: {len(ids):,} ids")
        for kind in sync_tmdb.KINDS:
            version, ids = sync_tmdb.export_ids(kind)
            failures += not ids
            print(f"{'ok  ' if ids else 'FAIL'} {kind} export {version}: {len(ids):,} ids")
    finally:
        client.close()
    print(
        "all TMDB requests answered as expected" if not failures else f"{failures} check(s) failed"
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
