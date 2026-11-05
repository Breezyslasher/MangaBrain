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
