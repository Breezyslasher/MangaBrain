"""Does Open Library carry enough ratings to justify a score column?

Books have no average_score, so the minimum-score filter is a no-op on
the Books tab. Adding one is cheap to describe and the wrong thing to
guess at, because the failure mode is specific: a rating present on a
small, well-known slice of the catalogue and absent everywhere else
would pass for a quality filter while acting as a popularity filter,
which is the one thing this project's ranking rules out.

So the question is not "do ratings exist" - they do, in a separate
monthly dump the works dump knows nothing about - but "what fraction of
the books we actually import would carry one, and with how many votes
behind it". A mean of two stars from two people is noise wearing a
number, and a filter over it is worse than no filter.

Two passes, in one run:

  1. The ratings dump, read whole. It is small, so this is exact: how
     many ratings, over how many distinct works, and how the votes pile
     up per work.
  2. The works dump, read as far as --limit allows, applying the same
     gate the importer applies (a description of at least
     --min-description characters, and at least one subject). For every
     work that would be imported, whether it is rated and by how many.

Pass 2 is a sample, and deliberately an unbiased one: the dump is read
in its own order, which is by work key, not by popularity. 2M rows is
about eight minutes and yields tens of thousands of importable works -
far more than coverage needs to be pinned down.

The bias check is the number to read first. If importable works are
rated far more often than works in general, the gate is selecting for
fame rather than for content, and that is worth knowing whatever is
decided about the score column.

    python -m pipeline.survey_openlibrary_ratings --limit 2000000
"""

import argparse
import gzip
import io
import statistics
import time

import httpx

from pipeline.openlibrary import (
    MIN_DESCRIPTION,
    ChunkReader,
    description_of,
    download_chunks,
    iter_works,
)

RATINGS_URL = "https://openlibrary.org/data/ol_dump_ratings_latest.txt.gz"

# A score is only worth filtering on with some votes behind it, so
# coverage is reported at each floor rather than once. The gap between
# them is the answer: if coverage collapses from 1 to 5, the ratings are
# a long tail of single votes and there is no score column to build.
VOTE_FLOORS = (1, 3, 5, 10)

# Open Library's own star scale. Anything outside it is a column we have
# misread, not a rating, so it is counted separately instead of averaged.
RATING_RANGE = range(1, 6)


def work_id(key: str) -> str:
    """The OL work id out of a /works/OL123W key, however it is spelled."""
    return key.strip().rsplit("/", 1)[-1]


def read_ratings() -> tuple[dict[str, list[int]], dict[str, int]]:
    """work id -> its ratings, read from the whole ratings dump.

    The column order is documented as work key, optional edition key,
    rating, date - and the edition key being optional is exactly the
    kind of detail a fixed index gets wrong on the rows that have it.
    The rating is found by value instead: it is the only field that is
    a bare integer on the star scale.
    """
    ratings: dict[str, list[int]] = {}
    tally = {"rows": 0, "unparsed": 0, "no_work": 0}
    reader = io.BufferedReader(ChunkReader(download_chunks(RATINGS_URL)), buffer_size=1 << 20)
    try:
        with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
            for raw in stream:
                tally["rows"] += 1
                parts = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                if not parts or not parts[0].strip():
                    tally["no_work"] += 1
                    continue
                score = None
                for field in parts[1:]:
                    text = field.strip()
                    if text.isdigit() and int(text) in RATING_RANGE:
                        score = int(text)
                        break
                if score is None:
                    tally["unparsed"] += 1
                    continue
                ratings.setdefault(work_id(parts[0]), []).append(score)
    except (OSError, EOFError, httpx.HTTPError) as exc:
        # Reported rather than swallowed: a truncated ratings dump would
        # understate coverage, which argues for the conclusion this
        # survey is most likely to reach. That is the kind of agreement
        # worth being suspicious of.
        tally["truncated"] = f"{type(exc).__name__}: {exc}"
        print(f"  ratings stream ended early ({type(exc).__name__}) at row {tally['rows']:,}")
    return ratings, tally


