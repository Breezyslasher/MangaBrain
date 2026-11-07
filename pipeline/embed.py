"""Generate synopsis embeddings for media rows that lack one.

Embeddings are versioned by model name: a media row without a row for the
configured EMBED_MODEL is treated as missing and embedded, so switching
models is a rerun of this script, not a schema change. New-version rows are
inserted ALONGSIDE the old version's (composite primary key), so the API
keeps serving the complete old embedding space until the new one has
coverage; once this script reports nothing left to embed, retire the old
rows with --prune-stale.

Usage:
    python -m pipeline.embed
    python -m pipeline.embed --prune-stale   # embed the rest, then drop old versions
    python -m pipeline.embed --source tvmaze --source tmdb   # TV and films only

Catalogues are embedded in SOURCE_ORDER: AniList, then TV (TVmaze), then
films and TMDB's extra shows, then books, so a new medium does not wait
days behind the books.
"""

import argparse
import os
import time
from collections.abc import Iterable, Mapping

import psycopg
from pgvector.psycopg import register_vector

from api.config import embed_model_id, settings

MAX_TEXT_CHARS = 4000
MAX_TAGS = 15

# Transformer attention memory scales with the square of sequence length, and
# bge-class models take 512-token inputs (twice MiniLM's 256), so encoding at
# batch 64 risks OOM on small hosts that share memory with the database and
# sync jobs. 16 keeps peak memory modest at minor throughput cost; raise via
# --encode-batch-size on beefier machines.
ENCODE_BATCH_SIZE = 16

# Which catalogues are embedded first. Smallest and most used first, so a
# new medium is usable in hours rather than queued behind a large one: on
# a NAS embedding ~3 books a second, the ~965,000-book catalogue is days of
# work, and in plain id order it held up TV and films (which take ids
# after the books) until it finished. Sources not named here go last.
SOURCE_ORDER = ("anilist", "tvmaze", "tmdb", "openlibrary")

# Keyset: each batch starts after the last id the previous one returned for
# its source. Without it every batch began at the lowest id and stepped
# over every row already embedded - the anime catalogue, then each film
# done so far - checking each against the embeddings index. That grew
# with the pass: measured on a NAS through an SSH tunnel at 4.4 rows/s
# with a GPU idle between batches.
SELECT_MISSING_SQL = """
    SELECT m.id, m.title_romaji, m.title_english, m.description_clean, m.genres, m.tags
    FROM media m
    LEFT JOIN embeddings e
        ON e.media_id = m.id AND e.embed_model = %s
    WHERE e.media_id IS NULL AND m.source = %s AND m.id > %s
    ORDER BY m.id
    LIMIT %s
"""

# Anything from a source SOURCE_ORDER does not name, after all of those.
SELECT_MISSING_OTHER_SQL = """
    SELECT m.id, m.title_romaji, m.title_english, m.description_clean, m.genres, m.tags
    FROM media m
    LEFT JOIN embeddings e
        ON e.media_id = m.id AND e.embed_model = %s
    WHERE e.media_id IS NULL AND NOT (m.source = ANY(%s)) AND m.id > %s
    ORDER BY m.id
    LIMIT %s
"""

# Below every id: media ids are positive.
START = -1
OTHER = "\0other"


def next_batch(
    conn: psycopg.Connection,
    stored_id: str,
    batch_size: int,
    sources: tuple[str, ...] | None,
    position: dict[str, int | None] | None = None,
) -> list:
    """The next rows to embed: the first source in SOURCE_ORDER with any
    left, then any other source. `sources` limits the pass to those.

    `position` carries a pass's place from batch to batch: per source, the
    last id returned, or None once the source has nothing left (a batch
    shorter than asked for is its remainder). A pass embeds every row it is
    handed, so nothing behind that id needs looking at again. Rows another
    process adds or clears meanwhile wait for the next pass.
    """
    position = {} if position is None else position

    def take(key: str, sql: str, scope) -> list:
        after = position.get(key, START)
        if after is None:
            return []
        rows = conn.execute(sql, (stored_id, scope, after, batch_size)).fetchall()
        # A short batch is the source's remainder: it is done for this
        # pass. Without this a pass chased a running AniList sync, which
        # clears the embeddings of changed titles in id order just ahead
        # of the cursor - batches of 6, 10, 14 rows, a minute apiece, and
        # the 1.6 million rows behind them never reached.
        position[key] = rows[-1][0] if len(rows) >= batch_size else None
        return rows

    for source in SOURCE_ORDER:
        if sources is not None and source not in sources:
            continue
        rows = take(source, SELECT_MISSING_SQL, source)
        if rows:
            return rows
    if sources is not None:
        for source in (s for s in sources if s not in SOURCE_ORDER):
            rows = take(source, SELECT_MISSING_SQL, source)
            if rows:
                return rows
        return []
    return take(OTHER, SELECT_MISSING_OTHER_SQL, list(SOURCE_ORDER))


