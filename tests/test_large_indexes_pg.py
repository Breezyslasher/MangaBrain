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
        with pytest.raises(psycopg.errors.UndefinedColumn):
            api.db.ensure_schema()
        with psycopg.connect(url) as conn:
            for index, _ in broken[:2]:
                assert conn.execute("SELECT to_regclass(%s)", (index,)).fetchone()[0], index
        assert "[schema] building idx_media_media_type" in capsys.readouterr().out

        monkeypatch.setattr(api.db, "LARGE_INDEXES", broken[:2])
        api.db.ensure_schema()
        assert "building" not in capsys.readouterr().out, "nothing is rebuilt"
    finally:
        api.db.close_pool()
        with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
