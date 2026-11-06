"""Would Hardcover improve the books catalogue enough to be worth adding?

Open Library is the only book source with a free bulk dump, so it stays
the catalogue. The question is whether Hardcover (https://hardcover.app),
whose blurbs and reader-applied genres, moods and tags are much richer,
should fill in what Open Library lacks. That is decided by measuring,
not by reputation, on two samples drawn from the same dump the importer
reads:

  imported   works the importer keeps (a description of at least
             --min-description characters, and a subject). Here
             Hardcover could only add: more tags, a longer synopsis.
  rejected   works the importer drops only for their description. Here
             Hardcover could add the book itself: a match with a long
             enough description is a book the catalogue gains.

Matching is by ISBN, the one key both sides share exactly. A work has no
ISBN of its own, so each sampled work's editions are read from Open
Library's API and their ISBNs looked up on Hardcover in batches. Title
search would match more and be wrong some of the time, so it is left
out: the match rates here are a floor, not an estimate.

The dump is read in its own order (by work key, not popularity) and the
samples are reservoir samples over the rows read, so neither is tilted
toward famous books. Popularity is never read from Hardcover at all.

Needs a Hardcover API token (hardcover.app > Settings > API), in
HARDCOVER_TOKEN, with or without the "Bearer " prefix. Nothing is
stored; the survey prints its result and exits.

    HARDCOVER_TOKEN=... python -m pipeline.survey_hardcover
    python -m pipeline.survey_hardcover --rows 400000 --sample 300 --json out.json
"""

import argparse
import json
import os
import random
import statistics
import sys
import time

from pipeline.client import RateLimitedClient
from pipeline.openlibrary import MIN_DESCRIPTION, description_of, iter_works
from pipeline.sync_openlibrary import clean_description, row_for

HARDCOVER = "https://api.hardcover.app/v1/graphql"
OPENLIBRARY = "https://openlibrary.org"

# Hardcover documents 60 requests a minute per token.
HARDCOVER_INTERVAL = 1.1
OPENLIBRARY_INTERVAL = 0.5

# ISBNs per work: enough to find the edition Hardcover knows, without a
# reprint-heavy classic sending hundreds.
MAX_ISBNS = 25
# Works per Hardcover request.
BATCH = 8

TAG_CATEGORIES = ("Genre", "Mood", "Tag", "Content Warning")

EDITIONS_QUERY = """
query Editions($isbns: [String!]!) {
  editions(
    where: {_or: [{isbn_13: {_in: $isbns}}, {isbn_10: {_in: $isbns}}]}
    limit: 400
  ) {
    isbn_13
    isbn_10
    book { id description cached_tags }
  }
}
"""


def sample_works(rows: int, size: int, min_description: int, seed: int) -> dict:
    """Reservoir samples of importable and description-rejected works."""
    rng = random.Random(seed)
    pools: dict[str, list[dict]] = {"imported": [], "rejected": []}
    seen = {"imported": 0, "rejected": 0}
    status: dict = {}
    started = time.monotonic()
    read = 0
    # cache=None: stream. The default cache downloads the whole dump
    # first, which a sample of the first few hundred thousand rows does
    # not need.
    for read, work in iter_works(rows, status=status, cache=None):
        row = row_for(work, min_description)
        if row is not None:
            group = "imported"
        elif row_for(work, 0) is not None:
            group = "rejected"
        else:
            continue
        seen[group] += 1
        entry = {
            "key": str(work["key"]),
            "description": len(clean_description(description_of(work))),
            "tags": len((row or {}).get("tags") or []) + len((row or {}).get("genres") or []),
        }
        pool = pools[group]
        if len(pool) < size:
            pool.append(entry)
        else:
            slot = rng.randrange(seen[group])
            if slot < size:
                pool[slot] = entry
        if read % 100_000 == 0:
            print(
                f"  {read:>9,} rows  {seen['imported']:,} importable"
                f"  {seen['rejected']:,} rejected for description",
                flush=True,
            )
    print(f"  read {read:,} rows in {time.monotonic() - started:.0f}s", flush=True)
    if status.get("truncated"):
        print(f"  WARNING: dump stream ended early ({status['truncated']})", flush=True)
    return {"pools": pools, "seen": seen, "rows": read}


def isbns_of(client: RateLimitedClient, work_key: str) -> list[str]:
    try:
        data = client.request("GET", f"{OPENLIBRARY}{work_key}/editions.json", params={"limit": 50})
    except Exception as exc:  # noqa: BLE001 - one work's failure is a miss
        print(f"  editions of {work_key} failed: {type(exc).__name__}", flush=True)
        return []
    out: list[str] = []
    for edition in data.get("entries") or []:
        for field in ("isbn_13", "isbn_10"):
            for isbn in edition.get(field) or []:
                isbn = str(isbn).replace("-", "").strip()
                if isbn and isbn not in out:
                    out.append(isbn)
    return out[:MAX_ISBNS]


def hardcover_books(client: RateLimitedClient, isbns: list[str]) -> dict[str, dict]:
    """Hardcover's book for each ISBN it knows."""
    data = client.request(
        "POST", HARDCOVER, json_body={"query": EDITIONS_QUERY, "variables": {"isbns": isbns}}
    )
    if data.get("errors"):
        raise RuntimeError(f"Hardcover refused the query: {data['errors'][0].get('message')}")
    found: dict[str, dict] = {}
    for edition in (data.get("data") or {}).get("editions") or []:
        book = edition.get("book")
        if not book:
            continue
        for field in ("isbn_13", "isbn_10"):
            if edition.get(field):
                found[str(edition[field])] = book
    return found


