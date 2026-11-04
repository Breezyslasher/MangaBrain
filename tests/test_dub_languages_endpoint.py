"""The dub language picker is built from the catalog, not from a list.

The UI offered nine hardcoded options while the database held 29 dub
languages, so no, th, he, id, zh, hi, fi, tr, ca and vi were present and
unselectable. The list had already drifted once: it was written when ANN was
the only source of non-English dubs and AniList had been asked about English
alone. Deriving the set from the data is what stops it drifting again as
coverage grows.

Only the display NAMES stay in the client, where an unknown code falls back
to the code itself - so a language that appears in the data is always
selectable even before anyone names it.
"""

from pathlib import Path

WEB = Path(__file__).parent.parent / "web"


def fake_rows(rows):
    """FakePool factory returning (lang, titles) pairs as dict rows."""
    return lambda sql, params: [{"lang": lang, "titles": n} for lang, n in rows]


def client_for(monkeypatch, rows):
    from fake_db import FakePool
    from fastapi.testclient import TestClient

    import api.main as main
    import api.routers.dub as dub_router
    from api.config import settings as cfg

    monkeypatch.setattr(cfg, "auth_token", "")
    monkeypatch.setattr(cfg, "rate_limit_per_minute", 0)
    main._rate_windows.clear()
    monkeypatch.setattr(dub_router, "get_pool", lambda: FakePool(fake_rows(rows)))
    return TestClient(main.app, raise_server_exceptions=False)


def test_languages_come_back_with_counts(monkeypatch):
    client = client_for(monkeypatch, [("en", 4479), ("es", 2187), ("de", 1968)])
    body = client.get("/dub/languages").json()
    assert body == {
        "languages": [
            {"code": "en", "titles": 4479},
            {"code": "es", "titles": 2187},
            {"code": "de", "titles": 1968},
        ]
    }


def test_an_empty_catalog_returns_an_empty_list_not_an_error(monkeypatch):
    """The client keeps its inline English fallback in that case."""
    client = client_for(monkeypatch, [])
    assert client.get("/dub/languages").json() == {"languages": []}


def test_the_floor_is_a_parameter_with_a_default():
    import inspect

    from api.routers.dub import dub_languages

    sig = inspect.signature(dub_languages)
    default = sig.parameters["min_titles"].default
    # A FastAPI Query default carries the value on .default.
    assert getattr(default, "default", default) == 10


def test_the_query_unions_both_sources():
    """A language either source knows about is offerable, matching the filter."""
    from api.routers.dub import LANGUAGES_SQL

    assert "unnest(dub_languages)" in LANGUAGES_SQL
    assert "unnest(measured_dub_languages)" in LANGUAGES_SQL
    assert "UNION ALL" in LANGUAGES_SQL


def test_the_query_counts_distinct_titles():
    """Both sources can name the same title; it must not be counted twice."""
    from api.routers.dub import LANGUAGES_SQL

    assert "count(DISTINCT id)" in LANGUAGES_SQL


def test_the_query_applies_the_floor_and_is_anime_only():
    from api.routers.dub import LANGUAGES_SQL

    assert "HAVING count(DISTINCT id) >= %(min_titles)s" in LANGUAGES_SQL
    assert LANGUAGES_SQL.count("media_type = 'ANIME'") == 2


def test_the_markup_no_longer_hardcodes_the_language_set():
    """Nine options is what drifted; English alone is the pre-fetch fallback."""
    html = (WEB / "index.html").read_text()
    start = html.index('<select id="dubLang">')
    end = html.index("</select>", start)
    block = html[start:end]
    assert block.count("<option") == 2, block
    assert 'value="en"' in block
    for drifted in ('value="de"', 'value="es"', 'value="tl"', 'value="zh-cn"'):
        assert drifted not in block


def test_the_client_fetches_the_set_and_names_locally():
    app = (WEB / "app.js").read_text()
    assert "/dub/languages" in app
    assert "loadDubLanguages" in app
    # Names are local, with a fallback so an unnamed code is still selectable.
    assert "LANGUAGE_NAMES" in app
    assert "code.toUpperCase()" in app


def test_the_client_names_the_languages_the_catalog_actually_has():
    """Every code measured in the live catalog should have a display name."""
    app = (WEB / "app.js").read_text()
    measured = (
        "ja en es de it pt fr ko tl pl ar hu no sv he id zh nl th ru da hi"
        " zh-hk fi tr zh-cn ca vi zh-tw"
    ).split()
    missing = [code for code in measured if f'"{code}"' not in app and f"{code}:" not in app]
    assert not missing, f"no display name for: {missing}"
