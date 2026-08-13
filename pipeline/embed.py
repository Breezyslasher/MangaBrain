"""Generate synopsis embeddings for media rows that lack one.

Embeddings are versioned by model name: a row whose embed_model differs from
the configured EMBED_MODEL is treated as missing and re-embedded, so switching
models is a rerun of this script, not a schema change.

Usage:
    python -m pipeline.embed
"""

import argparse
from collections.abc import Iterable, Mapping

import psycopg
from pgvector.psycopg import register_vector

from api.config import embed_model_id, settings

MAX_TEXT_CHARS = 4000
MAX_TAGS = 15

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
    ON CONFLICT (media_id) DO UPDATE SET
        embed_model = EXCLUDED.embed_model,
        embedding = EXCLUDED.embedding,
        updated_at = now()
"""


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
    also sharpens titles whose synopsis is vague (the Death Note problem)."""
    parts = [p for p in (title_romaji, title_english) if p]
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


def embed_missing(batch_size: int = 256, model_name: str | None = None) -> int:
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
            vectors = model.encode(texts, batch_size=64, normalize_embeddings=True)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate missing embeddings")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--model", default=None, help="override EMBED_MODEL")
    args = parser.parse_args()
    embed_missing(batch_size=args.batch_size, model_name=args.model)


if __name__ == "__main__":
    main()
