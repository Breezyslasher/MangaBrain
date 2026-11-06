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


def test_the_quantized_column_is_gone():
    """Binary quantization was built, measured and removed.

    232 ms at 77-85% recall of the exact top 60, against the HNSW index
    at 5.8 ms and 96% on the path a caller actually uses. A column
    nothing reads is the drift this project keeps paying for, so it is
    dropped rather than left dormant - the reasoning stays in
    db/schema.sql so the next person does not rebuild it from the same
    premise.
    """
    assert "ADD COLUMN IF NOT EXISTS embedding_bits" not in SCHEMA
    assert "DROP COLUMN IF EXISTS embedding_bits" in SCHEMA

    from pipeline.embed import UPSERT_SQL

    assert "embedding_bits" not in UPSERT_SQL
    assert "binary_quantize" not in UPSERT_SQL


def test_the_schema_records_why_rather_than_only_that():
    """A bare DROP invites the next person to re-add it."""
    assert "1,880,352" in SCHEMA, "the measurement that motivated it"
    assert "recall" in SCHEMA, "and the measurement that killed it"


def test_the_upsert_parameters_fit_the_upsert_sql():
    """Held against psycopg's own placeholder check, not read as text.

    Every embedding pass crashed on its first batch from 5 Nov 2026: the
    parameter tuple kept a value for a reverted column while the SQL lost
    its placeholder. The text tests above all passed, because the SQL on
    its own was right. This builds the real tuple and asks psycopg - the
    thing that actually rejected it - whether the two agree.
    """
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer

    from pipeline.embed import upsert_params

    rows = [(41, "Dune", None, "desc", [], []), (42, "Solaris", None, "desc", [], [])]
    params = upsert_params(rows, "model-v1", ["[0.1]", "[0.2]"])
    assert len(params) == 2
    for tup in params:
        query = PostgresQuery(Transformer())
        query.convert(UPSERT_SQL, tup)
        query.dump(tup)  # raises ProgrammingError on a count mismatch


def test_the_embed_loop_builds_its_parameters_through_upsert_params():
    """So the check above covers what the loop actually sends, rather
    than a copy of it the loop has drifted away from."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline/embed.py").read_text()
    assert "cur.executemany(UPSERT_SQL, upsert_params(rows, stored_id, vectors))" in source
