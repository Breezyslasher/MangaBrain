"""`full --missing` re-checks only the TMDB ids the database does not hold.
Skipped unless MANGABRAIN_TEST_PG names a server."""

import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")


@pytest.fixture
def db(monkeypatch):
    import psycopg

    from api.config import settings
    from api.db import ensure_schema

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    monkeypatch.setattr(settings, "database_url", url)
    ensure_schema()
    with psycopg.connect(url) as conn:
        for i in (1, 2):
            conn.execute(
                "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji,"
                " genres, tags) VALUES (%s, 'tmdb', %s, 'MOVIE', 'movie', 't', '{}', '[]')",
                (i, f"movie/{i}"),
            )
        conn.commit()
    yield url
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_only_ids_not_held_are_fetched_even_after_a_complete_pass(db, monkeypatch):
    import psycopg

    from pipeline import sync_tmdb

    fetched: list[int] = []
    monkeypatch.setattr(sync_tmdb, "export_ids", lambda kind: ("2026-11-07", [1, 2, 3, 4]))
    monkeypatch.setattr(sync_tmdb, "fetch", lambda client, kind, i: fetched.append(i) or None)
    with psycopg.connect(db) as conn:
        sync_tmdb.set_state(conn, "tmdb_full_movie", {"complete": True})
        conn.commit()
        sync_tmdb.full(conn, None, ("movie",))
        assert fetched == [], "a complete pass is not repeated"
        sync_tmdb.full(conn, None, ("movie",), missing=True)
        assert sorted(fetched) == [3, 4]
        assert sync_tmdb.get_state(conn, "tmdb_full_movie")["complete"] is True
