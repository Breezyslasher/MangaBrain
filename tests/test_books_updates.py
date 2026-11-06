"""The worker keeps books current the way it keeps AniList current.

Open Library has no changed-since API, so the books workflow imports and
embeds each monthly dump on GitHub, and the worker loads a release newer
than the one it holds. These pin when that check queues an update and,
as much, when it must not.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from pipeline import book_dataset, dataset_install, sync_anilist

NEW = "https://github.com/x/releases/download/books-2026-12/mangabrain-books.manifest.json"
OLD = "https://github.com/x/releases/download/books-2026-11/mangabrain-books.manifest.json"


class FakeConn:
    def __init__(self, has_books: bool):
        self.has_books = has_books

    def execute(self, sql, params=None):
        conn = self

        class Result:
            def fetchone(self):
                return (1,) if conn.has_books else None

        return Result()


@pytest.fixture
def world(monkeypatch):
    state: dict = {"sync": {}, "install": None, "requested": 0, "release": NEW}

    @contextmanager
    def connect():
        yield FakeConn(state.get("has_books", True))

    def request_install(conn, key, fresh=False):
        state["requested"] += 1
        state["install"] = {"state": "queued"}
        return state["install"]

    def write_install(conn, key, value):
        state["install"] = value
        return value

    monkeypatch.setattr(dataset_install, "_connect", connect)
    monkeypatch.setattr(dataset_install, "read_install", lambda conn, key: state["install"])
    monkeypatch.setattr(dataset_install, "request_install", request_install)
    monkeypatch.setattr(dataset_install, "write_install", write_install)
    monkeypatch.setattr(sync_anilist, "get_state", lambda conn, key: state["sync"].get(key))
    monkeypatch.setattr(
        sync_anilist, "set_state", lambda conn, key, value: state["sync"].__setitem__(key, value)
    )
    monkeypatch.setattr(book_dataset, "find_release_manifest", lambda: state["release"])
    return state


def test_a_newer_release_is_queued(world):
    world["sync"][book_dataset.LOADED_RELEASE_KEY] = {"manifest": OLD}
    assert dataset_install.check_for_updates() is True
    assert world["requested"] == 1
    assert world["install"]["state"] == "queued"


def test_the_release_already_loaded_is_not_reloaded(world):
    world["sync"][book_dataset.LOADED_RELEASE_KEY] = {"manifest": NEW}
    assert dataset_install.check_for_updates() is False
    assert world["requested"] == 0


def test_a_local_import_takes_the_first_release_for_its_embeddings(world):
    assert dataset_install.check_for_updates() is True


def test_no_books_means_no_download(world):
    world["has_books"] = False
    assert dataset_install.check_for_updates() is False
    assert world["requested"] == 0


def test_checked_at_most_daily(world):
    world["sync"][book_dataset.LOADED_RELEASE_KEY] = {"manifest": NEW}
    assert dataset_install.check_for_updates() is False
    world["sync"][book_dataset.LOADED_RELEASE_KEY] = {"manifest": OLD}
    # A release published an hour later waits for the next day's check.
    assert dataset_install.check_for_updates() is False
    stale = datetime.now(UTC) - timedelta(hours=21)
    world["sync"][dataset_install.UPDATE_CHECK_KEY] = {"checked_at": stale.isoformat()}
    assert dataset_install.check_for_updates() is True


def test_an_install_under_way_is_left_alone(world):
    world["install"] = {"state": "running"}
    assert dataset_install.check_for_updates() is False


def test_no_release_yet_is_quiet(world):
    world["release"] = None
    assert dataset_install.check_for_updates() is False


def test_a_failure_never_reaches_the_nightly_pass(world, monkeypatch):
    def boom():
        raise RuntimeError("GitHub down")

    monkeypatch.setattr(book_dataset, "find_release_manifest", boom)
    assert dataset_install.check_for_updates() is False


def test_the_nightly_pass_checks_before_it_installs():
    from pathlib import Path

    nightly = (Path(__file__).resolve().parent.parent / "pipeline/nightly.py").read_text()
    assert nightly.index("check_for_updates()") < nightly.index(
        "dataset_install.run_queued()\n        try"
    )


def test_a_load_records_which_release_it_holds():
    from pathlib import Path

    code = (Path(__file__).resolve().parent.parent / "pipeline/book_dataset.py").read_text()
    block = code[code.index("def load(") :]
    assert "LOADED_RELEASE_KEY" in block and "set_state(" in block
