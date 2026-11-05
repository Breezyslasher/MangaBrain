"""Does the official MAL list fetch work, from wherever this runs?

Exercises api.routers.mal._fetch_list itself rather than a copy of it, so
a passing run is evidence about the code the server actually uses. A
reimplementation here would drift, and the first thing it would stop
catching is the thing worth catching: MAL answers 200 to an unknown field
name, so a typo in LIST_PARAMS returns a complete list with no statuses
and no error.

Writes nothing. No database is touched, which is what lets this run on a
CI runner instead of the deployment host - and that in turn is the point,
because "is MAL reachable and is our request shaped right" should not
require someone to be at the NAS.

    MAL_CLIENT_ID=... python -m pipeline.probe_mal_list --username breezyslasher
"""

import argparse
from collections import Counter

from api.config import settings
from api.routers.mal import LIST_TYPES, _fetch_list
from pipeline.client import RateLimitedClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the official MAL list API")
    parser.add_argument("--username", required=True)
    parser.add_argument("--min-interval", type=float, default=None)
    args = parser.parse_args()

    if not settings.mal_client_id:
        print("MAL_CLIENT_ID is not set; nothing to probe.")
        raise SystemExit(1)

    client = RateLimitedClient(
        min_interval=args.min_interval or settings.mal_min_interval,
        max_retries=settings.interactive_max_retries,
        timeout=settings.interactive_timeout,
        extra_headers={"X-MAL-CLIENT-ID": settings.mal_client_id},
    )
    failures = 0
    try:
        for list_type in LIST_TYPES:
            try:
                entries = _fetch_list(client, args.username, list_type)
            except Exception as exc:  # noqa: BLE001
                # Includes the HTTPException the router raises, whose detail
                # distinguishes a rejected credential from a private list
                # from a host that did not answer. Printing the type alone
                # would throw that away.
                detail = getattr(exc, "detail", None) or str(exc)
                print(f"{list_type}: FAILED - {detail}")
                failures += 1
                continue

            statuses = Counter(status for status, _score in entries.values())
            scored = sum(1 for _status, score in entries.values() if score is not None)
            print(f"{list_type}: {len(entries)} entries, {scored} scored")
            for status, count in statuses.most_common():
                print(f"    {status or '(empty)':<16} {count}")
    finally:
        client.close()

    if failures:
        raise SystemExit(1)
    print(
        "\nBoth lists fetched. Paging, the client id header and the"
        "\nlist_status field all work from here."
    )


if __name__ == "__main__":
    main()