UPSERT_SQL = """
    INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)
    VALUES (%s, %s, %s, now())
    ON CONFLICT (media_id, embed_model) DO UPDATE SET
        embedding = EXCLUDED.embedding,
        updated_at = now()
"""

PRUNE_STALE_SQL = "DELETE FROM embeddings WHERE embed_model <> %s"

# How much work the pass has ahead of it, so progress can be reported as a
# fraction with an ETA instead of an unanchored running count.
PENDING_COUNT_SQL = """
    SELECT count(*)
    FROM media m
    LEFT JOIN embeddings e
        ON e.media_id = m.id AND e.embed_model = %s
    WHERE e.media_id IS NULL AND (%s::text[] IS NULL OR m.source = ANY(%s::text[]))
"""

# The whole catalog, so progress is reported against the migration rather
# than against one pass. A re-embed that spans several runs (the CI step
# timeout splits it deliberately) would otherwise restart at 0% every time.
CATALOG_COUNT_SQL = "SELECT count(*) FROM media"


def embed_text(
    title_romaji: str | None,
    title_english: str | None,
    description: str | None,
    genres: Iterable[str] | None = None,
    tags: Iterable[Mapping] | None = None,
) -> str:
    """Compose the text a row is embedded from. Genres and the top ranked
    tags come before the synopsis: the model truncates long input, and the
    compact signals must survive while a synopsis tail can be cut. This
    also sharpens titles whose synopsis is vague (the Death Note problem).

    Titles are omitted whenever a synopsis exists: title strings leak into
    semantic similarity through shared subwords ("Berserk" pulling in "Tales
    of Berseria") without adding content signal. They remain the fallback
    for rows that have no synopsis at all."""
    parts = [] if description else [p for p in (title_romaji, title_english) if p]
    genres = list(genres or [])
    if genres:
        parts.append("Genres: " + ", ".join(genres))
    ranked = sorted(
        (t for t in tags or [] if t.get("name")),
        key=lambda t: t.get("rank") or 0,
        reverse=True,
    )
    if ranked:
        parts.append("Themes: " + ", ".join(t["name"] for t in ranked[:MAX_TAGS]))
    if description:
        parts.append(description)
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def torch_thread_count(configured: int, cpu_count: int | None) -> int:
    """Threads torch gets for encoding: the configured value, or all cores
    minus one so the host stays responsive during a long embed pass."""
    if configured > 0:
        return configured
    return max(1, (cpu_count or 2) - 1)


def progress_line(
    done: int,
    pending: int,
    elapsed: float,
    already: int = 0,
    catalog_total: int | None = None,
    label: str = "embed",
) -> str:
    """Human-readable progress: fraction, percent, rate and ETA.

    The fraction is reported against the whole catalog (already embedded
    plus this pass's work), so a re-embed split across several runs shows
    one continuous 0-100% climb instead of restarting at 0% per run. Rate
    and ETA come from this pass, but the ETA still covers all remaining
    work, since `pending` counts everything the migration has left.
    """
    total = catalog_total if catalog_total is not None else pending
    position = already + done
    rate = done / elapsed if elapsed > 0 else 0.0
    parts = [f"[{label}] {position}/{total}"]
    if total > 0:
        parts.append(f"({100.0 * position / total:.1f}%)")
    if rate > 0:
        remaining = max(pending - done, 0)
        parts.append(f"{rate:.1f}/s")
        parts.append(f"ETA {remaining / rate / 60:.0f}m")
    return " ".join(parts)


def upsert_params(rows, stored_id: str, vectors) -> list[tuple]:
    """One parameter tuple per row, shaped to UPSERT_SQL's placeholders.

    Its own function so a test can hold it against the SQL. It carried a
    fourth value from 5 Nov 2026 - an extra copy of the vector for a
    binary_quantize column - and when that column was reverted the SQL
    lost its placeholder but this tuple kept the value. psycopg rejects a
    mismatch outright ("the query has 3 placeholders but 4 parameters
    were passed"), so every embedding pass died on its first batch. The
    source-text tests passed throughout: they read the SQL, and the SQL
    was correct. Nothing checked the two against each other.
    """
    return [(row[0], stored_id, vec) for row, vec in zip(rows, vectors, strict=True)]


