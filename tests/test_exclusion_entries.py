"""Exclusion lists carry tracker status and rating.

custom_exclusion_entries has always stored planned and score, and every
consumer reads them (keep_planned, For-you sampling, the taste profile), but
a posted list could only ever store bare ids. These tests pin the entries
body, and the merge rule every list source shares.
"""

import pytest
from fastapi.testclient import TestClient

import api.main as main
import api.routers.exclusions as exclusions
from api.config import settings
from api.models import ExclusionListIn
from api.routers.exclusions import merge_entry


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    main._rate_windows.clear()
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def stored(monkeypatch):
    """Capture what the handler would write, without a database."""
    captured: dict = {}

    def fake_store(name, entries):
        captured["name"] = name
        captured["entries"] = entries

    def fake_status(name):
        from datetime import datetime

        from api.models import ExclusionStatus

        return ExclusionStatus(
            name=name,
            entry_count=len(captured.get("entries", {})),
            updated_at=datetime(2026, 1, 1),
        )

    monkeypatch.setattr(exclusions, "store_exclusion_list", fake_store)
    monkeypatch.setattr(exclusions, "get_status", fake_status)
    return captured


def test_entries_keep_status_and_rating(client, stored):
    body = {
        "entries": [
            {"kind": "anilist", "id": 101, "planned": True, "score": 90},
            {"kind": "mal_manga", "id": 7, "planned": False},
        ]
    }
    assert client.post("/exclusions/suwayomi", json=body).status_code == 200
    assert stored["entries"] == {
        ("anilist", 101): (True, 90),
        ("mal_manga", 7): (False, None),
    }


def test_old_bodies_still_store_bare_ids(client, stored):
    body = {"anilist_ids": [1, 2], "mal_manga_ids": [3]}
    assert client.post("/exclusions/suwayomi", json=body).status_code == 200
    # A bare id is "on the list, status unknown": always excluded, unrated.
    assert stored["entries"] == {
        ("anilist", 1): (False, None),
        ("anilist", 2): (False, None),
        ("mal_manga", 3): (False, None),
    }


def test_bare_id_stays_excluded_but_takes_the_entry_rating(client, stored):
    body = {
        "anilist_ids": [101],
        "entries": [{"kind": "anilist", "id": 101, "planned": True, "score": 80}],
    }
    assert client.post("/exclusions/suwayomi", json=body).status_code == 200
    # started (the bare id) wins over planned, the rating still counts
    assert stored["entries"] == {("anilist", 101): (False, 80)}


def test_duplicate_entries_merge_started_wins_and_higher_score(client, stored):
    body = {
        "entries": [
            {"kind": "anilist", "id": 5, "planned": True, "score": 60},
            {"kind": "anilist", "id": 5, "planned": False, "score": 75},
        ]
    }
    assert client.post("/exclusions/suwayomi", json=body).status_code == 200
    assert stored["entries"] == {("anilist", 5): (False, 75)}


def test_merge_rule_matches_the_tracker_integrations():
    entries: dict[tuple[str, int], tuple[bool, int | None]] = {}
    key = ("anilist", 1)
    merge_entry(entries, key, True, None)
    assert entries[key] == (True, None)
    merge_entry(entries, key, True, 40)
    assert entries[key] == (True, 40)
    merge_entry(entries, key, False, None)  # started wins
    assert entries[key] == (False, 40)
    merge_entry(entries, key, True, 90)  # higher score wins
    assert entries[key] == (False, 90)


@pytest.mark.parametrize(
    "entry",
    [
        {"kind": "anilist", "id": 1, "score": 101},
        {"kind": "anilist", "id": 1, "score": -1},
        {"kind": "myanimelist", "id": 1},
        {"kind": "anilist"},
    ],
)
def test_invalid_entries_are_rejected(client, stored, entry):
    resp = client.post("/exclusions/suwayomi", json={"entries": [entry]})
    assert resp.status_code == 422


def test_entries_default_to_started_and_unrated():
    body = ExclusionListIn(entries=[{"kind": "anilist", "id": 1}])
    assert body.entries[0].planned is False
    assert body.entries[0].score is None
