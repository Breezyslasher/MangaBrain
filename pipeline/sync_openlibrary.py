"""Import books from Open Library's works dump.

Measured first (pipeline.survey_openlibrary, 12,000,000 rows, 6 Nov
2026): 2.6% of works carry a description of 200+ characters, 49.6%
carry subjects, 2.3% carry both. Median description 665 characters,
median 6 subjects. Scaled to the ~40M-work dump that is roughly 930,000
books - 5.6x the anime and manga catalogue - and raising the floor does
not cut much, because a work that has a description usually has a real
one: 600 characters only removes 45%.

So the floor stays where the measurement put it. A work needs a
description long enough to embed and at least one subject to compare,
and nothing else is filtered: no popularity, no edition count, no
curation.

    python -m pipeline.sync_openlibrary --limit 50000    # a slice, to look at
    python -m pipeline.sync_openlibrary                  # the whole dump

Resumable in the sense that matters: rows are upserted on
(source, source_id), so a re-run corrects and continues rather than
duplicating. It is not resumable in position - the dump is read from
the start each time, which at ~388 KB/s is the real cost.
"""

import argparse
import re
import time

from pipeline.openlibrary import MIN_DESCRIPTION, description_of, iter_works

SOURCE = "openlibrary"

# Open Library has no genre vocabulary - everything is a subject, from
# "Fiction" to "Cats in fiction, juvenile". The score reads genres and
# tags as separate terms, so leaving genres empty would silently zero
# one of three for every book. These are the subjects that are genres
# in all but name; the rest stay tags.
GENRE_SUBJECTS = {
    "fiction": "Fiction",
    "nonfiction": "Non-fiction",
    "non-fiction": "Non-fiction",
    "fantasy": "Fantasy",
    "science fiction": "Science Fiction",
    "mystery": "Mystery",
    "detective and mystery stories": "Mystery",
    "horror": "Horror",
    "romance": "Romance",
    "love stories": "Romance",
    "thriller": "Thriller",
    "historical fiction": "Historical",
    "biography": "Biography",
    "autobiography": "Biography",
    "poetry": "Poetry",
    "drama": "Drama",
    "humor": "Comedy",
    "humour": "Comedy",
    "adventure": "Adventure",
    "adventure stories": "Adventure",
    "juvenile fiction": "Children",
    "children's fiction": "Children",
    "young adult fiction": "Young Adult",
    "short stories": "Short Stories",
    "essays": "Essays",
    "travel": "Travel",
    "history": "History",
    "philosophy": "Philosophy",
    "science": "Science",
}

# Tags carry no weight of their own here. AniList ranks each tag 0-100
# by how well it fits, and tag_similarity reads that rank; Open Library
# subjects are an unordered set. A uniform rank says "present, weight
# unknown" rather than inventing an ordering the source does not have.
SUBJECT_RANK = 50

# A description worth embedding, but not an essay: the longest run to
# thousands of characters of publisher copy and review quotes, which
# dilutes the vector rather than sharpening it.
MAX_DESCRIPTION = 4000

UPSERT_SQL = """
    INSERT INTO media (
        id, source, source_id, media_type, medium,
        title_romaji, description, description_clean,
        genres, tags, start_year, cover_image_medium, cover_image_large,
        synonyms, synced_at
    )
    VALUES (
        nextval('media_external_id_seq'), %(source)s, %(source_id)s, 'BOOK', 'book',
        %(title)s, %(description)s, %(description_clean)s,
        %(genres)s, %(tags)s, %(start_year)s, %(cover_medium)s, %(cover_large)s,
        %(synonyms)s, now()
    )
    ON CONFLICT (source, source_id) DO UPDATE SET
        title_romaji = EXCLUDED.title_romaji,
        description = EXCLUDED.description,
        description_clean = EXCLUDED.description_clean,
        genres = EXCLUDED.genres,
        tags = EXCLUDED.tags,
        start_year = EXCLUDED.start_year,
        cover_image_medium = EXCLUDED.cover_image_medium,
        cover_image_large = EXCLUDED.cover_image_large,
        synonyms = EXCLUDED.synonyms,
        synced_at = now()
"""

