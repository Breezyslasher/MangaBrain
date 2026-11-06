"""How much of Open Library is worth ingesting?

Books are the one medium measured ready: the source probe found 96% of
sampled works carrying a description, median 537 characters, with a
median of 31 subjects. But that sample came from a subject:fiction
search, which returns well-populated records by construction. Open
Library as a whole is mostly bare catalogue rows - an edition with a
title, a year and nothing else - and those are worse than useless here:
a work with no description gets embedded from its own title, which is
how "Liar" and "Ghost Writer" surfaced for "unreliable narrator".

So this counts, before anything is imported, how many works carry
enough content to rank. That number decides the rest: the catalogue
size, the embedding hours, whether the dataset ships as its own
artifact, and whether a NAS can hold it.

    python -m pipeline.survey_openlibrary --limit 200000
    python -m pipeline.survey_openlibrary            # the whole dump

Reads the published works dump by streaming, so nothing is stored and
no API is hammered: Open Library asks that bulk users take the dumps
rather than paginate the search endpoint, and at tens of millions of
works that is the only honest option anyway.

A --limit reads the head of the dump, which is ordered by key and so
skews towards older records. It is an estimate; the unbounded run is
the measurement.
"""

import argparse
import statistics
import time

from pipeline.openlibrary import (
    DUMP_URL,
    MIN_DESCRIPTION,
    description_of,
    iter_works,
)

# Candidate floors, counted together. A full survey is hours, so
# re-running it per threshold is not a way to choose one.
#
# 200 characters was chosen to exclude taglines, not to select good
# records, and the difference decides embedding hours, how many release
# assets it takes to ship, and what competes for a place in the results.
#
# The (0, n) rows size the question the floor glosses over: a book with
# a short description but eight subjects is NOT the degenerate case.
# pipeline.embed puts genres and tags before the description and only
# falls back to the title when there is no description at all, so such
# a row has a real vector. How many books a 200-character floor throws
# away for a failure they do not have is a number, not an opinion.
THRESHOLDS = [
    (0, 5),
    (0, 8),
    (50, 3),
    (50, 8),
    (100, 3),
    (200, 1),
    (200, 3),
    (400, 1),
    (400, 3),
    (600, 1),
    (600, 3),
    (1000, 3),
]


def survey(limit: int | None, min_description: int) -> dict:
    counts = {"rows": 0, "parsed": 0, "with_description": 0, "with_subjects": 0, "usable": 0}
    lengths: list[int] = []
    subject_counts: list[int] = []
    grid = dict.fromkeys(THRESHOLDS, 0)
    started = time.monotonic()

    for rows, work in iter_works(limit, status=counts):
        counts["rows"] = rows
        counts["parsed"] += 1
        description = description_of(work)
        subjects = work.get("subjects") or []
        if len(description) >= min_description:
            counts["with_description"] += 1
        if subjects:
            counts["with_subjects"] += 1
        if len(description) >= min_description and subjects:
            counts["usable"] += 1
            lengths.append(len(description))
            subject_counts.append(len(subjects))
        for chars, tags in THRESHOLDS:
            if len(description) >= chars and len(subjects) >= tags:
                grid[(chars, tags)] += 1
        if rows % 250_000 == 0:
            rate = rows / max(time.monotonic() - started, 1e-9)
            print(
                f"  {rows:>10,} rows  {counts['usable']:>9,} usable  {rate:,.0f} rows/s",
                flush=True,
            )

    counts["median_length"] = int(statistics.median(lengths)) if lengths else 0
    counts["median_subjects"] = int(statistics.median(subject_counts)) if subject_counts else 0
    counts["seconds"] = round(time.monotonic() - started, 1)
    counts["grid"] = grid
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Count the rankable part of Open Library")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many rows (0 = all)")
    parser.add_argument("--min-description", type=int, default=MIN_DESCRIPTION)
    args = parser.parse_args()

    print(f"streaming {DUMP_URL}")
    counts = survey(args.limit or None, args.min_description)
    rows = counts["parsed"] or 1
    print()
    print(f"rows read            {counts['rows']:>12,}")
    print(f"works parsed         {counts['parsed']:>12,}")
    print(
        f"with a description   {counts['with_description']:>12,}"
        f"  ({counts['with_description'] / rows:.1%})"
    )
    print(
        f"with subjects        {counts['with_subjects']:>12,}"
        f"  ({counts['with_subjects'] / rows:.1%})"
    )
    print(f"usable (both)        {counts['usable']:>12,}  ({counts['usable'] / rows:.1%})")
    print(f"median description   {counts['median_length']:>12,} chars")
    print(f"median subjects      {counts['median_subjects']:>12,}")
    print(f"elapsed              {counts['seconds']:>12,} s")
    if counts.get("truncated"):
        print(f"\nPARTIAL: {counts['truncated']}")
        print("Shares above are of the rows that were read, not of the whole dump.")
    grid = counts.get("grid") or {}
    if grid:
        print("\nwhat each floor would keep, of the rows read:")
        for (chars, tags), kept in grid.items():
            share = kept / rows
            hours = kept / 165_000 * (70 / 60)
            print(
                f"  {chars:>5}+ chars, {tags}+ subjects   {kept:>10,}"
                f"  ({share:.2%})   ~{hours:.1f}h to embed"
            )

    if counts["usable"]:
        # The anime catalogue took roughly 70 minutes of CI to embed at
        # ~165,000 entries; that is the only rate measured on this
        # project, so it is the one used here.
        hours = counts["usable"] / 165_000 * (70 / 60)
        print(f"\nembedding this many at the measured anime rate: about {hours:.1f} hours")


if __name__ == "__main__":
    main()