def survey(limit: int | None, min_description: int) -> dict:
    ratings, tally = read_ratings()
    per_work = sorted(len(v) for v in ratings.values())
    all_scores = [s for scores in ratings.values() for s in scores]
    out: dict = {
        "rating_rows": tally["rows"],
        "rating_unparsed": tally["unparsed"],
        "rated_works": len(ratings),
        "mean_rating": round(statistics.fmean(all_scores), 2) if all_scores else 0.0,
        "votes_median": per_work[len(per_work) // 2] if per_work else 0,
        "votes_single": sum(1 for n in per_work if n == 1),
        "truncated_ratings": tally.get("truncated"),
    }
    print(
        f"ratings dump: {out['rating_rows']:,} rows over {out['rated_works']:,} works; "
        f"mean {out['mean_rating']}; {out['votes_single']:,} works rated exactly once",
        flush=True,
    )

    counts = {"rows": 0, "parsed": 0, "usable": 0, "rated_any": 0, "usable_rated": 0}
    floors = dict.fromkeys(VOTE_FLOORS, 0)
    rated_scores: list[float] = []
    started = time.monotonic()

    for rows, work in iter_works(limit, status=counts):
        counts["rows"] = rows
        counts["parsed"] += 1
        wid = work_id(str(work.get("key") or ""))
        scores = ratings.get(wid)
        if scores:
            counts["rated_any"] += 1
        if len(description_of(work)) >= min_description and (work.get("subjects") or []):
            counts["usable"] += 1
            if scores:
                counts["usable_rated"] += 1
                rated_scores.append(statistics.fmean(scores))
                for floor in VOTE_FLOORS:
                    if len(scores) >= floor:
                        floors[floor] += 1
        if rows % 250_000 == 0:
            rate = rows / max(time.monotonic() - started, 1e-9)
            print(
                f"  {rows:>10,} rows  {counts['usable']:>9,} usable  "
                f"{counts['usable_rated']:>7,} rated  {rate:,.0f} rows/s",
                flush=True,
            )

    counts["floors"] = floors
    counts["mean_rated_score"] = round(statistics.fmean(rated_scores), 2) if rated_scores else 0.0
    counts["seconds"] = round(time.monotonic() - started, 1)
    out.update(counts)
    return out


def report(out: dict, min_description: int) -> None:
    usable = out["usable"] or 1
    parsed = out["parsed"] or 1
    print(f"\nworks read: {out['rows']:,} rows in {out['seconds']:,}s")
    print(f"importable at {min_description}+ chars and 1+ subject: {out['usable']:,}")
    print("\ncoverage of the books we would import:")
    for floor in VOTE_FLOORS:
        hit = out["floors"][floor]
        label = "rating" if floor == 1 else "ratings"
        print(f"  {floor:>3}+ {label:<8} {hit:>8,}  {100 * hit / usable:>6.2f}% of importable")
    print(f"\nmean score where rated: {out['mean_rated_score']}")
    # The bias check. Equal rates mean the gate is not selecting for
    # fame; a much higher rate among importable works means it is, and
    # any score column built on this would inherit that.
    rate_all = 100 * out["rated_any"] / parsed
    rate_usable = 100 * out["usable_rated"] / usable
    if rate_all:
        print(
            f"rated share: {rate_usable:.2f}% of importable vs {rate_all:.2f}% of all works"
            f" ({rate_usable / rate_all:.1f}x)"
        )
    else:
        print("rated share: no rated work appeared in the sample")
    if out["truncated_ratings"]:
        print(f"\nWARNING ratings dump truncated ({out['truncated_ratings']}); coverage is a floor")
    if out.get("truncated"):
        print(f"WARNING works dump truncated ({out['truncated']}); the sample is short")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure Open Library rating coverage")
    parser.add_argument(
        "--limit",
        type=int,
        default=2_000_000,
        help="works rows to read; 0 reads the whole dump (hours)",
    )
    parser.add_argument("--min-description", type=int, default=MIN_DESCRIPTION)
    args = parser.parse_args()
    out = survey(args.limit or None, args.min_description)
    report(out, args.min_description)


if __name__ == "__main__":
    main()
