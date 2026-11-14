"""Profiles keep each person's accounts and named lists apart on one
server; no profile is the single-user setup exactly as it was. Skipped
unless MANGABRAIN_TEST_PG names a server."""

import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")


@pytest.fixture
def client(monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient

    import api.db
    from api import vocabulary
    from api.config import settings
    from api.main import app

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "yamtrack_url", "http://owner-yamtrack")
    monkeypatch.setattr(vocabulary, "warm", lambda: None)
    api.db.close_pool()
    with TestClient(app) as c:
        with psycopg.connect(url) as conn:
            for i in (1, 2, 3):
                conn.execute(
                    "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji)"
                    " VALUES (%s, 'anilist', %s, 'ANIME', 'anime', %s)",
                    (i, str(i), f"Show {i}"),
                )
            conn.commit()
        yield c
    api.db.close_pool()
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def as_(name):
    return {"X-Profile": name} if name else {}


def test_settings_are_per_profile(client):
    assert client.put("/settings", json={"anilist_username": "owner"}).status_code == 200
    assert client.post("/profiles", json={"name": "Sam"}).json()["profiles"] == ["default", "sam"]
    client.put("/settings", json={"anilist_username": "samuel"}, headers=as_("sam"))

    mine = client.get("/settings").json()
    sams = client.get("/settings", headers=as_("sam")).json()
    assert (mine["anilist_username"], mine["profile"]) == ("owner", "default")
    assert (sams["anilist_username"], sams["profile"]) == ("samuel", "sam")
    # The server owner's environment settings are not handed to others.
    assert mine["yamtrack_url"] == "http://owner-yamtrack"
    assert sams["yamtrack_url"] == ""


def test_named_lists_are_per_profile(client):
    client.post("/exclusions/watched", json={"anilist_ids": [1]})
    client.post("/exclusions/watched", json={"anilist_ids": [2]}, headers=as_("sam"))

    def shown(name):
        rows = client.get(
            "/search", params={"medium": "anime", "exclude_lists": "watched"}, headers=as_(name)
        ).json()["results"]
        return sorted(r["id"] for r in rows)

    assert shown("") == [2, 3], "the default profile hides its own list"
    assert shown("sam") == [1, 3], "Sam's list, not the default's"
    status = client.get("/exclusions/watched", headers=as_("sam")).json()
    assert status["name"] == "watched", "shown without the profile"


def test_removing_a_profile_removes_its_accounts_and_lists(client):
    import psycopg

    from api.config import settings

    client.post("/profiles", json={"name": "sam"})
    client.put("/settings", json={"kitsu_username": "s"}, headers=as_("sam"))
    client.post("/exclusions/watched", json={"anilist_ids": [2]}, headers=as_("sam"))
    client.post("/exclusions/watched", json={"anilist_ids": [1]})
    assert client.delete("/profiles/sam").json()["profiles"] == ["default"]
    with psycopg.connect(settings.database_url) as conn:
        keys = [r[0] for r in conn.execute("SELECT key FROM app_settings").fetchall()]
        lists = [r[0] for r in conn.execute("SELECT list_name FROM custom_exclusion_state")]
    assert not any(k.startswith("profile:sam:") for k in keys)
    assert lists == ["watched"], "the default's list is kept"


def test_bad_profile_names_are_refused(client):
    assert client.get("/settings", headers=as_("no spaces")).status_code == 422
    assert client.post("/profiles", json={"name": "default"}).status_code == 422
    assert client.delete("/profiles/default").status_code == 422
    assert client.post("/exclusions/a~b", json={"anilist_ids": [1]}).status_code == 422
