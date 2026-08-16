"""Public-exposure hardening: auth token, rate limit, security headers."""

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.config import settings


@pytest.fixture
def client():
    # No context manager: lifespan (database) intentionally not started.
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    # No test database: stub the one route these tests pass through so a
    # request that clears the middleware never opens a connection pool
    # (which would block waiting for Postgres).
    monkeypatch.setattr("api.routers.app_settings.read_settings", dict)
    main._rate_windows.clear()
    yield


def test_api_open_when_no_token_configured(client):
    assert client.get("/settings").status_code == 200


def test_api_locked_when_token_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "secret")
    assert client.get("/settings").status_code == 401
    assert client.post("/kitsu/refresh").status_code == 401
    bad = client.get("/settings", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
    good = client.get("/settings", headers={"Authorization": "Bearer secret"})
    assert good.status_code == 200


def test_healthz_and_static_stay_open(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "secret")
    assert client.get("/healthz").status_code == 200
    # The SPA shell loads without a token so it can prompt for one.
    assert client.get("/").status_code == 200
    assert client.get("/manifest.json").status_code == 200


def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    codes = [client.get("/settings").status_code for _ in range(3)]
    assert codes[0] == 200 and codes[1] == 200
    assert codes[2] == 429
    # /healthz is not an API path: probes are never throttled.
    assert client.get("/healthz").status_code == 200


def test_security_headers_present(client):
    resp = client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    home = client.get("/")
    assert "default-src 'self'" in home.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in home.headers["Content-Security-Policy"]


def test_medium_deep_links_serve_the_spa(client, monkeypatch):
    # /anime, /manga, /light-novel, /one-shot match Anibrain's structure and
    # serve the app shell (auth-exempt like the rest of the static SPA).
    monkeypatch.setattr(settings, "auth_token", "secret")
    for path in ("/anime", "/manga", "/light-novel", "/one-shot"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "MangaBrain" in resp.text
        assert resp.headers["Cache-Control"] == "no-cache"
        assert "Content-Security-Policy" in resp.headers


def test_icons_and_manifest_revalidate(client):
    # Icon changes must reach browsers: without no-cache, the old app icon
    # sticks in the favicon/manifest cache until site data is cleared.
    for path in ("/icon-192.png", "/icon-512.png", "/manifest.json"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "no-cache"
