"""The books dataset against a real Postgres with pgvector.

Binary COPY is the part of this that cannot be checked by reading code:
it is exact about types and silent about nothing, so the only honest test
is a round trip through a real server. Skipped unless
MANGABRAIN_TEST_PG names one, as a URL without a database:

    MANGABRAIN_TEST_PG=postgresql://mangabrain@127.0.0.1:55432 pytest tests/test_book_dataset_pg.py

Two databases, because the case that matters is merging into a catalogue
that already has content of its own.
"""

import json
import os
import uuid

import pytest

BASE = os.environ.get("MANGABRAIN_TEST_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="MANGABRAIN_TEST_PG not set")

DIM = 768


def vec(seed: float) -> str:
    return "[" + ",".join(f"{seed + i * 1e-4:.6f}" for i in range(DIM)) + "]"


@pytest.fixture
def two_dbs(monkeypatch):
    import psycopg

    from api.config import settings

    names = [f"mb_test_{uuid.uuid4().hex[:8]}" for _ in range(2)]
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        for name in names:
            admin.execute(f"CREATE DATABASE {name}")
    urls = [f"{BASE}/{name}" for name in names]

    def use(url):
        monkeypatch.setattr(settings, "database_url", url)

    yield urls, use
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as admin:
        for name in names:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def seed_books(url: str, books: list[dict], vectors: dict[str, str], model: str) -> None:
    import psycopg

    from pipeline.sync_openlibrary import UPSERT_SQL

    with psycopg.connect(url) as conn:
        for book in books:
            conn.execute(
                UPSERT_SQL,
                {
                    "source": "openlibrary",
                    "source_id": book["key"],
                    "title": book["title"],
                    "description": book.get("description", "desc " + book["title"]),
                    "description_clean": book.get("description", "desc " + book["title"]),
                    "genres": ["Fiction"],
                    "tags": json.dumps([{"name": "Space", "rank": 50}]),
                    "start_year": 1965,
                    "cover_medium": None,
                    "cover_large": None,
                    "synonyms": [],
                },
            )
        for key, v in vectors.items():
            conn.execute(
                "INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)"
                " SELECT id, %s, %s::vector, now() FROM media WHERE source_id = %s",
                (model, v, key),
            )
        conn.commit()


def seed_anime(url: str, anime_id: int, title: str, model: str) -> None:
    import psycopg

    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO media (id, media_type, medium, title_romaji)"
            " VALUES (%s, 'ANIME', 'anime', %s)",
            (anime_id, title),
        )
        conn.execute(
            "INSERT INTO embeddings (media_id, embed_model, embedding) VALUES (%s, %s, %s::vector)",
            (anime_id, model, vec(0.9)),
        )
        conn.commit()


def snapshot(url: str):
    import psycopg

    with psycopg.connect(url) as conn:
        media = conn.execute(
            "SELECT source, coalesce(source_id, id::text), title_romaji, description, medium"
            " FROM media ORDER BY 1, 2"
        ).fetchall()
        emb = conn.execute(
            "SELECT coalesce(m.source_id, m.id::text), e.embed_model, e.embedding::text"
            " FROM embeddings e JOIN media m ON m.id = e.media_id ORDER BY 1"
        ).fetchall()
        seq = conn.execute("SELECT last_value FROM media_external_id_seq").fetchone()[0]
    return media, emb, seq


def build_source(urls, use, tmp_path, part_bytes=4096):
    """DB A: three books, two embedded, plus an anime row that must not travel."""
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    use(urls[0])
    ensure_schema()
    model = bd.current_model_id()
    seed_anime(urls[0], 1, "Cowboy Bebop", model)
    seed_books(
        urls[0],
        [
            {"key": "/works/OL1W", "title": "Dune"},
            {"key": "/works/OL2W", "title": "Solaris"},
            {"key": "/works/OL3W", "title": "Roadside Picnic"},
        ],
        {"/works/OL1W": vec(0.1), "/works/OL2W": vec(0.2)},
        model,
    )
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"dump_version": "dump-A", "import_complete": True}))
    out = tmp_path / "out"
    manifest = bd.export(out, state, part_bytes=part_bytes)
    return out, manifest, model


