"""Find titles from a free-text description, against the live catalog.

The recommender already embeds every title into one space; nothing stops a
sentence a person types being embedded into that same space and ranked
against it. This is that, as a command-line tool, so the idea can be judged
on real output before it costs an API endpoint, a UI, and torch in the API
container.

    python -m pipeline.describe "a quiet story about grief in a seaside town"
    python -m pipeline.describe --medium manga --limit 20 "revenge, court intrigue"

Two things to keep in mind when reading the output.

The scores are raw query-document cosines. They are NOT the percentages the
app shows: display_similarity calibrates a blended seed-to-title score, and
applying that curve to a different kind of comparison would invent
confidence that was never measured. Ordering is what matters here.

The comparison is also asymmetric in a way the rest of the engine is not.
Stored text is "Genres: ...\\nThemes: ...\\n<synopsis>", built by
pipeline.embed.embed_text; a typed description is an ordinary sentence.
Seed-to-title similarity compares two documents of the same shape, and this
does not, so quality has to be judged here rather than assumed from the
benchmark numbers that chose the model.
"""

import argparse

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from api.config import embed_model_id, settings
from api.filters import build_filters
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS

# Matches the recommender: pgvector's default ef_search (40) stops the scan
# long before a filtered query has collected its rows.
EF_SEARCH = 500


def build_sql(dim: int, filter_sql: str) -> str:
    """The ranking query.

    The dimension is inlined as a literal rather than bound, because the ANN
    index is a per-dimension partial expression index and the planner only
    uses it when the ORDER BY cast and the vector_dims predicate match the
    index definition textually. Everything else is a bound parameter.
    """
    return f"""
        SELECT m.id, m.title_romaji, m.title_english, m.medium, m.format,
               m.start_year, m.average_score,
               1 - (e.embedding <=> %(query_vec)s) AS similarity
        FROM media m
        JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
        WHERE m.medium = ANY(%(mediums)s)
          AND vector_dims(e.embedding) = {dim}
          {filter_sql}
        ORDER BY e.embedding::vector({dim}) <=> %(query_vec)s
        LIMIT %(limit)s
    """


def encode(text: str):
    """Embed one query with the production model.

    Imported here, not at module scope: torch costs seconds and hundreds of
    megabytes, and --help, the tests, and anything importing build_sql
    should pay neither.
    """
    import os

    import torch
    from sentence_transformers import SentenceTransformer

    from pipeline.embed import torch_thread_count

    torch.set_num_threads(torch_thread_count(settings.embed_threads, os.cpu_count()))
    model = SentenceTransformer(settings.embed_model)
    # normalize_embeddings mirrors pipeline.embed: the stored vectors are
    # unit length, and mixing normalized with unnormalized would make cosine
    # distance disagree with what the index was built on.
    return model.encode([text], normalize_embeddings=True)[0]


def describe(text: str, medium: str | None, limit: int, adult: bool) -> list[dict]:
    vector = encode(text)
    filter_sql, filter_params = build_filters(adult=adult)
    sql = build_sql(int(vector.shape[0]), filter_sql)
    params = {
        "query_vec": vector,
        "model": embed_model_id(),
        "mediums": MEDIUM_GROUPS[medium] if medium else ALL_MEDIUMS,
        "limit": limit,
        **filter_params,
    }
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        register_vector(conn)
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(EF_SEARCH),))
        return conn.execute(sql, params).fetchall()


def format_row(index: int, row: dict) -> str:
    title = row["title_english"] or row["title_romaji"] or f"#{row['id']}"
    bits = [str(row["start_year"] or "----"), row["medium"]]
    if row["format"]:
        bits.append(row["format"])
    return f"{index:>3}. {row['similarity']:.3f}  {title}  ({' / '.join(bits)})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Find titles from a description")
    parser.add_argument("text", nargs="+", help="what you are in the mood for")
    parser.add_argument("--medium", choices=sorted(MEDIUM_GROUPS), default=None)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--adult", action="store_true", help="include adult titles")
    args = parser.parse_args()

    rows = describe(" ".join(args.text), args.medium, args.limit, args.adult)
    if not rows:
        print("no matches; is the catalog embedded?")
        return
    for index, row in enumerate(rows, start=1):
        print(format_row(index, row))


if __name__ == "__main__":
    main()
