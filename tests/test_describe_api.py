"""The description-search endpoint: auth, limits, and what it must not cost.

The ranking itself is tested in test_describe.py against pure functions.
These are the properties that only exist once it is an HTTP endpoint.
"""

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
    main._rate_windows.clear()
    yield


def test_describe_is_behind_the_access_token(client, monkeypatch):
    """A new endpoint is only guarded if its prefix is listed.

    API_PREFIXES is a hand-maintained tuple, so an endpoint added without
    touching it answers unauthenticated on a server that set a token -
    which is exactly what /dub had been doing.
    """
    monkeypatch.setattr(settings, "auth_token", "secret")
    assert client.get("/describe?q=a quiet story").status_code == 401
    assert client.get("/dub/languages").status_code == 401


def test_every_router_prefix_is_guarded():
    """The same omission, caught for every future router at once."""
    unguarded = sorted(
        path
        for path in main.app.openapi()["paths"]
        if not path.startswith(main.API_PREFIXES)
        and path not in {"/healthz", "/", "/openapi.json"}
    )
    assert unguarded == [], f"these endpoints bypass auth and the rate limit: {unguarded}"


def test_a_long_facet_list_is_refused_before_any_work(client):
    """Each facet is its own ANN scan, so the list length multiplies the
    cost of one request. Refused before the model is touched."""
    query = ",".join(f"thing {i}" for i in range(12))
    response = client.get("/describe", params={"q": query})
    assert response.status_code == 422
    assert "facets" in response.json()["detail"]


def test_a_query_must_be_long_enough_to_mean_something(client):
    assert client.get("/describe", params={"q": "a"}).status_code == 422


def test_importing_the_app_does_not_load_the_model():
    """The API container runs without torch resident, and must keep doing
    so for every server whose owner never uses description search. The
    model loads on the first /describe request and not before.
    """
    from api.embedding import is_loaded

    assert not is_loaded()


def test_the_web_client_asks_for_description_search_correctly():
    """The box exists, posts to /describe, and sends a spelling the
    endpoint reads.

    filterParams() emits exclude_list, which /search spells exclude_lists -
    a difference that has already silently dropped exclusions once in this
    codebase. /describe deliberately takes the filterParams spelling.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    markup = (root / "web/index.html").read_text()
    javascript = (root / "web/app.js").read_text()

    assert 'id="describe"' in markup and 'id="describeBtn"' in markup
    assert 'api("/describe"' in javascript

    from api.main import app

    accepted = {
        p["name"] for p in app.openapi()["paths"]["/describe"]["get"].get("parameters", [])
    }
    assert "exclude_list" in accepted, "must match what filterParams() emits"
    for name in ("q", "medium", "limit", "adult", "dub_lang"):
        assert name in accepted


def test_described_results_show_facet_coverage_not_a_percentage():
    """A raw query-document cosine is not the calibrated match percentage
    the recommender shows, and rendering it as one would be a lie in the
    most visible possible place."""
    from pathlib import Path

    javascript = (Path(__file__).resolve().parent.parent / "web/app.js").read_text()
    described = javascript[javascript.index("function renderDescribed(") :]
    described = described[: described.index("\n}")]
    assert "similarity: null" in described
    assert "facets_hit" in described
