"""Only one embedding pass writes at a time. The NAS worker's nightly pass
and an embed run from a PC through a tunnel deadlocked on the embeddings
key; the second pass now steps aside. Skipped unless MANGABRAIN_TEST_PG
names a server."""

import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")


def test_a_second_pass_steps_aside_while_one_runs(monkeypatch, capsys):
    import sys
    import types

    import psycopg

    import api.db
    from api.config import settings
    from pipeline import embed

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    try:
        url = f"{BASE}/{name}"
        monkeypatch.setattr(settings, "database_url", url)
        api.db.ensure_schema()
        loaded = []
        fake = types.SimpleNamespace(SentenceTransformer=lambda n: loaded.append(n))
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
        monkeypatch.setitem(
            sys.modules, "torch", types.SimpleNamespace(set_num_threads=lambda n: None)
        )
        with psycopg.connect(url) as holder:
            holder.execute("SELECT pg_advisory_lock(%s)", (embed.EMBED_LOCK_KEY,))
            assert embed.embed_missing() == 0
        assert "already running" in capsys.readouterr().out
        assert loaded == [], "the model is not loaded by a pass that steps aside"
    finally:
        api.db.close_pool()
        with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
