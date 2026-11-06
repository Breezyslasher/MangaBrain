"""Installing a dataset: the decisions, with every download stubbed.

The load itself is tested against a real database in
tests/test_book_dataset_pg.py; these cover who asks, when it runs, and
what each kind of failure turns into.
"""

import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import pipeline.dataset_install as di
import pipeline.nightly as nightly
from api.config import settings
from api.datasets import BY_KEY, DATASETS, dataset_for_medium
from api.media_groups import MEDIUM_GROUPS

# ---------------------------------------------------------------------
# The registry


def test_every_medium_comes_from_exactly_one_dataset():
    """A new tab cannot arrive without a way to fill it."""
    for group in MEDIUM_GROUPS:
        owners = [d.key for d in DATASETS if group in d.media]
        assert len(owners) == 1, (group, owners)
    assert {g for d in DATASETS for g in d.media} == set(MEDIUM_GROUPS)


def test_every_installable_dataset_has_an_installer_and_a_cost():
    for dataset in DATASETS:
        if dataset.installable:
            assert dataset.key in di.INSTALLERS, dataset.key
            assert dataset.download_hint, f"{dataset.key} must say what it costs"


def test_books_come_from_the_books_dataset():
    assert dataset_for_medium("book") is BY_KEY["books"]
    assert BY_KEY["books"].source == "openlibrary"


# ---------------------------------------------------------------------
# Startup


@pytest.fixture
def startup(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["nightly", "--once"])
    monkeypatch.setattr("api.db.ensure_schema", lambda: calls.append("schema"))
    monkeypatch.setattr(
        "pipeline.restore.seed_if_empty", lambda url: calls.append("anilist-seed") or True
    )
    monkeypatch.setattr(di, "recover_interrupted", lambda: calls.append("recover"))
    monkeypatch.setattr(
        di, "request", lambda key, fresh=False: calls.append(f"request:{key}:{fresh}")
    )
    monkeypatch.setattr(di, "run_queued", lambda: calls.append("run-queued") or 0)
    monkeypatch.setattr(nightly, "run_once", lambda client: calls.append("pass"))
    monkeypatch.setattr(nightly, "RateLimitedClient", lambda **k: object())
    monkeypatch.setattr(settings, "seed_snapshot_url", "https://example.invalid/d.dump")
    monkeypatch.setattr(settings, "seed_books", True)

    def run(empty: bool):
        monkeypatch.setattr("pipeline.restore.catalog_is_empty", lambda: empty)
        nightly.main()
        return calls

    return run


def test_a_fresh_install_requests_books_after_the_anilist_seed(startup):
    calls = startup(empty=True)
    assert "request:books:True" in calls
    # After, because that restore runs pg_restore --clean over sync_state,
    # where the request is kept.
    assert calls.index("anilist-seed") < calls.index("request:books:True")
    assert calls.index("run-queued") < calls.index("pass"), "tried before the pass"


def test_an_existing_install_is_never_sent_a_download(startup):
    """An upgrade must not start a 3.5 GB download nobody asked for."""
    assert not any(c.startswith("request:") for c in startup(empty=False))


def test_seed_books_false_skips_the_fresh_request(startup, monkeypatch):
    monkeypatch.setattr(settings, "seed_books", False)
    assert not any(c.startswith("request:") for c in startup(empty=True))


def test_an_interrupted_install_is_picked_back_up_at_startup(startup):
    calls = startup(empty=False)
    assert calls.index("recover") < calls.index("run-queued")


