"""Genre and tag vocabularies per medium, against a real Postgres. Skipped
unless MANGABRAIN_TEST_PG names a server (see tests/test_book_dataset_pg.py)."""

import json
import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")

ROWS = [
    # id, medium, genres, tags, is_adult
    (1, "anime", ["Action", "Mahou Shoujo"], ["Time Travel"], False),
    (2, "anime", ["Hentai"], ["Nudity"], True),
    (3, "tv", ["Legal", "Drama"], [], False),
    (4, "book", ["Biography"], ["TI-83 (Calculator)", "Dogs"], False),
    (5, "book", ["Fiction"], ["Dogs"], False),
]


@pytest.fixture
def db(monkeypatch):
    import psycopg

    import api.db
    from api import vocabulary
    from api.config import settings

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    monkeypatch.setattr(settings, "database_url", url)
    api.db.close_pool()
    api.db.ensure_schema()
    vocabulary.forget()
    with psycopg.connect(url) as conn:
        for i, medium, genres, tags, adult in ROWS:
            conn.execute(
                "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji,"
                " genres, tags, is_adult) VALUES (%s, 'x', %s, 'BOOK', %s, 't', %s, %s, %s)",
                (
                    i,
                    str(i),
                    medium,
                    genres,
                    json.dumps([{"name": t, "rank": 50} for t in tags]),
                    adult,
                ),
            )
        conn.commit()
    yield vocabulary
    vocabulary.forget()
    api.db.close_pool()
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_each_medium_gets_its_own_genres(db):
    assert db.vocabulary("genres", "anime") == ["Action", "Mahou Shoujo"]
    assert db.vocabulary("genres", "tv") == ["Drama", "Legal"]
    assert db.vocabulary("genres", "book") == ["Biography", "Fiction"]


def test_each_medium_gets_its_own_tags(db):
    assert db.vocabulary("tags", "anime") == ["Time Travel"]
    assert db.vocabulary("tags", "tv") == []
    assert "Time Travel" not in db.vocabulary("tags", "book")


def test_adult_titles_add_nothing(db):
    assert "Hentai" not in db.vocabulary("genres", None)
    assert "Nudity" not in db.vocabulary("tags", None)


def test_tags_are_capped_by_how_many_titles_share_them(db, monkeypatch):
    monkeypatch.setattr(db, "MAX_TAGS", 1)
    assert db.vocabulary("tags", "book") == ["Dogs"], "the shared word, not the one-off"


def test_a_stale_list_is_served_while_it_refreshes(db, monkeypatch):
    first = db.vocabulary("genres", "tv")
    calls = []
    monkeypatch.setattr(db, "compute", lambda kind, group: calls.append(1) or ["New"])
    assert db.vocabulary("genres", "tv", now=lambda: 10**12) == first
    import time

    for _ in range(50):
        if db.vocabulary("genres", "tv") == ["New"]:
            break
        time.sleep(0.01)
    assert db.vocabulary("genres", "tv") == ["New"]
    assert calls == [1]