def embed_missing(
    batch_size: int = 256,
    model_name: str | None = None,
    encode_batch_size: int = ENCODE_BATCH_SIZE,
    max_seconds: float | None = None,
    sources: tuple[str, ...] | None = None,
) -> int:
    """Embed every row missing a current-version embedding, catalogue by
    catalogue in SOURCE_ORDER (or only `sources`, when given).

    max_seconds stops the pass between batches once that much time has
    gone, leaving the rest for the next run. The CI jobs need it: a step
    killed at its timeout loses nothing already committed, but it FAILS,
    and a failure cannot be told apart from a real crash - so the resume
    chain would also re-dispatch on a bug. Stopping on a budget succeeds,
    and the caller asks how much is left.
    """
    # Imported lazily: pulls in torch, which the API service never needs.
    import torch
    from sentence_transformers import SentenceTransformer

    threads = torch_thread_count(settings.embed_threads, os.cpu_count())
    torch.set_num_threads(threads)
    print(f"[embed] torch limited to {threads} threads", flush=True)

    model_name = model_name or settings.embed_model
    stored_id = embed_model_id(model_name)
    model = SentenceTransformer(model_name)
    total = 0
    with psycopg.connect(settings.database_url) as conn:
        register_vector(conn)
        only = list(sources) if sources is not None else None
        pending = conn.execute(PENDING_COUNT_SQL, (stored_id, only, only)).fetchone()[0]
        catalog_total = conn.execute(CATALOG_COUNT_SQL).fetchone()[0]
        already = catalog_total - pending
        print(
            f"[embed] {pending} entries to embed with {stored_id}"
            f" ({already}/{catalog_total} already current)",
            flush=True,
        )
        started = time.monotonic()
        position: dict[str, int | None] = {}
        while True:
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                print(
                    f"[embed] stopping at the {max_seconds:.0f}s budget;"
                    f" {max(pending - total, 0)} left for the next run",
                    flush=True,
                )
                break
            rows = next_batch(conn, stored_id, batch_size, sources, position)
            if not rows:
                break
            texts = [embed_text(row[1], row[2], row[3], row[4], row[5]) for row in rows]
            vectors = model.encode(texts, batch_size=encode_batch_size, normalize_embeddings=True)
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, upsert_params(rows, stored_id, vectors))
            conn.commit()
            total += len(rows)
            print(
                progress_line(total, pending, time.monotonic() - started, already, catalog_total),
                flush=True,
            )
    print(f"[embed] done, {total} new/updated embeddings ({stored_id})", flush=True)
    return total


def prune_stale(model_name: str | None = None) -> int:
    """Delete embeddings of every version except the configured one.

    Only safe once embed_missing reports nothing left: until then the old
    rows are what recommendations serve from during a re-embed migration.
    """
    stored_id = embed_model_id(model_name or settings.embed_model)
    with psycopg.connect(settings.database_url) as conn:
        cur = conn.execute(PRUNE_STALE_SQL, (stored_id,))
        conn.commit()
        print(f"[embed] pruned {cur.rowcount} stale embeddings (kept {stored_id})", flush=True)
        return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate missing embeddings")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--encode-batch-size", type=int, default=ENCODE_BATCH_SIZE)
    parser.add_argument("--model", default=None, help="override EMBED_MODEL")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="stop between batches after this long; the rest waits for the next run",
    )
    parser.add_argument(
        "--source",
        action="append",
        help=(
            "embed only this catalogue (anilist, tvmaze, tmdb, openlibrary); repeat for"
            " several. Default: all of them, in that order."
        ),
    )
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="after embedding completes, delete rows of other embedding versions",
    )
    args = parser.parse_args()
    embed_missing(
        batch_size=args.batch_size,
        model_name=args.model,
        encode_batch_size=args.encode_batch_size,
        max_seconds=args.max_seconds,
        sources=tuple(args.source) if args.source else None,
    )
    # Pruning needs a complete new version, and a budgeted stop is by
    # definition not one, so a timed pass never prunes.
    if args.prune_stale and args.max_seconds is None and not args.source:
        prune_stale(args.model)


if __name__ == "__main__":
    main()