def test_installs_are_watched_every_minute(monkeypatch):
    """A request from the settings page starts within a minute."""
    clock = {"t": 0.0}
    monkeypatch.setattr(nightly.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    polls: list[float] = []
    nightly.watch_installs(lambda: polls.append(clock["t"]), poll=60, rounds=5)
    assert polls == [0, 60, 120, 180, 240]


def test_installs_run_beside_the_passes_not_between_them():
    """Polled only between passes, an install requested while a pass was
    embedding the books sat at Queued for days."""
    import inspect

    main = inspect.getsource(nightly.main)
    assert "start_install_watcher(dataset_install.run_queued)" in main
    loop = main[main.index("while True:") :]
    assert "time.sleep(delay)" in loop
    assert "sleep_watching_installs" not in inspect.getsource(nightly)
    watcher = nightly.start_install_watcher(lambda: 0)
    assert watcher.daemon and watcher.name == "install-watcher"


# ---------------------------------------------------------------------
# Running an install


@pytest.fixture
def store(monkeypatch):
    """sync_state as a dict, and no real connection anywhere."""
    rows: dict[str, dict] = {}

    @contextmanager
    def fake_connect():
        yield object()

    monkeypatch.setattr(di, "_connect", fake_connect)
    monkeypatch.setattr(di, "read_install", lambda conn, key: rows.get(key))

    def write(conn, key, value):
        rows[key] = dict(value)
        return rows[key]

    monkeypatch.setattr(di, "write_install", write)
    return rows


def with_installer(monkeypatch, fn):
    monkeypatch.setattr(di, "INSTALLERS", {"books": fn})


def test_a_queued_install_runs_and_reports_done(store, monkeypatch):
    seen: list[dict] = []

    def installer(request, report):
        report({"phase": "downloading", "bytes": 10, "total": 20})
        seen.append(dict(store["books"]))
        return "Installed 3 books."

    with_installer(monkeypatch, installer)
    store["books"] = {"state": "queued", "fresh": True}
    assert di.run_queued() == 1
    assert seen[0]["state"] == "running" and seen[0]["bytes"] == 10
    assert store["books"]["state"] == "done" and store["books"]["message"] == "Installed 3 books."


def test_no_release_yet_waits_an_hour_and_is_not_retried_every_poll(store, monkeypatch):
    """The first books build takes two days. Asking GitHub every minute
    meanwhile would exhaust an unauthenticated rate limit."""
    calls: list[int] = []

    def installer(request, report):
        calls.append(1)
        raise di.NotYet("No release yet.")

    with_installer(monkeypatch, installer)
    store["books"] = {"state": "queued"}
    di.run_queued()
    assert store["books"]["state"] == "waiting"
    next_try = datetime.fromisoformat(store["books"]["next_try"])
    assert next_try - datetime.now(UTC) > timedelta(minutes=55)
    di.run_queued()
    assert calls == [1], "not retried before next_try"


def test_a_waiting_install_runs_once_its_time_comes(store, monkeypatch):
    with_installer(monkeypatch, lambda request, report: "ok")
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store["books"] = {"state": "waiting", "next_try": past}
    assert di.run_queued() == 1 and store["books"]["state"] == "done"


def test_a_network_failure_retries_later_on_its_own(store, monkeypatch):
    def installer(request, report):
        raise httpx.ConnectError("connection reset")

    with_installer(monkeypatch, installer)
    store["books"] = {"state": "queued"}
    di.run_queued()
    assert store["books"]["state"] == "waiting"
    gap = datetime.fromisoformat(store["books"]["next_try"]) - datetime.now(UTC)
    assert gap > timedelta(hours=5)


def test_any_other_failure_stops_until_someone_retries(store, monkeypatch):
    """A part failing its checksum would fail the same way next time.
    Retrying on a timer would re-download 3.5 GB to reach it."""

    def installer(request, report):
        raise ValueError("part02 does not match its manifest")

    with_installer(monkeypatch, installer)
    store["books"] = {"state": "queued"}
    assert di.run_queued() == 1
    assert store["books"]["state"] == "failed" and "manifest" in store["books"]["message"]
    assert di.run_queued() == 0, "a failed install waits for a Retry"


def test_nothing_runs_when_nothing_was_asked_for(store, monkeypatch):
    def must_not_run(request, report):
        raise AssertionError("installed something nobody asked for")

    with_installer(monkeypatch, must_not_run)
    assert di.run_queued() == 0
    store["books"] = {"state": "done"}
    assert di.run_queued() == 0


def test_run_queued_never_raises(monkeypatch):
    """It runs before every nightly pass, which it must not cost."""

    def broken():
        raise OSError("database unreachable")

    monkeypatch.setattr(di, "_connect", broken)
    assert di.run_queued() == 0


def test_a_fresh_install_builds_the_index_after_and_a_live_one_does_not(monkeypatch):
    import pipeline.book_dataset as bd

    loads: list[bool] = []
    monkeypatch.setattr(bd, "find_release_manifest", lambda repo=bd.DEFAULT_REPO: "u")

    def fake_load(url, **kw):
        loads.append(kw["rebuild_indexes"])
        return {"staged": 1, "embeddings": 1}

    monkeypatch.setattr(bd, "load", fake_load)
    di.install_books({"fresh": True}, lambda p: None)
    di.install_books({"fresh": False}, lambda p: None)
    assert loads == [True, False]


def test_the_rebuilt_indexes_come_from_the_schema_file():
    import pipeline.book_dataset as bd

    ddl = bd.vector_index_ddl()
    assert len(ddl) == len(bd.VECTOR_INDEXES)
    for name, stmt in zip(sorted(bd.VECTOR_INDEXES), sorted(ddl), strict=True):
        assert name in stmt and "USING hnsw" in stmt


def test_the_compose_file_offers_the_switch():
    from pathlib import Path

    compose = (Path(__file__).resolve().parent.parent / "docker-compose.prebuilt.yml").read_text()
    assert "SEED_BOOKS: ${MANGABRAIN_SEED_BOOKS:-true}" in compose