def test_round_trip_merges_into_an_existing_catalogue(two_dbs, tmp_path):
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, manifest, model = build_source(urls, use, tmp_path)
    assert manifest["rows"] == 3 and manifest["embedded"] == 2
    assert len(manifest["parts"]) > 1, "small part size, so the split itself is exercised"

    # DB B already has its own anime, a book it imported itself more
    # recently than the dataset, a book the dataset does not have, and a
    # vector it computed itself for one of the dataset's books.
    use(urls[1])
    ensure_schema()
    seed_anime(urls[1], 7, "Mushishi", model)
    seed_books(
        urls[1],
        [
            {"key": "/works/OL2W", "title": "Solaris", "description": "newer local text"},
            {"key": "/works/OL9W", "title": "Local Only"},
        ],
        {"/works/OL2W": vec(0.5)},
        model,
    )
    import psycopg

    with psycopg.connect(urls[1]) as conn:
        # Newer than anything in the dataset.
        conn.execute(
            "UPDATE media SET synced_at = now() + interval '1 day' WHERE source_id = '/works/OL2W'"
        )
        conn.commit()
    _, _, seq_before = snapshot(urls[1])

    result = bd.load(out / bd.MANIFEST_NAME, tmp_path / "loaded.json")
    media, emb, seq_after = snapshot(urls[1])
    by_key = {row[1]: row for row in media}

    assert by_key["7"][2] == "Mushishi", "the target's anime is untouched"
    assert "1" not in by_key, "the source's anime never travelled"
    assert by_key["/works/OL9W"][2] == "Local Only", "a book the dataset lacks is kept"
    assert by_key["/works/OL1W"][2] == "Dune" and by_key["/works/OL3W"][2] == "Roadside Picnic"
    assert by_key["/works/OL2W"][3] == "newer local text", "a newer local row is not rolled back"
    assert result["inserted"] == 2 and result["updated"] == 0

    vectors = {row[0]: row[2] for row in emb}
    assert "/works/OL1W" in vectors, "the dataset's vector arrived"
    assert "/works/OL3W" not in vectors, "an unembedded book stays unembedded"
    assert vectors["/works/OL2W"].startswith("[0.5"), "a local vector is not replaced"
    assert seq_after - seq_before == 2, "ids are drawn only for the two new books"
    assert json.loads((tmp_path / "loaded.json").read_text())["dump_version"] == "dump-A"


def test_loading_twice_changes_nothing(two_dbs, tmp_path):
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, _, _ = build_source(urls, use, tmp_path)
    use(urls[1])
    ensure_schema()
    bd.load(out / bd.MANIFEST_NAME)
    first = snapshot(urls[1])
    again = bd.load(out / bd.MANIFEST_NAME)
    assert again["inserted"] == 0 and again["updated"] == 0 and again["embeddings"] == 0
    assert snapshot(urls[1]) == first, "not even the sequence moved"


def test_a_tampered_part_loads_nothing(two_dbs, tmp_path):
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, manifest, _ = build_source(urls, use, tmp_path)
    victim = out / manifest["parts"][-1]["name"]
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))

    use(urls[1])
    ensure_schema()
    before = snapshot(urls[1])
    import zlib

    import psycopg

    # Which layer notices depends on where the flipped byte lands: zlib
    # rejects the stream, COPY rejects what it decodes to, or the hash
    # check catches it at the end of the part. Any of the three is a
    # refusal; what matters is that nothing was committed.
    with pytest.raises((ValueError, zlib.error, psycopg.Error)):
        bd.load(out / bd.MANIFEST_NAME)
    assert snapshot(urls[1]) == before, "rolled back whole, not half-loaded"


def test_a_missing_part_is_refused(two_dbs, tmp_path):
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, manifest, _ = build_source(urls, use, tmp_path)
    manifest["parts"] = manifest["parts"][:-1]
    (out / bd.MANIFEST_NAME).write_text(json.dumps(manifest))

    use(urls[1])
    ensure_schema()
    before = snapshot(urls[1])
    with pytest.raises(ValueError, match="mid-stream"):
        bd.load(out / bd.MANIFEST_NAME)
    assert snapshot(urls[1]) == before


