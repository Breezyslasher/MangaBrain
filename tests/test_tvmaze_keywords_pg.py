"""TMDB keywords onto TVmaze shows, against a real Postgres, with TMDB
faked. Skipped unless MANGABRAIN_TEST_PG names a server (see
tests/test_book_dataset_pg.py)."""

import json
import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")

DIM = 768


class FakeTmdb:
    """/find by IMDb or TVDB id, and /tv/{id}/keywords."""

    def __init__(self):
        self.find = {("tt1", "imdb_id"): 501, ("99", "tvdb_id"): 502}
        self.keywords = {501: ["Time Travel", "time travel", "Small Town"], 502: ["Lawyer"]}
        self.calls: list[str] = []

    def get(self, path, params=None):
        from pipeline.sync_tmdb import Gone

        self.calls.append(path)
        if path.startswith("/find/"):
            match = self.find.get((path.split("/")[2], params["external_source"]))
            return {"tv_results": [{"id": match}] if match else []}
        tmdb_id = int(path.split("/")[2])
        if tmdb_id not in self.keywords:
            raise Gone(path)
        return {"id": tmdb_id, "results": [{"name": k} for k in self.keywords[tmdb_id]]}


@pytest.fixture
def db(monkeypatch):
    import psycopg

    from api.config import settings
    from api.db import ensure_schema

    name = f"mb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{BASE}/{name}"
    monkeypatch.setattr(settings, "database_url", url)
    ensure_schema()
    vec = "[" + ",".join("0.01" for _ in range(DIM)) + "]"
    with psycopg.connect(url) as conn:
        for i, ext in ((1, {"imdb": "tt1"}), (2, {"tvdb": "99"}), (3, {"imdb": "tt404"})):
            conn.execute(
                "INSERT INTO media (id, source, source_id, media_type, medium, title_romaji,"
                " genres, tags, external_ids) VALUES (%s, 'tvmaze', %s, 'TV', 'tv', 't',"
                " '{}', '[]', %s)",
                (i, str(i), json.dumps(ext)),
            )
            conn.execute(
                "INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)"
                " VALUES (%s, 'm', %s, now())",
                (i, vec),
            )
        conn.commit()
    yield url
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def rows(url):
    import psycopg

    with psycopg.connect(url) as conn:
        return {
            r[0]: (r[1], r[2], r[3] is not None, r[4])
            for r in conn.execute(
                "SELECT m.id, m.tags, m.tmdb_match_id, m.tmdb_keywords_at,"
                " (SELECT count(*) FROM embeddings e WHERE e.media_id = m.id)"
                " FROM media m ORDER BY m.id"
            ).fetchall()
        }


def test_matched_shows_get_tmdb_keywords_and_are_embedded_again(db):
    import psycopg

    from pipeline.sync_tmdb import tvmaze_keywords

    tmdb = FakeTmdb()
    with psycopg.connect(db) as conn:
        tally = tvmaze_keywords(conn, tmdb, 100)
    assert tally == {"checked": 3, "matched": 2, "changed": 2}
    got = rows(db)
    assert [t["name"] for t in got[1][0]] == ["Time Travel", "Small Town"], "deduplicated"
    assert got[1][1] == 501 and got[2][1] == 502
    assert got[1][3] == 0 and got[2][3] == 0, "tags changed, so embedded again"
    # No match: no keywords, embedding kept, but checked - not retried nightly.
    assert got[3] == ([], None, True, 1)


def test_a_show_is_not_looked_up_again_until_it_is_due(db):
    import psycopg

    from pipeline.sync_tmdb import tvmaze_keywords

    with psycopg.connect(db) as conn:
        tvmaze_keywords(conn, FakeTmdb(), 100)
        again = FakeTmdb()
        assert tvmaze_keywords(conn, again, 100)["checked"] == 0
        assert again.calls == []
        conn.execute("UPDATE media SET tmdb_keywords_at = now() - interval '200 days'")
        conn.commit()
        assert tvmaze_keywords(conn, again, 100)["checked"] == 3
        assert "/find/tt1" not in again.calls, "a known match skips the /find"


def test_unchanged_keywords_keep_the_embedding(db):
    import psycopg

    from pipeline.sync_tmdb import tvmaze_keywords

    with psycopg.connect(db) as conn:
        tvmaze_keywords(conn, FakeTmdb(), 100)
        conn.execute(
            "INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)"
            " SELECT 1, 'm', embedding, now() FROM embeddings WHERE media_id = 3"
        )
        conn.execute("UPDATE media SET tmdb_keywords_at = now() - interval '200 days'")
        conn.commit()
        assert tvmaze_keywords(conn, FakeTmdb(), 100)["changed"] == 0
    assert rows(db)[1][3] == 1


def test_a_tv_release_is_never_built_from_tmdb_keywords(db, tmp_path):
    import psycopg

    from pipeline import tv_dataset
    from pipeline.sync_tmdb import tvmaze_keywords

    with psycopg.connect(db) as conn:
        tvmaze_keywords(conn, FakeTmdb(), 100)
    with pytest.raises(RuntimeError, match="TMDB forbids redistribution"):
        tv_dataset.export(tmp_path)
