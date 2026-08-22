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
"""

import argparse
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

SELECT_MISSING_SQL = """
    SELECT m.id, m.title_romaji, m.title_english, m.description_clean, m.genres, m.tags
    FROM media m
    LEFT JOIN embeddings e
        ON e.media_id = m.id AND e.embed_model = %s
    WHERE e.media_id IS NULL
    ORDER BY m.id
    LIMIT %s
"""

UPSERT_SQL = """
    INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)
    VALUES (%s, %s, %s, now())
    ON CONFLICT (media_id, embed_model) DO UPDATE SET
        embedding = EXCLUDED.embedding,
        updated_at = now()
"""

PRUNE_STALE_SQL = "DELETE FROM embeddings WHERE embed_model <> %s"


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


def embed_missing(
    batch_size: int = 256,
    model_name: str | None = None,
    encode_batch_size: int = ENCODE_BATCH_SIZE,
) -> int:
    # Imported lazily: pulls in torch, which the API service never needs.
    from sentence_transformers import SentenceTransformer

    model_name = model_name or settings.embed_model
    stored_id = embed_model_id(model_name)
    model = SentenceTransformer(model_name)
    total = 0
    with psycopg.connect(settings.database_url) as conn:
        register_vector(conn)
        while True:
            rows = conn.execute(SELECT_MISSING_SQL, (stored_id, batch_size)).fetchall()
            if not rows:
                break
            texts = [embed_text(row[1], row[2], row[3], row[4], row[5]) for row in rows]
            vectors = model.encode(texts, batch_size=encode_batch_size, normalize_embeddings=True)
            with conn.cursor() as cur:
                cur.executemany(
                    UPSERT_SQL,
                    [(row[0], stored_id, vec) for row, vec in zip(rows, vectors, strict=True)],
                )
            conn.commit()
            total += len(rows)
            print(f"[embed] {total} embeddings written")
    print(f"[embed] done, {total} new/updated embeddings ({stored_id})")
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
        print(f"[embed] pruned {cur.rowcount} stale embeddings (kept {stored_id})")
        return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate missing embeddings")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--encode-batch-size", type=int, default=ENCODE_BATCH_SIZE)
    parser.add_argument("--model", default=None, help="override EMBED_MODEL")
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
    )
    if args.prune_stale:
        prune_stale(args.model)


if __name__ == "__main__":
    main()
