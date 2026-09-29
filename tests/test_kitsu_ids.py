"""Kitsu ids on a posted exclusion list, and synonyms/by-mal lookups.

A Suwayomi client can know only a Kitsu id for a tracked title. Kitsu ids
are not a join key for the exclusion filter, so they are translated to the
MAL/AniList ids they map to before anything is stored, through the same
mapping walk the library import uses.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import api.main as main
import api.routers.exclusions as exclusions
import api.routers.kitsu as kitsu
import api.routers.search as search
from api.config import settings
from api.db import MAX_SYNONYMS, MEDIA_COLS, media_from_row
from api.models import ExclusionStatus
from tests.fake_db import FakePool


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    main._rate_windows.clear()
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def stored(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        exclusions,
        "store_exclusion_list",
        lambda name, entries: captured.update(entries=entries),
    )
    monkeypatch.setattr(
        exclusions,
        "get_status",
        lambda name: ExclusionStatus(
            name=name,
            entry_count=len(captured.get("entries", {})),
            updated_at=datetime(2026, 1, 1),
        ),
    )
    return captured


def test_kitsu_ids_are_stored_as_the_ids_they_map_to(client, stored, monkeypatch):
    monkeypatch.setattr(
        kitsu,
        "external_keys_for",
        lambda ids_by_kind: {
            ("kitsu_manga", 11): [("mal_manga", 501), ("anilist", 777)],
            ("kitsu_manga", 12): [],
        },
    )
    body = {
        "entries": [
            {"kind": "kitsu_manga", "id": 11, "planned": True, "score": 70},
            {"kind": "kitsu_manga", "id": 12},
        ]
    }
    resp = client.post("/exclusions/suwayomi", json=body)
    assert resp.status_code == 200
    # Both mappings carry the entry's own state.
    assert stored["entries"] == {
        ("mal_manga", 501): (True, 70),
        ("anilist", 777): (True, 70),
    }
    # The id Kitsu could not map is reported, not silently dropped.
    assert resp.json()["skipped"] == 1


def test_no_kitsu_entries_means_no_kitsu_call(client, stored, monkeypatch):
    def explode(ids_by_kind):
        raise AssertionError("Kitsu must not be contacted for plain ids")

    monkeypatch.setattr(kitsu, "external_keys_for", explode)
    resp = client.post("/exclusions/suwayomi", json={"anilist_ids": [1]})
    assert resp.status_code == 200
    assert resp.json()["skipped"] is None


def test_media_keys_reads_mal_and_anilist_mappings():
    media = {
        "id": "9",
        "type": "manga",
        "relationships": {"mappings": {"data": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}},
    }
    mappings = {
        "a": {"attributes": {"externalSite": "myanimelist/manga", "externalId": "501"}},
        "b": {"attributes": {"externalSite": "anilist/manga", "externalId": "777"}},
        # Non-numeric external ids are a known Kitsu data bug.
        "c": {"attributes": {"externalSite": "anilist/manga", "externalId": "abc"}},
    }
    assert kitsu.media_keys("manga", media, mappings) == [
        ("mal_manga", 501),
        ("anilist", 777),
    ]


def test_media_keys_ignores_other_sites_and_missing_media():
    mappings = {"a": {"attributes": {"externalSite": "mangaupdates", "externalId": "5"}}}
    media = {"relationships": {"mappings": {"data": [{"id": "a"}]}}}
    assert kitsu.media_keys("manga", media, mappings) == []
    assert kitsu.media_keys("manga", None, mappings) == []


def test_kitsu_id_lookup_is_chunked_to_kitsus_page_cap():
    # config/initializers/jsonapi-resources.rb: maximum_page_size = 20
    assert kitsu.ID_LOOKUP_BATCH == 20


def test_no_ids_makes_no_request():
    assert kitsu.external_keys_for({"manga": []}) == {}


def test_synonyms_are_selected_and_capped():
    assert "m.synonyms" in MEDIA_COLS
    row = {
        "id": 1,
        "id_mal": None,
        "medium": "manga",
        "title_romaji": "T",
        "title_english": None,
        "title_native": None,
        "description": None,
        "genres": [],
        "tags": [],
        "format": None,
        "episodes": None,
        "chapters": None,
        "volumes": None,
        "country_of_origin": None,
        "start_year": None,
        "status": None,
        "average_score": None,
        "cover_image_medium": None,
        "cover_image_large": None,
        "is_adult": False,
        "synonyms": [f"alt {i}" for i in range(25)],
        "popularity": None,
        "favourites": None,
    }
    out = media_from_row(row)
    assert out.synonyms == [f"alt {i}" for i in range(MAX_SYNONYMS)]
    row["synonyms"] = None
    assert media_from_row(row).synonyms == []


def test_by_mal_is_typed_and_404s_when_unknown(client, monkeypatch):
    seen: list = []

    def responder(sql, params):
        seen.append(params)
        return [] if params["mal_id"] == 404 else [{"id": 5, **_media_row()}]

    monkeypatch.setattr(search, "get_pool", lambda: FakePool(responder))
    assert client.get("/search/by-mal/404").status_code == 404

    ok = client.get("/search/by-mal/7")
    assert ok.status_code == 200
    assert ok.json()["id"] == 5
    assert seen[-1]["media_type"] == "MANGA"  # MAL ids are separate id spaces

    client.get("/search/by-mal/7?type=anime")
    assert seen[-1]["media_type"] == "ANIME"


def _media_row() -> dict:
    return {
        "id_mal": 7,
        "medium": "manga",
        "title_romaji": "T",
        "title_english": None,
        "title_native": None,
        "description": None,
        "genres": [],
        "tags": [],
        "format": None,
        "episodes": None,
        "chapters": None,
        "volumes": None,
        "country_of_origin": None,
        "start_year": None,
        "status": None,
        "average_score": None,
        "cover_image_medium": None,
        "cover_image_large": None,
        "is_adult": False,
        "synonyms": [],
        "popularity": None,
        "favourites": None,
    }