def test_a_schema_mismatch_is_refused_before_any_data(two_dbs, tmp_path):
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, manifest, _ = build_source(urls, use, tmp_path)
    manifest["columns"][4][1] = "integer"  # title_romaji was text
    (out / bd.MANIFEST_NAME).write_text(json.dumps(manifest))

    use(urls[1])
    ensure_schema()
    with pytest.raises(ValueError, match="columns do not match"):
        bd.load(out / bd.MANIFEST_NAME)


def test_another_model_s_vectors_are_left_behind(two_dbs, tmp_path):
    """Two models' vectors are two different spaces. Searching across them
    would rank nonsense, so the rows load and the vectors do not."""
    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, manifest, _ = build_source(urls, use, tmp_path)
    manifest["embed_model"] = "some/other-model#text-v1"
    (out / bd.MANIFEST_NAME).write_text(json.dumps(manifest))

    use(urls[1])
    ensure_schema()
    result = bd.load(out / bd.MANIFEST_NAME)
    assert result["inserted"] == 3 and result["embeddings"] == 0


def test_the_export_is_deterministic(two_dbs, tmp_path):
    """Same rows, same bytes: ordered by key, and gzip's timestamp pinned.
    A checkpoint that hashes differently every run would make an
    unchanged build look changed."""
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, manifest, _ = build_source(urls, use, tmp_path, part_bytes=10**9)
    again = bd.export(tmp_path / "again", None, part_bytes=10**9)
    assert [p["sha256"] for p in manifest["parts"]] == [p["sha256"] for p in again["parts"]]


def test_prepare_ci_drops_only_the_vector_indexes(two_dbs):
    import psycopg

    from pipeline import book_dataset as bd

    urls, use = two_dbs
    use(urls[0])
    bd.prepare_ci()
    with psycopg.connect(urls[0]) as conn:
        names = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes").fetchall()}
    assert not names & set(bd.VECTOR_INDEXES)
    assert any("trgm" in n or "title" in n for n in names), "everything else stays"


def test_the_embed_upsert_writes_on_a_real_server(two_dbs):
    """The 5 Nov breakage, end to end: the tuple the embed loop builds,
    sent through the SQL it is sent through, landing as a row."""
    import psycopg
    from pgvector.psycopg import register_vector

    from api.db import ensure_schema
    from pipeline.embed import UPSERT_SQL, upsert_params

    urls, use = two_dbs
    use(urls[0])
    ensure_schema()
    seed_anime(urls[0], 5, "Planetes", "seed-model")
    import numpy as np

    with psycopg.connect(urls[0]) as conn:
        register_vector(conn)
        rows = [(5, "Planetes", None, "desc", [], [])]
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, upsert_params(rows, "model-v1", [np.full(DIM, 0.25)]))
        conn.commit()
        got = conn.execute(
            "SELECT vector_dims(embedding) FROM embeddings"
            " WHERE media_id = 5 AND embed_model = 'model-v1'"
        ).fetchone()
    assert got == (DIM,)


def test_a_seed_load_builds_the_vector_indexes_afterwards(two_dbs, tmp_path):
    """Dropped for the bulk load, and back once it is in - the install
    must not be left searching by sequential scan."""
    import psycopg

    from api.db import ensure_schema
    from pipeline import book_dataset as bd

    urls, use = two_dbs
    out, _, _ = build_source(urls, use, tmp_path)
    use(urls[1])
    ensure_schema()
    result = bd.load(out / bd.MANIFEST_NAME, ensure=False, rebuild_indexes=True)
    assert result["inserted"] == 3 and result["embeddings"] == 2
    with psycopg.connect(urls[1]) as conn:
        names = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes").fetchall()}
    assert set(bd.VECTOR_INDEXES) <= names


@pytest.fixture
def settings_db(two_dbs):
    """One database with schema, a dict-row connection, and fresh caches."""
    import psycopg
    from psycopg.rows import dict_row

    import api.routers.datasets as ds_router
    from api.db import ensure_schema

    urls, use = two_dbs
    use(urls[0])
    ensure_schema()
    ds_router.forget_counts()
    with psycopg.connect(urls[0], row_factory=dict_row, autocommit=True) as conn:
        yield urls[0], conn
    ds_router.forget_counts()


