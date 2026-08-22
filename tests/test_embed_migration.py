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
