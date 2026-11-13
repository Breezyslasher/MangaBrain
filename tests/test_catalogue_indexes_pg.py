"""The catalogue-wide queries are answered from indexes, not by reading
every row: a tag filter, the browse order and the dub counts. Each took
seconds on a NAS once 1.1 million books arrived. Also that a random pick
still finds a title when the page sample holds none. Skipped unless
MANGABRAIN_TEST_PG names a server."""

import json
import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")


@pytest.fixture
def conn(monkeypatch):
    import psycopg

    import api.db
    from api.config import settings

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    monkeypatch.setattr(settings, "database_url", url)
    api.db.close_pool()
    api.db.ensure_schema()
    with psycopg.connect(url) as c:
        c.execute(
            "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji,"
            " genres, tags, start_year)"
            " SELECT g, 'x', g::text, 'BOOK', 'book', 'Book ' || g,"
            " ARRAY['Genre' || (g % 20)],"
            " jsonb_build_array(jsonb_build_object('name', 'Tag' || (g % 5000))),"
            " 1950 + g % 75"
            " FROM generate_series(1, 30000) g"
        )
        c.execute(
            "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji, tags)"
            " VALUES (40000, 'x', '40000', 'ANIME', 'anime', 'Show', %s)",
            (json.dumps([{"name": "Iyashikei", "rank": 80}]),),
        )
        c.execute("ANALYZE media")
        c.commit()
        yield c
    api.db.close_pool()
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def plan(conn, sql, params=None):
    return "\n".join(r[0] for r in conn.execute("EXPLAIN " + sql, params or {}).fetchall())


def test_a_tag_filter_uses_the_tag_index(conn):
    from api.filters import build_filters

    filters, params = build_filters(tags_include=["Tag4999"])
    text = plan(conn, f"SELECT m.id FROM media m WHERE m.medium = 'book' {filters}", params)
    assert "idx_media_tag_names" in text, text
    rows = conn.execute(f"SELECT m.id FROM media m WHERE TRUE {filters}", params).fetchall()
    assert len(rows) == 6


def test_tag_names_read_the_stored_shape(conn):
    names = conn.execute("SELECT mb_tag_names(tags) FROM media WHERE id = 40000").fetchone()[0]
    assert names == ["Iyashikei"]
    assert conn.execute("SELECT mb_tag_names(NULL)").fetchone()[0] == []


def test_browse_reads_the_medium_order_index(conn):
    """Films nearly all carry a year and few books do, so one order over
    every medium walked past every film to reach the newest books. The
    order is per medium, and the browse must read it without a sort."""
    from api.routers.search import BROWSE_ORDER, medium_clause

    conn.execute(
        "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji, start_year)"
        " SELECT g, 'x', g::text, 'MOVIE', 'movie', 'Film ' || g, 2025"
        " FROM generate_series(50000, 90000) g"
    )
    conn.execute("UPDATE media SET start_year = NULL WHERE medium = 'book' AND id % 10 <> 0")
    conn.execute("ANALYZE media")
    sql = (
        f"SELECT m.id FROM media m WHERE {medium_clause(['book'])} ORDER BY {BROWSE_ORDER} LIMIT 60"
    )
    text = plan(conn, sql, {"medium_one": "book"})
    assert "idx_media_medium_browse" in text, text
    assert "Sort" not in text.split("idx_media_medium_browse")[0], text
    assert medium_clause(["manga", "manhwa", "manhua"]) == "m.medium = ANY(%(mediums)s)"


def test_dub_counts_read_the_media_type_index(conn):
    from api.routers.dub import LANGUAGES_SQL

    text = plan(conn, LANGUAGES_SQL, {"min_titles": 1})
    assert "idx_media_media_type" in text, text


def test_a_random_pick_falls_back_when_the_sample_is_empty(conn):
    from fastapi.testclient import TestClient

    from api import vocabulary
    from api.main import app

    vocabulary.warm = lambda: None
    with TestClient(app) as client:
        # One anime among 30,000 rows: a 2% page sample rarely holds it.
        for _ in range(5):
            resp = client.get("/random", params={"medium": "anime"})
            assert resp.status_code == 200
            assert resp.json()["title"] == "Show"
        assert client.get("/random", params={"medium": "book"}).status_code == 200
