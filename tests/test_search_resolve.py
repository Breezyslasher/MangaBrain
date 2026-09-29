"""Exact identity lookups a library client needs: /search/resolve and
/search/by-mal.

Identity, not ranking: a wrong id excludes an unrelated title and pulls the
taste profile toward something the user never read, so these never return a
"closest" guess. The SQL classification (which entries a title text hits) is
exercised through canned rows; the normalization SQL itself is asserted on
shape, since CI has no database.
"""

import pytest
from fastapi.testclient import TestClient

import api.main as main
import api.routers.search as search
from api.config import settings
from tests.fake_db import FakePool

# (raw title, media id, best ordinality) rows the catalog pass would return.
# Ordinality 1-3 are the main titles, 4+ come from the synonyms array.
CATALOG = {
    "Kaguya-sama - Love Is War": [{"raw": "Kaguya-sama - Love Is War", "id": 101, "best_ord": 2}],
    "かぐや様は告らせたい": [{"raw": "かぐや様は告らせたい", "id": 101, "best_ord": 3}],
    "나 혼자만 레벨업": [{"raw": "나 혼자만 레벨업", "id": 202, "best_ord": 3}],
    "Only I Level Up": [{"raw": "Only I Level Up", "id": 202, "best_ord": 5}],
    "Kaguya": [],
    "Bloom": [
        {"raw": "Bloom", "id": 301, "best_ord": 1},
        {"raw": "Bloom", "id": 302, "best_ord": 1},
    ],
}
MAL_ROWS = [{"id": 909, "id_mal": 55555}]


def responder(sql, params):
    if "id_mal = ANY" in sql:
        wanted = set(params["mal_ids"])
        return [row for row in MAL_ROWS if row["id_mal"] in wanted]
    if "WITH wanted AS" in sql:
        rows = []
        for title in params["titles"]:
            rows.extend(CATALOG.get(title, []))
        return rows
    return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(search, "get_pool", lambda: FakePool(responder))
    main._rate_windows.clear()
    return TestClient(main.app, raise_server_exceptions=False)


def resolve(client, items, adult=False):
    resp = client.post("/search/resolve", json={"items": items, "adult": adult})
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def test_punctuation_insensitive_english_title(client):
    out = resolve(client, [{"key": "a", "titles": ["Kaguya-sama - Love Is War"]}])
    assert out["a"] == {"id": 101, "match": "title"}


def test_japanese_native_title(client):
    out = resolve(client, [{"key": "b", "titles": ["かぐや様は告らせたい"]}])
    assert out["b"] == {"id": 101, "match": "title"}


def test_korean_native_title(client):
    out = resolve(client, [{"key": "c", "titles": ["나 혼자만 레벨업"]}])
    assert out["c"] == {"id": 202, "match": "title"}


def test_synonym_only_hit_is_reported_as_synonym(client):
    out = resolve(client, [{"key": "d", "titles": ["Only I Level Up"]}])
    assert out["d"] == {"id": 202, "match": "synonym"}


def test_main_title_beats_a_synonym_on_the_same_entry(client):
    out = resolve(client, [{"key": "e", "titles": ["Only I Level Up", "나 혼자만 레벨업"]}])
    assert out["e"] == {"id": 202, "match": "title"}


def test_mal_manga_id_wins(client):
    out = resolve(client, [{"key": "f", "titles": ["Bloom"], "mal_id": 55555}])
    assert out["f"] == {"id": 909, "match": "mal"}


def test_mal_anime_id_does_not_resolve(client):
    # The catalog pass is manga-only, so an anime MAL id finds nothing and
    # there is no title to fall back on.
    out = resolve(client, [{"key": "g", "titles": [], "mal_id": 12345}])
    assert out["g"] == {"id": None, "match": "none"}


def test_two_entries_with_the_same_name_are_ambiguous(client):
    out = resolve(client, [{"key": "h", "titles": ["Bloom"]}])
    assert out["h"] == {"id": None, "match": "ambiguous"}


def test_a_prefix_is_not_a_match(client):
    out = resolve(client, [{"key": "i", "titles": ["Kaguya"]}])
    assert out["i"] == {"id": None, "match": "none"}


def test_keys_are_echoed_and_every_item_gets_a_result(client):
    items = [
        {"key": "suwayomi:1", "titles": ["Kaguya-sama - Love Is War"]},
        {"key": "suwayomi:2", "titles": ["nothing here"]},
    ]
    out = resolve(client, items)
    assert set(out) == {"suwayomi:1", "suwayomi:2"}


def test_adult_flag_is_passed_through(client):
    client.post("/search/resolve", json={"items": [{"key": "k", "titles": ["Bloom"]}]})
    sqls = search.get_pool().conn.queries
    assert all(params.get("adult") is False for _, params in sqls)


def test_too_many_items_is_rejected(client):
    items = [{"key": str(i), "titles": ["x"]} for i in range(501)]
    resp = client.post("/search/resolve", json={"items": items})
    assert resp.status_code == 422


def test_resolve_requires_the_token_when_one_is_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "secret")
    body = {"items": [{"key": "a", "titles": ["Kaguya-sama - Love Is War"]}]}
    assert client.post("/search/resolve", json=body).status_code == 401
    ok = client.post("/search/resolve", json=body, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_by_mal_requires_the_token_when_one_is_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "secret")
    assert client.get("/search/by-mal/1").status_code == 401


def test_normalization_keeps_non_ascii_and_avoids_alnum_class():
    # [:alnum:] is locale-dependent; the explicit range keeps Japanese,
    # Korean and Chinese characters while dropping ASCII punctuation.
    assert "[:alnum:]" not in search.RESOLVE_SQL
    assert r"[^a-z0-9\u0080-\U0010ffff]+" in search.RESOLVE_SQL
    # Both sides are normalized, and empty results never match.
    assert search.RESOLVE_SQL.count("regexp_replace") == 2
    assert "w.norm <> ''" in search.RESOLVE_SQL


def test_resolve_is_manga_family_only():
    assert "m.media_type = 'MANGA'" in search.RESOLVE_SQL
    assert "m.media_type = 'MANGA'" in search.RESOLVE_BY_MAL_SQL


def test_main_titles_come_before_synonyms_in_the_unnest():
    assert "ARRAY[m.title_romaji, m.title_english, m.title_native] || m.synonyms" in (
        search.RESOLVE_SQL
    )
    assert search.MAIN_TITLE_ORDS == 3