# Open Library descriptions often end with a provenance footer that is
# not about the book: "----------" then a source line, or a bare
# "([source][1])" link. Embedding those matches books by where their
# blurb came from.
FOOTER = re.compile(r"\n-{3,}.*\Z|\(\[source\]\[\d+\]\).*\Z", re.S)


def clean_description(text: str) -> str:
    return FOOTER.sub("", text).strip()


def year_of(work: dict) -> int | None:
    """A four-digit year from first_publish_date, which is free text.

    Works carry no reliable date - publication belongs to editions - so
    this is best effort and often None. start_year drives the year
    filter, and a wrong year is worse than no year.
    """
    raw = str(work.get("first_publish_date") or "")
    match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", raw)
    return int(match.group(1)) if match else None


def covers_of(work: dict) -> tuple[str | None, str | None]:
    ids = [i for i in (work.get("covers") or []) if isinstance(i, int) and i > 0]
    if not ids:
        return None, None
    base = f"https://covers.openlibrary.org/b/id/{ids[0]}"
    return f"{base}-M.jpg", f"{base}-L.jpg"


def row_for(work: dict, min_description: int) -> dict | None:
    """A media row, or None if the work cannot be ranked."""
    key = str(work.get("key") or "")
    title = str(work.get("title") or "").strip()
    description = clean_description(description_of(work))
    subjects = [str(s).strip() for s in (work.get("subjects") or []) if str(s).strip()]
    if not key or not title or len(description) < min_description or not subjects:
        return None

    genres: list[str] = []
    tags: list[dict] = []
    for subject in subjects:
        genre = GENRE_SUBJECTS.get(subject.lower())
        if genre and genre not in genres:
            genres.append(genre)
        else:
            tags.append({"name": subject, "rank": SUBJECT_RANK, "is_adult": False})

    cover_medium, cover_large = covers_of(work)
    return {
        "source": SOURCE,
        # The work key, "/works/OL45804W", not a parsed number: it is
        # what Open Library calls this and what a reader can look up.
        "source_id": key,
        "title": title,
        "description": description,
        "description_clean": description[:MAX_DESCRIPTION],
        "genres": genres,
        "tags": tags,
        "start_year": year_of(work),
        "cover_medium": cover_medium,
        "cover_large": cover_large,
        "synonyms": [],
    }


def sync(limit_rows: int | None, min_description: int, batch: int = 1000) -> int:
    import json

    import psycopg

    from api.config import settings

    written = skipped = 0
    started = time.monotonic()
    status: dict = {}
    pending: list[dict] = []

    with psycopg.connect(settings.database_url) as conn:
        for rows, work in iter_works(limit_rows, status=status):
            row = row_for(work, min_description)
            if row is None:
                skipped += 1
                continue
            row["tags"] = json.dumps(row["tags"])
            pending.append(row)
            if len(pending) >= batch:
                with conn.cursor() as cur:
                    cur.executemany(UPSERT_SQL, pending)
                conn.commit()
                written += len(pending)
                pending.clear()
                rate = rows / max(time.monotonic() - started, 1e-9)
                print(
                    f"  {rows:>10,} rows  {written:>8,} books  {rate:,.0f} rows/s",
                    flush=True,
                )
        if pending:
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, pending)
            conn.commit()
            written += len(pending)

    print(f"\nwrote {written:,} books; skipped {skipped:,} works with too little to rank")
    if status.get("truncated"):
        print(f"PARTIAL: {status['truncated']} - re-run to continue from the start")
    print("run python -m pipeline.embed next; nothing is rankable until it is embedded")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Import books from Open Library")
    parser.add_argument("--limit", type=int, default=0, help="dump rows to read (0 = all)")
    parser.add_argument("--min-description", type=int, default=MIN_DESCRIPTION)
    parser.add_argument("--batch", type=int, default=1000)
    args = parser.parse_args()
    sync(args.limit or None, args.min_description, args.batch)


if __name__ == "__main__":
    main()