def tag_counts(cached_tags) -> dict[str, int]:
    if isinstance(cached_tags, str):
        try:
            cached_tags = json.loads(cached_tags)
        except ValueError:
            cached_tags = {}
    cached_tags = cached_tags if isinstance(cached_tags, dict) else {}
    return {c: len(cached_tags.get(c) or []) for c in TAG_CATEGORIES}


def measure(
    group: list[dict],
    openlibrary: RateLimitedClient,
    hardcover: RateLimitedClient,
    min_description: int,
) -> dict:
    for entry in group:
        entry["isbns"] = isbns_of(openlibrary, entry["key"])
    with_isbn = [e for e in group if e["isbns"]]
    for start in range(0, len(with_isbn), BATCH):
        chunk = with_isbn[start : start + BATCH]
        found = hardcover_books(hardcover, [i for e in chunk for i in e["isbns"]])
        for entry in chunk:
            book = next((found[i] for i in entry["isbns"] if i in found), None)
            if book is not None:
                entry["hc_description"] = len(clean_description(book.get("description") or ""))
                entry["hc_tags"] = tag_counts(book.get("cached_tags"))
        print(f"  looked up {min(start + BATCH, len(with_isbn))}/{len(with_isbn)}", flush=True)

    matched = [e for e in group if "hc_tags" in e]
    n = len(group) or 1

    def pct(count: int, of: int) -> float:
        return round(100 * count / of, 1) if of else 0.0

    def median(values: list[int]) -> float:
        return statistics.median(values) if values else 0

    hc_tag_total = [sum(e["hc_tags"][c] for c in ("Genre", "Mood", "Tag")) for e in matched]
    return {
        "works": len(group),
        "with_isbn": len(with_isbn),
        "with_isbn_pct": pct(len(with_isbn), n),
        "matched": len(matched),
        "matched_pct": pct(len(matched), n),
        "hc_long_description": sum(1 for e in matched if e["hc_description"] >= min_description),
        "hc_long_description_pct": pct(
            sum(1 for e in matched if e["hc_description"] >= min_description), n
        ),
        "hc_longer_description": sum(1 for e in matched if e["hc_description"] > e["description"]),
        "ol_tags_median": median([e["tags"] for e in group]),
        "ol_tags_median_matched": median([e["tags"] for e in matched]),
        "hc_tags_median": median(hc_tag_total),
        "hc_3plus_tags_pct": pct(sum(1 for t in hc_tag_total if t >= 3), len(matched)),
        "hc_with_mood_pct": pct(sum(1 for e in matched if e["hc_tags"]["Mood"]), len(matched)),
        "hc_with_genre_pct": pct(sum(1 for e in matched if e["hc_tags"]["Genre"]), len(matched)),
    }


def report(out: dict) -> None:
    seen = out["seen"]
    print()
    print(f"Dump rows read: {out['rows']:,}")
    print(f"  importable {seen['imported']:,}, rejected only for description {seen['rejected']:,}")
    for name, label in (("imported", "Imported books"), ("rejected", "Rejected books")):
        r = out[name]
        print()
        print(f"{label} (sample of {r['works']}):")
        print(f"  have an ISBN on Open Library     {r['with_isbn']:>5}  {r['with_isbn_pct']}%")
        print(f"  found on Hardcover by ISBN       {r['matched']:>5}  {r['matched_pct']}%")
        print(
            f"  Hardcover description >= {out['min_description']} chars"
            f" {r['hc_long_description']:>4}  {r['hc_long_description_pct']}%"
        )
        if name == "imported":
            print(f"  Hardcover description longer     {r['hc_longer_description']:>5}")
            print(
                f"  tags: Open Library median {r['ol_tags_median_matched']}"
                f" vs Hardcover median {r['hc_tags_median']} (matched books)"
            )
        else:
            print(f"  tags: Hardcover median {r['hc_tags_median']} (matched books)")
        print(
            f"  of matched: genre {r['hc_with_genre_pct']}%, mood {r['hc_with_mood_pct']}%,"
            f" 3+ tags {r['hc_3plus_tags_pct']}%"
        )
    rejected = out["rejected"]
    if out["seen"]["rejected"] and rejected["works"]:
        share = rejected["hc_long_description"] / rejected["works"]
        print()
        print(
            f"Books Hardcover could add: about {share * out['seen']['rejected']:,.0f}"
            f" of the {out['seen']['rejected']:,} rejected works in the rows read"
            f" ({100 * share:.1f}%), before scaling to the whole dump."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure what Hardcover would add to books")
    parser.add_argument("--rows", type=int, default=400_000, help="dump rows to sample from")
    parser.add_argument("--sample", type=int, default=300, help="works per group")
    parser.add_argument("--min-description", type=int, default=MIN_DESCRIPTION)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", help="also write the result to this file")
    args = parser.parse_args()

    token = os.environ.get("HARDCOVER_TOKEN", "").strip()
    if not token:
        sys.exit("Set HARDCOVER_TOKEN (hardcover.app > Settings > API).")
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"

    print(f"Sampling {args.sample} works per group from {args.rows:,} dump rows", flush=True)
    sampled = sample_works(args.rows, args.sample, args.min_description, args.seed)
    openlibrary = RateLimitedClient(min_interval=OPENLIBRARY_INTERVAL, timeout=60.0)
    hardcover = RateLimitedClient(
        min_interval=HARDCOVER_INTERVAL,
        timeout=60.0,
        extra_headers={"Authorization": token},
    )
    out: dict = {
        "rows": sampled["rows"],
        "seen": sampled["seen"],
        "min_description": args.min_description,
    }
    try:
        for name in ("imported", "rejected"):
            print(f"Measuring {name} ({len(sampled['pools'][name])} works)", flush=True)
            out[name] = measure(
                sampled["pools"][name], openlibrary, hardcover, args.min_description
            )
    finally:
        openlibrary.close()
        hardcover.close()
    report(out)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
