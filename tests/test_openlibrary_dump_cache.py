"""The dump cache, which exists because the import kept being killed.

The import runs under `docker exec`, so anything that recreates the
worker container ends it, and the dump is a single gzip stream with no
seeking into it. Caching the file is the only resume available, so these
tests cover the parts that decide whether a cached file is trusted.
"""

import gzip
import json
from pathlib import Path

import pytest

import pipeline.openlibrary as ol
from pipeline.openlibrary import DUMP_CACHE, ensure_dump, iter_works

CURRENT = "https://archive.org/download/ol_dump_2026-10-31/ol_dump_works_2026-10-31.txt.gz"


@pytest.fixture(autouse=True)
def current_dump(monkeypatch):
    """Every test here knows which dump is current without asking.

    ensure_dump asks Open Library before trusting its cache. Left to do
    that, a test on a runner with network access would find its fixture
    unstamped, delete it, and start downloading 4 GB.
    """
    monkeypatch.setattr(ol, "dump_version", lambda url=ol.DUMP_URL: CURRENT)


def write_dump(path: Path, titles: list[str], version: str | None = CURRENT) -> None:
    """A dump in the real five-column shape, JSON last, stamped as cached."""
    rows = []
    for i, title in enumerate(titles, start=1):
        work = {"key": f"/works/OL{i}W", "title": title, "subjects": ["Fiction"]}
        rows.append(f"/type/work\t/works/OL{i}W\t1\t2026-01-01\t{json.dumps(work)}")
    path.write_bytes(gzip.compress(("\n".join(rows) + "\n").encode()))
    if version is not None:
        path.with_name(path.name + ".version").write_text(version)


def test_a_cached_dump_is_read_from_disk_and_never_fetched(tmp_path):
    """The whole point. With the file present no request is made, which
    is what turns a restart from an hour and three quarters into a
    minute."""
    cache = tmp_path / "works.txt.gz"
    write_dump(cache, ["Dune", "Solaris", "Roadside Picnic"])

    seen = [work["title"] for _, work in iter_works(cache=cache)]
    assert seen == ["Dune", "Solaris", "Roadside Picnic"]


def test_the_row_limit_still_applies_to_a_cached_dump(tmp_path):
    cache = tmp_path / "works.txt.gz"
    write_dump(cache, ["Dune", "Solaris", "Roadside Picnic"])

    assert [t for _, t in ((r, w["title"]) for r, w in iter_works(limit=2, cache=cache))] == [
        "Dune",
        "Solaris",
    ]


def test_an_existing_cache_is_reported_complete_without_a_request(tmp_path):
    cache = tmp_path / "works.txt.gz"
    write_dump(cache, ["Dune"])
    path, complete = ensure_dump(cache, url="http://127.0.0.1:1/never-requested")
    assert (path, complete) == (cache, True)


def test_an_unwritable_cache_falls_back_to_streaming(capsys):
    """A cache that cannot be written is no cache, not a failure. The
    default path exists only inside the container; on a CI runner /root
    is not writable, and the survey workflow would have died on it.

    The download is stubbed rather than allowed to happen: a unit test
    that reaches for a 4 GB dump on a runner that happens to have
    network access is not a unit test."""
    import pipeline.openlibrary as ol

    asked: list[str] = []

    def fake_chunks(url, max_attempts=8, start=0, totals=None):
        asked.append(url)
        return iter(())

    original = ol.download_chunks
    ol.download_chunks = fake_chunks
    try:
        assert list(iter_works(limit=1, cache=Path("/proc/nope/works.txt.gz"))) == []
    finally:
        ol.download_chunks = original

    assert "no dump cache" in capsys.readouterr().out
    assert asked == [ol.DUMP_URL], "it fell through to the streaming path"


def test_a_partial_download_is_not_promoted_to_the_cache(tmp_path):
    """Completion is size against the length the server stated. Renaming
    a short file into place would make every later run read a truncated
    dump and report the shortfall as a finished import."""
    cache = tmp_path / "works.txt.gz"

    def fake_chunks(url, max_attempts=8, start=0, totals=None):
        if totals is not None:
            totals["total"] = 999_999
        yield b"only the first bytes"

    original = ol.download_chunks
    ol.download_chunks = fake_chunks
    try:
        path, complete = ensure_dump(cache)
    finally:
        ol.download_chunks = original

    assert complete is False
    assert not cache.exists(), "a short download must not take the cache path"
    assert path.name.endswith(".part"), "it stays under .part so the next run resumes it"
    assert path.read_bytes() == b"only the first bytes"


