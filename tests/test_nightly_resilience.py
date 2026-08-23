"""An upstream outage must not stall local embedding work.

AniList returning 403 (as it did when the API was temporarily disabled)
used to abort the whole worker pass, which quietly froze an in-progress
re-embed migration for as long as the outage lasted - even though
embedding only reads rows already in the database.
"""

import pytest

from pipeline import nightly


class FakeCursor:
    def fetchone(self):
        return (1,)  # catalog is non-empty


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return FakeCursor()

    def commit(self):
        pass


@pytest.fixture
def worker(monkeypatch):
    calls = {"embed": 0, "prune": 0}
    monkeypatch.setattr(nightly.psycopg, "connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(nightly, "get_state", lambda conn, key: {"ts": 1_700_000_000})
    monkeypatch.setattr(nightly, "set_state", lambda conn, key, value: None)
    def record(name):
        def bump():
            calls[name] += 1

        return bump

    monkeypatch.setattr(nightly, "embed_missing", record("embed"))
    monkeypatch.setattr(nightly, "prune_stale", record("prune"))
    return calls


def test_embedding_runs_even_when_the_sync_fails(worker, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("AniList HTTP 403: temporarily disabled")

    monkeypatch.setattr(nightly, "incremental_sync", boom)

    # The failure still surfaces, so the loop keeps its shorter retry delay.
    with pytest.raises(RuntimeError, match="403"):
        nightly.run_once(client=None)

    assert worker["embed"] == 1, "embedding must run despite the sync failure"
    assert worker["prune"] == 1


def test_healthy_pass_syncs_then_embeds(worker, monkeypatch):
    monkeypatch.setattr(nightly, "incremental_sync", lambda *a, **k: (5, False))

    nightly.run_once(client=None)

    assert worker["embed"] == 1
    assert worker["prune"] == 1
