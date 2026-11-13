"""The large catalogue indexes are built one transaction each, after the
schema file, so one that is done is kept even when a later one fails or the
process stops. Skipped unless MANGABRAIN_TEST_PG names a server."""

import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")


def test_each_large_index_is_kept_as_soon_as_it_is_built(monkeypatch, capsys):
    import psycopg

    import api.db
    from api.config import settings

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    try:
        monkeypatch.setattr(settings, "database_url", url)
        broken = [*api.db.LARGE_INDEXES[:2], ("idx_broken", "ON media (no_such_column)")]
        monkeypatch.setattr(api.db, "LARGE_INDEXES", broken)
        api.db.ensure_schema()  # a failed index is reported, never raised
        with psycopg.connect(url) as conn:
            for index, _ in broken[:2]:
                assert conn.execute("SELECT to_regclass(%s)", (index,)).fetchone()[0], index
        out = capsys.readouterr().out
        assert "[schema] building idx_media_media_type" in out
        assert "could not build idx_broken" in out

        monkeypatch.setattr(api.db, "LARGE_INDEXES", broken[:2])
        api.db.ensure_schema()
        assert "building" not in capsys.readouterr().out, "nothing is rebuilt"
    finally:
        api.db.close_pool()
        with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_a_tag_name_too_long_for_the_index_is_left_out(monkeypatch):
    """An Open Library subject over 2,712 bytes failed the first build."""
    import json

    import psycopg

    import api.db
    from api.config import settings

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    try:
        monkeypatch.setattr(settings, "database_url", url)
        monkeypatch.setattr(api.db, "LARGE_INDEXES", [])
        api.db.ensure_schema()
        tags = [{"name": "Dogs"}, {"name": "x" * 4000}]
        with psycopg.connect(url) as conn:
            conn.execute(
                "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji, tags)"
                " VALUES (1, 'x', '1', 'BOOK', 'book', 't', %s)",
                (json.dumps(tags),),
            )
            conn.execute("CREATE INDEX idx_t ON media USING gin (mb_tag_names(tags))")
            names = conn.execute("SELECT mb_tag_names(tags) FROM media").fetchone()[0]
        assert names == ["Dogs"]
    finally:
        api.db.close_pool()
        with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
