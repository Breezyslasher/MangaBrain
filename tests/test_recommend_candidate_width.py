"""The candidate pool is ranked narrow and widened once.

/recommend pools 500 candidates by ANN distance and serves 60 of them. It
used to select MEDIA_COLS for the pool, so every request read, transferred
and deserialized 500 synopses to use at most 60 - the same waste
pipeline/describe.py avoids, and the reason its build_rows_sql docstring
named the recommender.

Two halves have to hold together: the pool query must stay narrow, and the
full rows must actually be fetched afterwards. Pinning only the first would
pass while the response served titles with no cover and no description;
pinning it by source inspection alone would pass while the second query was
never issued. So this drives the router.
"""

import numpy as np
import pytest

DIM = 8
SEED_ID = 1
CANDIDATE_IDS = [10, 11, 12]


def media_row(media_id: int, **overrides):
    """A row with every column media_from_row reads."""
    row = {
        "id": media_id,
        "id_mal": 1000 + media_id,
        "medium": "anime",
        "title_romaji": f"Title {media_id}",
        "title_english": None,
        "title_native": None,
        "description": f"A synopsis for {media_id}.",
        "genres": ["Action"],
        "tags": [{"name": "Mecha", "rank": 80}],
        "format": "TV",
        "episodes": 26,
        "chapters": None,
        "volumes": None,
        "country_of_origin": "JP",
        "start_year": 1998,
        "status": "FINISHED",
        "average_score": 80,
        "cover_image_medium": f"https://example.invalid/{media_id}.jpg",
        "cover_image_large": f"https://example.invalid/{media_id}-l.jpg",
        "is_adult": False,
        "synonyms": [],
        "popularity": 1,
        "favourites": 1,
        "english_va_count": 1,
        "dub_groups": [],
        "english_dub_inferred": False,
        "dub_languages": ["en"],
        "measured_dub_languages": [],
        "mal_dub_languages": [],
    }
    row.update(overrides)
    return row


def responder(sql, params):
    """Answer each query the recommender issues, by shape."""
    text = " ".join(sql.split())
    if "FROM media m" in text and "LEFT JOIN embeddings" in text:
        # The seed fetch, which carries the embedding and its version.
        return [
            media_row(SEED_ID)
            | {"embedding": np.ones(DIM, dtype=np.float32), "embed_model": "test-model"}
        ]
    if "r.relation_type" in text:
        return []
    if "SELECT m.id, m.genres, m.tags" in text:
        return [
            {"id": i, "genres": ["Action"], "tags": [{"name": "Mecha", "rank": 80}], "semantic": s}
            for i, s in zip(CANDIDATE_IDS, (0.9, 0.8, 0.7), strict=True)
        ]
    if "m.id = ANY(%(ids)s)" in text:
        return [media_row(i) for i in params["ids"]]
    return []


@pytest.fixture
def client(monkeypatch):
    from fake_db import FakePool
    from fastapi.testclient import TestClient

    import api.main as main
    import api.routers.recommend as rec
    from api.config import settings as cfg

    monkeypatch.setattr(cfg, "auth_token", "")
    monkeypatch.setattr(cfg, "rate_limit_per_minute", 0)
    main._rate_windows.clear()
    pool = FakePool(responder)
    monkeypatch.setattr(rec, "get_pool", lambda: pool)
    monkeypatch.setattr(rec, "hnsw_supports_iterative_scan", lambda conn: False)
    return TestClient(main.app, raise_server_exceptions=False), pool


def test_the_pool_query_does_not_read_the_candidates_synopses(client):
    """500 rows of description to serve 60 is the waste being removed."""
    http, pool = client
    assert http.get(f"/recommend/{SEED_ID}").status_code == 200
    pool_queries = [
        sql
        for sql, _ in pool.conn.queries
        if "SELECT m.id, m.genres, m.tags" in " ".join(sql.split())
    ]
    assert len(pool_queries) == 1, "expected exactly one candidate pool query"
    assert "description_clean" not in pool_queries[0]
    assert "cover_image" not in pool_queries[0]
    assert "synonyms" not in pool_queries[0]


def test_the_served_rows_are_still_complete(client):
    """The narrowing is only safe because the survivors are widened: a
    response of ids with no cover or description would be worse than the
    read it saves."""
    http, _ = client
    body = http.get(f"/recommend/{SEED_ID}").json()
    assert body["results"], "expected recommendations"
    for item in body["results"]:
        media = item["media"]
        assert media["description"], media
        assert media["cover_image"], media
        assert media["title"], media


def test_only_the_served_page_is_widened(client):
    """The second fetch asks for the page, not for the pool."""
    http, pool = client
    http.get(f"/recommend/{SEED_ID}?limit=2")
    widened = [
        params
        for sql, params in pool.conn.queries
        if "m.id = ANY(%(ids)s)" in " ".join(sql.split()) and "embeddings" not in sql
    ]
    assert len(widened) == 1, widened
    assert widened[0]["ids"] == CANDIDATE_IDS[:2]


def test_the_ranking_order_survives_the_second_fetch(client):
    """Rows come back in index order, not ranked order, so the caller has
    to re-apply its own ranking rather than trust the query."""
    http, _ = client
    body = http.get(f"/recommend/{SEED_ID}").json()
    served = [item["media"]["id"] for item in body["results"]]
    assert served == CANDIDATE_IDS
    similarities = [item["similarity"] for item in body["results"]]
    assert similarities == sorted(similarities, reverse=True)
