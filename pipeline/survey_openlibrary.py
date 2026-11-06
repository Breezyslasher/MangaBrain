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
import gzip
import io
import json
import statistics
import time

import httpx

DUMP_URL = "https://openlibrary.org/data/ol_dump_works_latest.txt.gz"

# The same floor the source probe used, and for the same reason: below
# this a description is a tagline, not something to embed.
MIN_DESCRIPTION = 200

# 200 characters was chosen to exclude taglines, not to select good
# records, and the difference matters: at 2.32% of a ~40M-work dump the
# catalogue is around 930,000 books, which is 5.6x the anime and manga
# catalogue combined and more vector than a single release asset can
# hold. Where to put the floor is a judgement about result quality, and
# it should be made against numbers rather than re-run for hours per
# guess. Each pass now counts every combination.
THRESHOLDS = [(200, 1), (200, 3), (400, 1), (400, 3), (600, 1), (600, 3), (1000, 3)]


class ChunkReader(io.RawIOBase):
    """A file-like view over httpx's chunk iterator.

    gzip.GzipFile wants an object with .read(); iter_raw() yields bytes.
    Passing the generator straight in raised "'generator' object has no
    attribute 'read'" on the first byte of a multi-gigabyte download,
    which is a cheap failure but only at runtime - hence the test that
    drives real gzip bytes through this in chunks.
    """

    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def description_of(work: dict) -> str:
    """Open Library stores a description as a string or as {value: ...}."""
    value = work.get("description")
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def survey(limit: int | None, min_description: int) -> dict:
    counts = {"rows": 0, "parsed": 0, "with_description": 0, "with_subjects": 0, "usable": 0}
    lengths: list[int] = []
    subject_counts: list[int] = []
    grid = dict.fromkeys(THRESHOLDS, 0)
    started = time.monotonic()

    with httpx.stream("GET", DUMP_URL, timeout=120.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        reader = io.BufferedReader(ChunkReader(resp.iter_raw()), buffer_size=1 << 20)
        with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
            for raw in stream:
                counts["rows"] += 1
                if limit and counts["rows"] > limit:
                    break
                # Five tab-separated columns; the JSON is the last.
                parts = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                try:
                    work = json.loads(parts[4])
                except ValueError:
                    continue
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
                if counts["rows"] % 250_000 == 0:
                    rate = counts["rows"] / max(time.monotonic() - started, 1e-9)
                    print(
                        f"  {counts['rows']:>10,} rows  {counts['usable']:>9,} usable"
                        f"  {rate:,.0f} rows/s",
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