def test_until_someone_chooses_only_media_with_data_are_on(settings_db):
    """An install without books shows no empty Books tab."""
    from api.routers.app_settings import enabled_media

    url, conn = settings_db
    assert enabled_media(conn) == ["anime", "manga", "light_novel", "one_shot"]


def test_switching_books_on_requests_the_install_and_resaving_does_not(settings_db):
    from api.datasets import read_install, write_install
    from api.routers.app_settings import _save_enabled_media, enabled_media

    url, conn = settings_db
    _save_enabled_media(conn, ["anime", "book"])
    assert enabled_media(conn) == ["anime", "book"]
    assert read_install(conn, "books")["state"] == "queued"

    # The worker finishes it; saving the same selection again must not
    # start a second download.
    write_install(conn, "books", {"state": "done", "message": "ok"})
    _save_enabled_media(conn, ["anime", "book"])
    assert read_install(conn, "books")["state"] == "done"

    # Off and on again, with books still absent: a new request.
    _save_enabled_media(conn, ["anime"])
    _save_enabled_media(conn, ["anime", "book"])
    assert read_install(conn, "books")["state"] == "queued"


def test_switching_a_medium_off_deletes_nothing(settings_db):
    from api.routers.app_settings import _save_enabled_media

    url, conn = settings_db
    seed_anime(url, 3, "Haibane Renmei", "m")
    _save_enabled_media(conn, ["manga"])
    assert conn.execute("SELECT count(*) AS n FROM media").fetchone()["n"] == 1


def test_a_medium_whose_data_is_present_is_never_installed_again(settings_db):
    from api.datasets import read_install
    from api.routers.app_settings import _save_enabled_media

    url, conn = settings_db
    seed_books(url, [{"key": "/works/OL1W", "title": "Dune"}], {}, "m")
    _save_enabled_media(conn, ["anime"])
    _save_enabled_media(conn, ["anime", "book"])
    assert read_install(conn, "books") is None


def test_the_selection_is_validated(settings_db):
    from fastapi import HTTPException

    from api.routers.app_settings import _save_enabled_media

    url, conn = settings_db
    with pytest.raises(HTTPException) as unknown:
        _save_enabled_media(conn, ["anime", "podcasts"])
    assert unknown.value.status_code == 422
    with pytest.raises(HTTPException) as empty:
        _save_enabled_media(conn, [])
    assert empty.value.status_code == 422


def test_the_datasets_listing_reports_what_is_present(settings_db, monkeypatch):
    import api.routers.datasets as ds_router

    url, conn = settings_db
    monkeypatch.setattr(ds_router, "release_available", lambda d: True if d.installable else None)
    seed_books(url, [{"key": "/works/OL1W", "title": "Dune"}], {"/works/OL1W": vec(0.1)}, "x")
    ds_router.forget_counts()
    out = {d.key: d for d in (ds_router.to_out(conn, d) for d in ds_router.DATASETS)}
    assert out["anilist"].installed and not out["anilist"].installable
    assert out["books"].installed and out["books"].items == 1
    assert out["books"].release_available is True


def test_the_worker_carries_a_request_through_real_sync_state(settings_db, monkeypatch):
    import pipeline.dataset_install as di
    from api.datasets import read_install, request_install

    url, conn = settings_db
    request_install(conn, "books")
    request_install(conn, "books")  # idempotent while under way

    def installer(request, report):
        report({"phase": "downloading", "bytes": 1, "total": 2})
        return "Installed 1 book."

    monkeypatch.setattr(di, "INSTALLERS", {"books": installer})
    assert di.run_queued() == 1
    state = read_install(conn, "books")
    assert state["state"] == "done" and state["message"] == "Installed 1 book."


def test_an_install_left_running_by_a_dead_worker_is_requeued(settings_db):
    import pipeline.dataset_install as di
    from api.datasets import read_install, write_install

    url, conn = settings_db
    write_install(conn, "books", {"state": "running", "phase": "downloading"})
    di.recover_interrupted()
    assert read_install(conn, "books")["state"] == "queued"
