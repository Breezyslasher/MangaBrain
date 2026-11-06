"""A fresh install gets books as well as the AniList catalogue.

Fresh only, and retried until it lands. These drive the worker's startup
with every external call stubbed, so they test the decisions rather than
the downloads; the load itself is tested against a real database in
tests/test_book_dataset_pg.py.
"""

import sys

import pytest

import pipeline.book_dataset as bd
import pipeline.nightly as nightly
from api.config import settings


@pytest.fixture
def startup(monkeypatch):
    """main() with --once and nothing real underneath it."""
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["nightly", "--once"])
    monkeypatch.setattr("api.db.ensure_schema", lambda: calls.append("schema"))
    monkeypatch.setattr(
        "pipeline.restore.seed_if_empty", lambda url: calls.append("anilist-seed") or True
    )
    monkeypatch.setattr(bd, "mark_seed_pending", lambda: calls.append("mark"))
    monkeypatch.setattr(bd, "maybe_seed", lambda *a, **k: calls.append("maybe-seed") or False)
    monkeypatch.setattr(nightly, "run_once", lambda client: calls.append("pass"))
    monkeypatch.setattr(nightly, "RateLimitedClient", lambda **k: object())
    monkeypatch.setattr(settings, "seed_snapshot_url", "https://example.invalid/d.dump")
    monkeypatch.setattr(settings, "seed_books", True)

    def run(empty: bool):
        monkeypatch.setattr("pipeline.restore.catalog_is_empty", lambda: empty)
        nightly.main()
        return calls

    return run


def test_a_fresh_install_is_marked_for_books_after_the_anilist_seed(startup):
    calls = startup(empty=True)
    assert "mark" in calls
    # After, because that restore runs pg_restore --clean over sync_state,
    # which is where the mark is kept.
    assert calls.index("anilist-seed") < calls.index("mark")
    assert calls.index("maybe-seed") < calls.index("pass"), "tried before the pass"


def test_an_existing_install_is_never_marked(startup):
    """An upgrade must not start a 3.5 GB download nobody asked for."""
    assert "mark" not in startup(empty=False)


def test_seed_books_false_skips_it_entirely(startup, monkeypatch):
    monkeypatch.setattr(settings, "seed_books", False)
    calls = startup(empty=True)
    assert "mark" not in calls and "maybe-seed" not in calls


@pytest.fixture
def flag(monkeypatch):
    state = {"pending": True, "cleared": False, "loaded": []}
    monkeypatch.setattr(bd, "seed_pending", lambda: state["pending"])

    def clear():
        state["cleared"] = True
        state["pending"] = False

    monkeypatch.setattr(bd, "clear_seed_pending", clear)
    monkeypatch.setattr(bd, "load", lambda url, **k: state["loaded"].append((url, k)))
    return state


def test_no_release_yet_keeps_the_mark_for_the_next_pass(flag, monkeypatch):
    """The first books build takes about two days. An install made
    before it finishes should still end up with books."""
    monkeypatch.setattr(bd, "find_release_manifest", lambda repo=bd.DEFAULT_REPO: None)
    assert bd.maybe_seed() is False
    assert flag["pending"] and not flag["cleared"]


def test_a_failed_seed_keeps_the_mark_and_does_not_raise(flag, monkeypatch):
    """Best-effort: it must never cost the pass its AniList sync."""
    monkeypatch.setattr(bd, "find_release_manifest", lambda repo=bd.DEFAULT_REPO: "u")

    def broken(url, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(bd, "load", broken)
    assert bd.maybe_seed() is False
    assert flag["pending"]


def test_a_successful_seed_clears_the_mark_and_builds_indexes_after(flag, monkeypatch):
    monkeypatch.setattr(bd, "find_release_manifest", lambda repo=bd.DEFAULT_REPO: "u")
    assert bd.maybe_seed() is True
    assert flag["cleared"]
    url, kwargs = flag["loaded"][0]
    assert url == "u" and kwargs["rebuild_indexes"] is True


def test_nothing_is_fetched_when_the_install_is_not_waiting(flag, monkeypatch):
    flag["pending"] = False

    def must_not_run(repo=bd.DEFAULT_REPO):
        raise AssertionError("asked GitHub for a release nobody was waiting for")

    monkeypatch.setattr(bd, "find_release_manifest", must_not_run)
    assert bd.maybe_seed() is False


def test_the_rebuilt_indexes_come_from_the_schema_file():
    """One source of truth for the index definitions."""
    ddl = bd.vector_index_ddl()
    assert len(ddl) == len(bd.VECTOR_INDEXES)
    for name, stmt in zip(sorted(bd.VECTOR_INDEXES), sorted(ddl), strict=True):
        assert name in stmt and "USING hnsw" in stmt


def test_the_compose_file_offers_the_switch():
    from pathlib import Path

    compose = (Path(__file__).resolve().parent.parent / "docker-compose.prebuilt.yml").read_text()
    assert "SEED_BOOKS: ${MANGABRAIN_SEED_BOOKS:-true}" in compose
