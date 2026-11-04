"""Asset URLs carry a content hash, so a deploy cannot serve a stale app.js.

The dub language picker showed its two-option fallback on a deployment
whose API returned twenty-five languages, because the browser was running
an app.js from before loadDubLanguages existed. The no-cache header could
not help: a response cached BEFORE that header was introduced is still
subject to heuristic freshness, so the browser never revalidated.

Versioning the URL sidesteps revalidation entirely - a changed file is a
different URL, which no cache can satisfy from an old entry. The comment in
api/main.py notes this app had already been bitten once before; this is the
second time, which is what moved it from a header to a URL.
"""

import hashlib
from pathlib import Path

import pytest

WEB = Path(__file__).parent.parent / "web"


def test_the_markup_references_the_assets_plainly():
    """The hash is injected when serving; the file on disk stays clean."""
    html = (WEB / "index.html").read_text()
    assert '"app.js"' in html
    assert '"style.css"' in html


def test_serving_injects_a_content_hash():
    from api.main import _versioned_index

    html = _versioned_index(WEB, WEB / "index.html")
    assert 'src="app.js?v=' in html
    assert 'href="style.css?v=' in html


def test_the_hash_is_the_content_hash():
    from api.main import _asset_version, _versioned_index

    want = hashlib.sha256((WEB / "app.js").read_bytes()).hexdigest()[:10]
    assert _asset_version(WEB / "app.js") == want
    assert f"app.js?v={want}" in _versioned_index(WEB, WEB / "index.html")


def test_a_changed_asset_gets_a_different_url(tmp_path):
    """The property that makes this work at all."""
    from api.main import _versioned_index

    (tmp_path / "index.html").write_text('<script src="app.js"></script>')
    (tmp_path / "app.js").write_text("console.log(1)")
    first = _versioned_index(tmp_path, tmp_path / "index.html")
    (tmp_path / "app.js").write_text("console.log(2)")
    second = _versioned_index(tmp_path, tmp_path / "index.html")
    assert first != second


def test_an_unchanged_asset_keeps_its_url(tmp_path):
    """Otherwise every restart would invalidate every client's cache."""
    from api.main import _versioned_index

    (tmp_path / "index.html").write_text('<script src="app.js"></script>')
    (tmp_path / "app.js").write_text("console.log(1)")
    assert _versioned_index(tmp_path, tmp_path / "index.html") == _versioned_index(
        tmp_path, tmp_path / "index.html"
    )


def test_a_missing_asset_is_left_alone_rather_than_breaking_the_page(tmp_path):
    from api.main import _versioned_index

    (tmp_path / "index.html").write_text('<script src="app.js"></script>')
    html = _versioned_index(tmp_path, tmp_path / "index.html")
    assert html == '<script src="app.js"></script>'


def test_the_root_path_is_served_through_the_versioned_shell():
    """Without its own route the static mount would serve the raw file, and
    "/" is the page most likely to be opened."""
    import inspect

    import api.main as main

    source = inspect.getsource(main)
    assert 'app.add_api_route("/", _spa_shell' in source


@pytest.mark.parametrize("path", ["/anime", "/manga", "/light-novel", "/one-shot"])
def test_every_medium_deep_link_is_served_the_same_way(path):
    from api.main import MEDIUM_PATHS

    assert path in MEDIUM_PATHS
