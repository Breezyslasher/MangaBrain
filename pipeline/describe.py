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

# Every facet is its own ANN scan, so a long comma list multiplies the
# cost of one request. Prose is the better input anyway.
MAX_FACETS = 5

# How deep each facet's ranking goes before fusion.
#
# 200 was far too shallow and the first real run showed it: over a 165k
# catalog, three 200-row pools almost never intersect, so every result
# came back "1/3 facets" and fusion was just interleaving three unrelated
# lists. Overlap is the entire mechanism, and overlap needs depth. The
# pool query carries ids only, so this costs a deeper index scan rather
# than 6000 synopses in memory.
FACET_POOL = 2000


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


def build_rows_sql() -> str:
    """Full rows for a handful of ids, once the ranking is decided.

    Separate from the pool query because the pool is now thousands of rows
    per facet and all it needs is their ids: pulling every synopsis through
    only to discard 99% of them is the same waste the recommender makes
    with its 500-row candidate fetch.
    """
    from api.db import MEDIA_COLS

    # The full column list, because the API builds MediaOut from these
    # rows: a trimmed set would serve a response with no cover and no
    # genres. Only the handful of rows that survived ranking are fetched,
    # so the width costs nothing.
    return f"""
        SELECT {MEDIA_COLS}
        FROM media m
        WHERE m.id = ANY(%(ids)s)
    """


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
    synopsis_sql = "" if not require_synopsis else (" AND coalesce(m.description_clean, '') <> ''")
    return f"""
        SELECT m.id,
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
    """Embed the facets, through the same loader the API uses.

    Imported inside the function, not at module scope: that import pulls in
    torch, and --help, the tests and anything importing build_sql should
    pay neither the seconds nor the hundreds of megabytes.
    """
    from api.embedding import encode_queries

    return encode_queries(texts)


def rank(
    conn,
    vectors,
    *,
    mediums: list[str],
    limit: int,
    filter_sql: str,
    filter_params: dict,
    require_synopsis: bool = True,
) -> list[dict]:
    """Rank and fuse, on a connection the caller owns.

    Split out from describe() so the API serves this from its pool rather
    than opening its own connection, and so there is one implementation of
    the fusion rather than one per client.
    """
    from api.db import hnsw_supports_iterative_scan

    sql = build_sql(int(vectors[0].shape[0]), filter_sql, require_synopsis)
    facet_count = len(vectors)
    pool = limit if facet_count == 1 else FACET_POOL

    ef_search = min(max(pool, 40), 1000)
    conn.execute("SELECT set_config(%s, %s, true)", ("hnsw.ef_search", str(ef_search)))
    if hnsw_supports_iterative_scan(conn):
        # Without this the scan stops at ef_search rows, which is fewer
        # than a deep facet pool asks for.
        conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")

    rankings: list[list[int]] = []
    best_similarity: dict[int, float] = {}
    for vector in vectors:
        rows = conn.execute(
            sql,
            {
                "query_vec": vector,
                "model": embed_model_id(),
                "mediums": mediums,
                "limit": pool,
                **filter_params,
            },
        ).fetchall()
        rankings.append([row["id"] for row in rows])
        for row in rows:
            previous = best_similarity.get(row["id"])
            if previous is None or row["similarity"] > previous:
                best_similarity[row["id"]] = row["similarity"]

    if facet_count == 1:
        ordered = rankings[0][:limit]
        scores: dict[int, float] = {}
        hits = dict.fromkeys(ordered, 1)
    else:
        scores = fuse(rankings)
        hits = {media_id: sum(media_id in r for r in rankings) for media_id in scores}
        # Coverage first, fused rank second. Pure RRF lets one facet's
        # first place (1/61) outscore a title found by two facets deep in
        # their pools (1/210 + 1/240), which is the opposite of what
        # someone asking for three things at once means.
        ordered = sorted(scores, key=lambda i: (-hits[i], -scores[i], i))[:limit]

    display = conn.execute(build_rows_sql(), {"ids": ordered}).fetchall()
    by_id = {row["id"]: row for row in display}
    return [
        {
            **by_id[i],
            "similarity": best_similarity.get(i),
            "rrf": scores.get(i),
            "facets_hit": hits[i],
            "facet_count": facet_count,
        }
        for i in ordered
        if i in by_id
    ]


def describe(
    text: str,
    medium: str | None,
    limit: int,
    adult: bool,
    require_synopsis: bool = True,
) -> list[dict]:
    """Rank the catalog against a description, on a connection of our own.

    One facet is one ANN scan. Several facets are one scan each, fused by
    rank - more expensive, and the only way a facet the catalog barely
    covers survives next to one it covers thousands of times over.
    """
    facets = split_facets(text)
    vectors = encode(facets)
    filter_sql, filter_params = build_filters(adult=adult)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        register_vector(conn)
        return rank(
            conn,
            vectors,
            mediums=MEDIUM_GROUPS[medium] if medium else ALL_MEDIUMS,
            limit=limit,
            filter_sql=filter_sql,
            filter_params=filter_params,
            require_synopsis=require_synopsis,
        )


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
    synopsis = " ".join((row.get("description") or "").split())
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
    parser.add_argument("--why", action="store_true", help="print the opening of each synopsis")
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
