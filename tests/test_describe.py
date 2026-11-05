"""Free-text search over the same embedding space the recommender uses."""

import re
from pathlib import Path

from pipeline.describe import build_sql, format_row

ROOT = Path(__file__).resolve().parent.parent


def test_the_dimension_is_inlined_so_the_ann_index_is_used():
    """The index is a per-dimension partial expression index.

    The planner only uses it when the ORDER BY cast and the vector_dims
    predicate match its definition textually, so the dimension cannot be a
    bound parameter here even though everything else is.
    """
    sql = build_sql(768, "")
    assert "vector_dims(e.embedding) = 768" in sql
    assert "e.embedding::vector(768) <=> %(query_vec)s" in sql
    assert "768" not in sql.replace("vector_dims(e.embedding) = 768", "").replace(
        "e.embedding::vector(768)", ""
    ), "the dimension should appear only where the index definition needs it"


def test_only_the_dimension_is_interpolated():
    """Everything a caller supplies is a bound parameter."""
    sql = build_sql(384, " AND m.is_adult = FALSE")
    placeholders = set(re.findall(r"%\((\w+)\)s", sql))
    assert placeholders == {"query_vec", "model", "mediums", "limit"}


def test_results_are_restricted_to_the_current_embedding_version():
    """Two embedding versions coexist during a re-embed migration, and
    cosine between spaces is meaningless."""
    assert "e.embed_model = %(model)s" in build_sql(768, "")


def test_torch_is_not_imported_at_module_scope():
    """--help, the tests, and anything importing build_sql must not pay for
    torch: it costs seconds and hundreds of megabytes."""
    source = (ROOT / "pipeline/describe.py").read_text()
    module_level = source[: source.index("def build_sql")]
    assert "import torch" not in module_level
    assert "sentence_transformers" not in module_level


def test_a_row_renders_with_missing_fields():
    """Most of the catalog has holes; an unknown year must not crash it."""
    row = {
        "id": 42,
        "title_english": None,
        "title_romaji": "Shoujo Shuumatsu Ryokou",
        "medium": "anime",
        "format": None,
        "start_year": None,
        "similarity": 0.5123,
    }
    line = format_row(1, row)
    assert "Shoujo Shuumatsu Ryokou" in line
    assert "0.512" in line
    assert "----" in line


def test_why_prints_the_synopsis_and_says_when_there_is_none():
    """A list of unfamiliar titles cannot be judged, which is the only
    question this tool exists to answer. An empty synopsis is itself the
    explanation for a weak match: such rows are embedded from their titles."""
    base = {
        "id": 7,
        "title_english": "Example",
        "title_romaji": None,
        "medium": "manga",
        "format": "MANGA",
        "start_year": 2020,
        "similarity": 0.8,
    }
    # "description", not "description_clean": display rows come from
    # MEDIA_COLS, which aliases the cleaned column to the name the API
    # exposes.
    with_text = format_row(1, {**base, "description": "A long way\nfrom home."}, why=True)
    assert "A long way from home." in with_text, "newlines should collapse to one line"

    without = format_row(1, {**base, "description": ""}, why=True)
    assert "no synopsis" in without

    assert "\n" not in format_row(1, {**base, "description": "x"}, why=False)


def test_prose_stays_one_facet_and_commas_split():
    """Connected prose is what the model reads best, so it is not chopped.
    A comma list is the shape people use to ask for several things, and the
    shape averaging handles worst."""
    from pipeline.describe import split_facets

    assert split_facets("a quiet story about grief in a seaside town") == [
        "a quiet story about grief in a seaside town"
    ]
    assert split_facets("revenge, court intrigue, unreliable narrator") == [
        "revenge",
        "court intrigue",
        "unreliable narrator",
    ]
    # Trailing and doubled separators must not produce empty facets, which
    # would embed the empty string and rank the catalog by nothing.
    assert split_facets("revenge,, court intrigue,") == ["revenge", "court intrigue"]
    assert split_facets("   ") == [""]


def test_fusion_prefers_a_title_every_facet_found():
    """The point of fusing: measured, 'revenge' peaks at 0.789 and
    'unreliable narrator' at 0.714, so averaging the vectors lets the first
    drown the second. Ranks are comparable across facets; cosines are not.
    """
    from pipeline.describe import fuse

    # 7 is second and third; 1 and 2 each top one ranking and miss the other.
    scores = fuse([[1, 7, 99], [2, 3, 7]])
    assert scores[7] > scores[1]
    assert scores[7] > scores[2]
    assert scores[1] == scores[2], "the same rank in different facets is worth the same"


def test_a_lone_first_place_cannot_beat_being_in_every_facet():
    """K damps the top of each ranking; without it, fusion would just be
    'whichever facet the catalog covers most densely' again."""
    from pipeline.describe import fuse

    scores = fuse([[1, 2], [2, 3], [2, 4]])
    assert max(scores, key=lambda i: scores[i]) == 2


def test_title_only_rows_are_excluded_by_default():
    """They are embedded from their own name, so they match a description
    by coincidence of wording: 'Liar' and 'Ghost Writer' both surfaced for
    'unreliable narrator' knowing nothing about their content."""
    assert "coalesce(m.description_clean, '') <> ''" in build_sql(768, "")
    assert "description_clean, '') <> ''" not in build_sql(768, "", require_synopsis=False)


