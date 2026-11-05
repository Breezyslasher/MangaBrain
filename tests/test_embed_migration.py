"""Invariants of the versioned-embedding migration path.

A model switch must never break serving: new-version rows are inserted
alongside the old version's (composite primary key), the ANN index is a
per-dimension partial expression index over a dimension-untyped column, and
queries repeat the cast + vector_dims predicate so the planner can use it.
These tests pin the SQL shapes that make that work.
"""

from pathlib import Path

from pipeline.embed import PRUNE_STALE_SQL, UPSERT_SQL

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def test_upsert_conflicts_per_version_not_per_media():
    # ON CONFLICT (media_id) would overwrite the serving version's row
    # mid-migration; the conflict target must include embed_model.
    assert "ON CONFLICT (media_id, embed_model)" in UPSERT_SQL
    # And the update must not rewrite embed_model (the key carries it).
    assert "embed_model = EXCLUDED" not in UPSERT_SQL


def test_prune_keeps_only_the_given_version():
    assert "embed_model <> %s" in PRUNE_STALE_SQL


def test_schema_embedding_column_is_dimension_untyped():
    assert "embedding   vector NOT NULL" in SCHEMA
    assert "vector(384) NOT NULL" not in SCHEMA


def test_schema_has_composite_primary_key():
    assert "PRIMARY KEY (media_id, embed_model)" in SCHEMA


def test_schema_has_partial_index_per_dimension():
    for dim in (384, 768):
        assert f"(embedding::vector({dim})) vector_cosine_ops" in SCHEMA
        assert f"WHERE vector_dims(embedding) = {dim}" in SCHEMA


def test_candidate_query_matches_index_expression():
    # The recommender inlines the dimension into a cast and a vector_dims
    # predicate; both must be present or the partial HNSW index goes unused
    # and every request degrades to a sequential scan.
    import inspect

    from api.routers import recommend

    source = inspect.getsource(recommend._recommend_for_seeds)
    assert "vector_dims(e.embedding) = {dim}" in source
    assert "e.embedding::vector({dim}) <=> %(seed_vec)s" in source


def test_torch_thread_count_leaves_a_core_free():
    from pipeline.embed import torch_thread_count

    assert torch_thread_count(0, 4) == 3
    assert torch_thread_count(0, 1) == 1
    assert torch_thread_count(0, None) == 1
    assert torch_thread_count(2, 8) == 2


def test_progress_line_reports_fraction_percent_and_eta():
    from pipeline.embed import progress_line

    line = progress_line(50, 200, elapsed=10.0)
    assert "50/200" in line
    assert "(25.0%)" in line
    assert "5.0/s" in line
    # 150 remaining at 5/s = 30s = 0m (rounded), so just assert the label.
    assert "ETA" in line


def test_progress_line_survives_zero_elapsed_and_zero_pending():
    from pipeline.embed import progress_line

    assert progress_line(0, 0, elapsed=0.0) == "[embed] 0/0"
    assert "0/100" in progress_line(0, 100, elapsed=0.0)


def test_progress_line_counts_the_whole_catalog_across_resumed_runs():
    """A re-embed split across CI runs must not restart its percentage.

    Each run only knows the rows still missing, so anchoring the fraction on
    that made every resumed run report 0% and climb again (run 14 of
    dataset-publish reached 58.8%, then its resume started over).
    """
    from pipeline.embed import progress_line

    # Resumed run: 99k of a 160k catalog already current, 60,536 left,
    # 35,584 done this pass.
    line = progress_line(35584, 60536, elapsed=4200.0, already=99464, catalog_total=160000)
    assert "135048/160000" in line
    assert "(84.4%)" in line
    # ETA still covers all remaining work, not just this run's slice.
    assert "ETA 49m" in line


def test_progress_line_defaults_to_the_pass_when_no_catalog_total():
    from pipeline.embed import progress_line

    assert progress_line(50, 200, elapsed=10.0).startswith("[embed] 50/200 (25.0%)")


def test_the_quantized_copy_is_written_by_the_same_statement_as_the_vector():
    """A derived column with a second writer drifts.

    This project has paid for that twice already: mal_dub_languages was
    read by the filter and not by the language picker, and
    measured_dub_languages was selected and never served. embedding_bits
    is written in the embedding's own upsert so there is no window where
    a row has one and not the other.
    """
    from pipeline.embed import UPSERT_SQL

    assert "embedding_bits" in UPSERT_SQL
    assert "binary_quantize(%s)" in UPSERT_SQL
    assert "embedding_bits = EXCLUDED.embedding_bits" in UPSERT_SQL


def test_the_quantized_column_is_not_generated():
    """GENERATED ALWAYS AS ... STORED rewrites the table under an
    exclusive lock, and ensure_schema() runs at API startup - a boot
    would stall past its healthcheck rewriting 165,000 rows and their
    TOAST. A plain ADD COLUMN is instant."""
    assert "ADD COLUMN IF NOT EXISTS embedding_bits VARBIT" in SCHEMA
    assert "GENERATED ALWAYS AS (binary_quantize" not in SCHEMA


def test_the_quantized_column_is_dimension_untyped_like_the_vector():
    """Two embedding versions coexist during a re-embed, so a bit(768)
    column could not hold both - the same reason the vector column has
    no declared dimension."""
    assert "embedding_bits VARBIT" in SCHEMA
    assert "embedding_bits bit(768)" not in SCHEMA.lower().replace("BIT(768)", "bit(768)")


def test_no_ann_index_on_the_quantized_column():
    """The column exists to make an EXACT scan affordable. An index on it
    would reintroduce the approximation it is there to remove."""
    assert "hnsw ((embedding_bits" not in SCHEMA
    assert "bit_hamming_ops" not in SCHEMA
