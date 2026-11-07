"""Title search against a real Postgres: punctuation is ignored on both
sides. Skipped unless MANGABRAIN_TEST_PG names a server (see
tests/test_book_dataset_pg.py)."""

import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")

TITLES = {
    1: "A Dog's Purpose",
    2: "JoJo’s Bizarre Adventure",
    3: "Spider-Man: Homecoming",
    4: "Purpose Driven Life",
}


@pytest.fixture
def db(monkeypatch):
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
    with psycopg.connect(url) as conn:
        for i, title in TITLES.items():
            conn.execute(
                "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji,"
                " title_english, genres, tags) VALUES (%s, 'tmdb', %s, 'MOVIE', 'movie',"
                " %s, %s, '{}', '[]')",
                (i, str(i), title, title),
            )
        conn.commit()
    yield
    api.db.close_pool()
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def found(q: str) -> list[int]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers.search import router

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/search", params={"q": q})
    assert resp.status_code == 200, resp.text
    return [m["id"] for m in resp.json()["results"]]


@pytest.mark.parametrize(
    "q",
    ["A Dog’s Purpose", "a dog's purpose", "dogs purpose", "a dog’s p"],
)
def test_either_apostrophe_or_none_finds_the_title(db, q):
    assert found(q)[0] == 1


def test_a_curly_apostrophe_in_the_title_is_found_with_a_straight_one(db):
    assert found("jojo's") == [2]
    assert found("jojos bizarre") == [2]


def test_hyphens_and_colons_are_ignored(db):
    assert found("spiderman homecoming") == [3]
    assert found("spider man") == [3]