def test_a_fused_row_reports_facet_coverage_rather_than_a_cosine():
    """No single cosine exists for a fused result, and inventing one would
    be the same dishonesty as running these through display_similarity."""
    row = {
        "id": 3,
        "title_english": "Example",
        "title_romaji": None,
        "medium": "manga",
        "format": "MANGA",
        "start_year": 2021,
        "similarity": 0.77,
        "rrf": 0.031,
        "facets_hit": 2,
        "facet_count": 3,
    }
    assert "2/3 facets" in format_row(1, row)
    assert "0.77" not in format_row(1, row)


def test_coverage_outranks_a_single_strong_facet():
    """What "three things at once" has to mean.

    Pure RRF gives a facet's first place 1/61 and a title found twice deep
    in two pools 1/210 + 1/240, so the single-facet hit wins - which is how
    a real three-facet run came back almost entirely "1/3 facets". Sorting
    by coverage first is what makes fusion conjunctive.
    """
    from pipeline.describe import fuse

    rankings = [[1] + list(range(100, 200)), list(range(300, 500)) + [250], [250, 999]]
    scores = fuse(rankings)
    hits = {i: sum(i in r for r in rankings) for i in scores}
    ordered = sorted(scores, key=lambda i: (-hits[i], -scores[i], i))
    assert hits[250] == 2
    assert ordered[0] == 250, "a title two facets found must beat one facet's first place"


def test_the_pool_is_deep_enough_for_facets_to_intersect():
    """Overlap is the whole mechanism, and overlap needs depth: three
    200-row pools over a 165k catalog essentially never intersect."""
    from pipeline.describe import FACET_POOL

    assert FACET_POOL >= 1000, "shallow pools make fusion an interleave, not an intersection"


def test_the_pool_query_does_not_carry_synopses():
    """Thousands of rows per facet, of which a handful are displayed."""
    from pipeline.describe import build_rows_sql

    pool_sql = build_sql(768, "")
    assert "description" not in pool_sql.split("FROM")[0], (
        "the pool query should select ids and distance, not synopses"
    )
    assert "description_clean AS description" in build_rows_sql()


def test_coverage_counts_only_a_real_match():
    """A badge of 3/3 has to mean three facets matched.

    The pool is 2,000 rows, roughly a tenth of the anime catalog, and
    ordering is coverage-first - so before this, a title sitting mid-table
    in three pools outranked one that genuinely answered two. Measured on
    a live query: gambling and time-travel donghua at 3/3, with the one
    title that answered the question absent.
    """
    from pipeline.describe import HIT_DEPTH

    assert HIT_DEPTH < 2000, "a hit must be nearer the top than the pool is deep"


def test_a_deep_rank_still_scores_but_does_not_count_as_coverage():
    """Weak evidence is not no evidence: RRF reads the whole pool, while
    the badge reads the top slice only."""
    import inspect

    from pipeline.describe import rank

    source = inspect.getsource(rank)
    assert "fuse(rankings)" in source, "RRF must still see the full rankings"
    assert "ranking[:HIT_DEPTH]" in source, "coverage must read the top slice"


def test_a_one_word_facet_is_still_a_facet():
    """ "mecha, war, politics" is the case comma splitting exists for.

    A minimum facet length was tried and reverted: every part of that
    query is one word, so a length filter rejects exactly what the
    feature is for. Weak facets are handled by HIT_DEPTH instead.
    """
    from pipeline.describe import split_facets

    assert split_facets("mecha, war, politics") == ["mecha", "war", "politics"]


def test_the_pooled_distance_is_computed_once():
    """The SELECT used to repeat the distance without the cast, so every
    pooled row paid twice for the same answer - 6,000 extra computations
    on a three-facet query."""
    from pipeline.describe import build_sql

    sql = build_sql(768, "", True)
    assert sql.count("e.embedding <=> %(query_vec)s") == 0
    assert sql.count("e.embedding::vector(768) <=> %(query_vec)s") == 2


def test_every_benchmark_statistic_has_something_to_average():
    """Removing a measurement must remove its summary line too.

    Deleting the binary stage took the recall computation with it - the
    append sat between the block removed and the prints kept - and the
    benchmark then ran its slowest queries to completion before dying on
    an empty list. A crash at the end of a multi-minute run is the worst
    place to put one.
    """
    import ast
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline/bench_describe.py").read_text()
    averaged = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"mean", "median"}
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            averaged.add(node.args[0].id)
    assert averaged, "expected the benchmark to summarise something"
    for name in sorted(averaged):
        assert re.search(rf"\b{name}\.append\(", source), f"{name} is averaged but never filled"


def test_the_benchmark_hands_rank_numpy_not_whatever_the_driver_returned():
    """pipeline.describe.rank reads vectors[0].shape, and register_vector
    returns a pgvector Vector in some versions.

    This broke twice. The second time it was collateral from removing the
    binary stage: as_array sat between the deleted SQL and the next
    constant, so it went with them, and the sweep - the last thing to run
    - died after minutes of timings had already completed.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline/bench_describe.py").read_text()
    assert "def as_array(" in source
    sampling = source[source.index("vectors = [") :]
    sampling = sampling[: sampling.index("]")]
    assert "as_array(" in sampling, "sampled embeddings must be converted before rank() sees them"


def test_the_benchmark_calls_rank_rather_than_reimplementing_it():
    """A copy of the fusion SQL would drift from the endpoint exactly
    where the measurement is supposed to be about the endpoint."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline/bench_describe.py").read_text()
    assert "describe_module.rank(" in source
    assert "RRF_K" not in source, "fusion belongs to pipeline.describe, not to its benchmark"
