"""Exercise every TMDB request pipeline.sync_tmdb makes, live, and print
what came back - so "is the request right" is answered by TMDB rather than
by reading its docs. Writes nothing; needs no database.

    TMDB_API_KEY=... python -m pipeline.check_tmdb
"""

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


def main() -> None:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        sys.exit("Set TMDB_API_KEY.")
    client = sync_tmdb.TmdbClient(key)
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
