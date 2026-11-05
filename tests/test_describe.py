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
    with_text = format_row(1, {**base, "description_clean": "A long way\nfrom home."}, why=True)
    assert "A long way from home." in with_text, "newlines should collapse to one line"

    without = format_row(1, {**base, "description_clean": ""}, why=True)
    assert "no synopsis" in without

    assert "\n" not in format_row(1, {**base, "description_clean": "x"}, why=False)


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