def test_a_resumed_download_appends_from_the_offset(tmp_path):
    """The .part file's size is the Range offset, so a restart continues
    the 4 GB fetch rather than beginning it again - which it could not
    do before the cache existed."""
    cache = tmp_path / "works.txt.gz"
    part = tmp_path / "works.txt.gz.part"
    part.write_bytes(b"first half;")
    # Of the dump that is current. An unstamped partial is discarded,
    # since nothing says which file its bytes came from.
    (tmp_path / "works.txt.gz.part.version").write_text(CURRENT)
    starts: list[int] = []

    def fake_chunks(url, max_attempts=8, start=0, totals=None):
        starts.append(start)
        if totals is not None:
            totals["total"] = len(b"first half;second half")
        yield b"second half"

    original = ol.download_chunks
    ol.download_chunks = fake_chunks
    try:
        path, complete = ensure_dump(cache)
    finally:
        ol.download_chunks = original

    assert starts == [len(b"first half;")], "resumed at the bytes already on disk"
    assert complete is True
    assert path == cache and cache.read_bytes() == b"first half;second half"


def test_an_unconfirmable_size_is_not_cached_as_complete(tmp_path):
    """With no Content-Length and no Content-Range there is nothing to
    check against, so the file is not trusted as finished."""
    cache = tmp_path / "works.txt.gz"

    def fake_chunks(url, max_attempts=8, start=0, totals=None):
        yield b"who knows how much of it"

    original = ol.download_chunks
    ol.download_chunks = fake_chunks
    try:
        _, complete = ensure_dump(cache)
    finally:
        ol.download_chunks = original
    assert complete is False
    assert not cache.exists()


def test_the_default_cache_lives_where_compose_mounts_a_volume():
    """A cache on the container filesystem would be wiped by the very
    event it exists to survive: the worker being recreated."""
    import yaml

    assert str(DUMP_CACHE).startswith("/root/.cache/mangabrain/")
    for name in ("docker-compose.yml", "docker-compose.prebuilt.yml"):
        spec = yaml.safe_load((Path(__file__).resolve().parent.parent / name).read_text())
        mounts = spec["services"]["worker"]["volumes"]
        assert "dump_cache:/root/.cache/mangabrain" in mounts, name
        assert "dump_cache" in spec["volumes"], f"{name}: the volume must be declared"


def test_a_cache_of_last_month_s_dump_is_replaced(tmp_path):
    """Open Library publishes monthly. A cache that never noticed would
    re-read one old catalogue for every import after it, passing it off
    as the current one - which is what the first version of this did."""
    cache = tmp_path / "works.txt.gz"
    write_dump(cache, ["Old"], version="ol_dump_2026-09-30")
    fetched: list[int] = []

    def fake_chunks(url, max_attempts=8, start=0, totals=None):
        fetched.append(start)
        if totals is not None:
            totals["total"] = len(b"new dump")
        yield b"new dump"

    original = ol.download_chunks
    ol.download_chunks = fake_chunks
    try:
        path, complete = ensure_dump(cache)
    finally:
        ol.download_chunks = original

    assert fetched == [0], "the stale file was not resumed into; it was replaced"
    assert complete and path.read_bytes() == b"new dump"
    assert (tmp_path / "works.txt.gz.version").read_text() == CURRENT


def test_the_cache_is_used_when_the_current_dump_cannot_be_asked(tmp_path, monkeypatch):
    """Offline, or Open Library is down: an import from the cached file
    beats no import at all."""
    import httpx

    def unreachable(url=ol.DUMP_URL):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(ol, "dump_version", unreachable)
    cache = tmp_path / "works.txt.gz"
    write_dump(cache, ["Dune"], version="ol_dump_2026-09-30")
    path, complete = ensure_dump(cache)
    assert (path, complete) == (cache, True)


def test_a_partial_download_of_another_dump_is_not_resumed(tmp_path):
    """Its bytes belong to a different file. Appending this month's tail
    to last month's head would produce a dump that decompresses into
    garbage partway through."""
    cache = tmp_path / "works.txt.gz"
    (tmp_path / "works.txt.gz.part").write_bytes(b"last month's head")
    (tmp_path / "works.txt.gz.part.version").write_text("ol_dump_2026-09-30")
    starts: list[int] = []

    def fake_chunks(url, max_attempts=8, start=0, totals=None):
        starts.append(start)
        if totals is not None:
            totals["total"] = len(b"this month")
        yield b"this month"

    original = ol.download_chunks
    ol.download_chunks = fake_chunks
    try:
        path, complete = ensure_dump(cache)
    finally:
        ol.download_chunks = original
    assert starts == [0]
    assert complete and path.read_bytes() == b"this month"
