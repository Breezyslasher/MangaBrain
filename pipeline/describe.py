"""Find titles from a free-text description, against the live catalog.

The recommender already embeds every title into one space; nothing stops a
sentence a person types being embedded into that same space and ranked
against it. This is that, as a command-line tool, so the idea can be judged
on real output before it costs an API endpoint, a UI, and torch in the API
container.

    python -m pipeline.describe "a quiet story about grief in a seaside town"
    python -m pipeline.describe --why "a time loop that only one person notices"
    python -m pipeline.describe --medium manga "revenge, court intrigue, unreliable narrator"

A comma-separated query is treated as separate facets rather than averaged
into one vector, because averaging loses the facet the catalog covers
least. Measured: "revenge, court intrigue" peaks at 0.789 against the
thousands of near-identical revenge manhwa, while "unreliable narrator"
peaks at 0.714 against a handful of titles; averaged, the first drowns the
second completely, and the reader who typed both gets only one. Each facet
is ranked separately and the rankings are fused by reciprocal rank, so a
title has to do well on all of them to rise. Ranks are comparable across
facets; raw cosines from different neighbourhoods are not.

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

# Reciprocal rank fusion: score = sum over facets of 1 / (RRF_K + rank).
# K=60 is the constant from the original paper; it damps the top of each
# ranking so one facet's first place cannot outweigh being present in all
# of them, which is the entire point of fusing.
RRF_K = 60

# How deep each facet's ranking goes before fusion. Deeper costs one ANN
# scan per facet either way; this bounds the rows carried in memory.
FACET_POOL = 200


def split_facets(text: str) -> list[str]:
    """Commas separate facets; anything else is one description.

    Prose is the better input and stays a single vector - connected
    meaning is what the model reads best. A comma list is the shape people
    reach for when they want several things at once, and that is the shape
    averaging handles worst.
    """
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return parts or [text.strip()]


def fuse(rankings: list[list[int]]) -> dict[int, float]:
    """Reciprocal rank fusion of per-facet rankings: {media_id: score}."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, media_id in enumerate(ranking, start=1):
            scores[media_id] = scores.get(media_id, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def build_sql(dim: int, filter_sql: str, require_synopsis: bool = True) -> str:
    """The ranking query.

    The dimension is inlined as a literal rather than bound, because the ANN
    index is a per-dimension partial expression index and the planner only
    uses it when the ORDER BY cast and the vector_dims predicate match the
    index definition textually. Everything else is a bound parameter.
    """
    # Titles with no synopsis are embedded from their own name
    # (pipeline.embed.embed_text), so they match a description by
    # coincidence of wording and can say nothing about their content:
    # "Liar" and "Ghost Writer" both surfaced for "unreliable narrator"
    # on the strength of their titles alone. They are excluded here by
    # default, which the recommender does not do - there a seed's own
    # genres and tags still carry real signal.
    synopsis_sql = "" if not require_synopsis else (
        " AND coalesce(m.description_clean, '') <> ''"
    )
    return f"""
        SELECT m.id, m.title_romaji, m.title_english, m.medium, m.format,
               m.start_year, m.average_score, m.description_clean,
               1 - (e.embedding <=> %(query_vec)s) AS similarity
        FROM media m
        JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
        WHERE m.medium = ANY(%(mediums)s)
          AND vector_dims(e.embedding) = {dim}
          {synopsis_sql}
          {filter_sql}
        ORDER BY e.embedding::vector({dim}) <=> %(query_vec)s
        LIMIT %(limit)s
    """


def encode(texts: list[str]):
    """Embed one or more queries with the production model.

    One call for all facets: loading the model costs seconds and encoding
    a handful of short strings costs milliseconds.

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
    return model.encode(texts, normalize_embeddings=True)


def describe(
    text: str,
    medium: str | None,
    limit: int,
    adult: bool,
    require_synopsis: bool = True,
) -> list[dict]:
    """Rank the catalog against a description.

    One facet is one ANN scan. Several facets are one scan each, fused by
    rank - more expensive, and the only way a facet the catalog barely
    covers survives next to one it covers thousands of times over.
    """
    facets = split_facets(text)
    vectors = encode(facets)
    filter_sql, filter_params = build_filters(adult=adult)
    sql = build_sql(int(vectors[0].shape[0]), filter_sql, require_synopsis)
    mediums = MEDIUM_GROUPS[medium] if medium else ALL_MEDIUMS

    rankings: list[list[int]] = []
    rows_by_id: dict[int, dict] = {}
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        register_vector(conn)
        conn.execute("SELECT set_config(%s, %s, true)", ("hnsw.ef_search", str(EF_SEARCH)))
        for vector in vectors:
            rows = conn.execute(
                sql,
                {
                    "query_vec": vector,
                    "model": embed_model_id(),
                    "mediums": mediums,
                    "limit": limit if len(facets) == 1 else FACET_POOL,
                    **filter_params,
                },
            ).fetchall()
            rankings.append([row["id"] for row in rows])
            for row in rows:
                # Keep the best cosine any facet gave a title, for display
                # only: the ordering below is by fused rank, not by this.
                previous = rows_by_id.get(row["id"])
                if previous is None or row["similarity"] > previous["similarity"]:
                    rows_by_id[row["id"]] = row

    if len(facets) == 1:
        return [{**rows_by_id[i], "facets_hit": 1, "facet_count": 1} for i in rankings[0]]

    scores = fuse(rankings)
    hits = {media_id: sum(media_id in r for r in rankings) for media_id in scores}
    ordered = sorted(scores, key=lambda i: (-scores[i], -hits[i], i))[:limit]
    return [
        {**rows_by_id[i], "rrf": scores[i], "facets_hit": hits[i], "facet_count": len(facets)}
        for i in ordered
    ]


SYNOPSIS_CHARS = 220


def format_row(index: int, row: dict, why: bool = False) -> str:
    """One result. With why=True, the opening of the synopsis underneath.

    Judging whether a match is good means knowing what the title is about,
    and a list of unfamiliar names cannot be judged at all - which is the
    only question this tool exists to answer. Titles with no synopsis are
    embedded from their titles alone (pipeline.embed.embed_text), so an
    empty line here is itself the explanation for a weak match.
    """
    title = row["title_english"] or row["title_romaji"] or f"#{row['id']}"
    bits = [str(row["start_year"] or "----"), row["medium"]]
    if row["format"]:
        bits.append(row["format"])
    if row.get("facet_count", 1) > 1:
        # No single cosine exists for a fused result, so the honest summary
        # is how many facets found it at all.
        score = f"{row['facets_hit']}/{row['facet_count']} facets"
    else:
        score = f"{row['similarity']:.3f}"
    line = f"{index:>3}. {score}  {title}  ({' / '.join(bits)})"
    if not why:
        return line
    synopsis = " ".join((row.get("description_clean") or "").split())
    if not synopsis:
        return line + "\n       (no synopsis: embedded from its title alone)"
    if len(synopsis) > SYNOPSIS_CHARS:
        synopsis = synopsis[:SYNOPSIS_CHARS].rsplit(" ", 1)[0] + "..."
    return line + f"\n       {synopsis}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Find titles from a description")
    parser.add_argument("text", nargs="+", help="what you are in the mood for")
    parser.add_argument("--medium", choices=sorted(MEDIUM_GROUPS), default=None)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--adult", action="store_true", help="include adult titles")
    parser.add_argument(
        "--why", action="store_true", help="print the opening of each synopsis"
    )
    parser.add_argument(
        "--include-title-only",
        action="store_true",
        help="also match titles the catalog has no synopsis for",
    )
    args = parser.parse_args()

    rows = describe(
        " ".join(args.text),
        args.medium,
        args.limit,
        args.adult,
        require_synopsis=not args.include_title_only,
    )
    if not rows:
        print("no matches; is the catalog embedded?")
        return
    for index, row in enumerate(rows, start=1):
        print(format_row(index, row, why=args.why))


if __name__ == "__main__":
    main()
