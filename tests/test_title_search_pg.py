"""Title search runs on the trigram index, not a scan, and still finds
titles across punctuation, spaces and case. Skipped unless
MANGABRAIN_TEST_PG names a server."""

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
    api.db.ensure_schema()
    with psycopg.connect(url) as c:
        titles = ["A Dog's Purpose", "Iron Man", "Spider-Man: Homecoming"]
        for i, title in enumerate(titles, 1):
            c.execute(
                "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji)"
                " VALUES (%s, 'x', %s, 'BOOK', 'book', %s)",
                (i, str(i), title),
            )
        c.execute(
            "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji)"
            " SELECT g, 'x', g::text, 'BOOK', 'book', 'Filler ' || g"
            " FROM generate_series(100, 20000) g"
        )
        c.execute("ANALYZE media")
        c.commit()
        yield c
    api.db.close_pool()
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def search(conn, q):
    from api.routers.search import word_filter

    sql, params = word_filter(q)
    rows = conn.execute(
        f"SELECT m.title_romaji FROM media m WHERE m.medium = 'book' AND ({sql})", params
    ).fetchall()
    return [r[0] for r in rows]


def test_titles_match_across_punctuation_spaces_and_case(conn):
    assert search(conn, "a dog’s purpose") == ["A Dog's Purpose"]
    assert search(conn, "IRONMAN") == ["Iron Man"]
    assert search(conn, "spiderman homecoming") == ["Spider-Man: Homecoming"]


def test_a_title_search_uses_the_index(conn):
    from api.routers.search import word_filter

    sql, params = word_filter("purpose")
    plan = conn.execute(
        f"EXPLAIN SELECT m.id FROM media m WHERE m.medium = 'book' AND ({sql})", params
    ).fetchall()
    text = "\n".join(r[0] for r in plan)
    assert "idx_media_title_search" in text, text
