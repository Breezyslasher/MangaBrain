"""Yamtrack's book list feeds the "yamtrack" exclusion list.

Yamtrack stores Open Library edition ids; the catalog holds works. These
tests pin the edition-to-work resolution and that a resolved book lands
under the kind that matches media.id directly.
"""

from datetime import UTC, datetime

import httpx

from api.routers import yamtrack


class FakeOpenLibrary:
    def __init__(self, pages: dict[str, dict]):
        self.pages = pages
        self.requested: list[str] = []

    def request(self, method: str, url: str, **_):
        self.requested.append(url)
        if url not in self.pages:
            request = httpx.Request(method, url)
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )
        return self.pages[url]

    def close(self):
        pass


OL = "https://openlibrary.org"


def test_an_edition_resolves_to_its_work():
    client = FakeOpenLibrary({f"{OL}/books/OL1M.json": {"works": [{"key": "/works/OL9W"}]}})
    assert yamtrack.openlibrary_work_key("OL1M", client) == "/works/OL9W"


def test_a_work_id_needs_no_request():
    client = FakeOpenLibrary({})
    assert yamtrack.openlibrary_work_key("OL9W", client) == "/works/OL9W"
    assert client.requested == []


def test_a_merged_edition_follows_its_redirect():
    client = FakeOpenLibrary(
        {
            f"{OL}/books/OL1M.json": {"type": {"key": "/type/redirect"}, "location": "/books/OL2M"},
            f"{OL}/books/OL2M.json": {"works": [{"key": "/works/OL9W"}]},
        }
    )
    assert yamtrack.openlibrary_work_key("OL1M", client) == "/works/OL9W"


def test_unresolvable_ids_are_none_not_errors():
    client = FakeOpenLibrary({})
    assert yamtrack.openlibrary_work_key("OL1M", client) is None  # 404
    assert yamtrack.openlibrary_work_key("9780141", client) is None  # not an OL id
    assert yamtrack.openlibrary_work_key("OL5A", client) is None  # an author


def test_refresh_stores_books_by_media_id(monkeypatch):
    lists = {
        "anime": [{"item": {"source": "mal", "media_id": "5"}, "status": 3, "score": 8}],
        "manga": [{"item": {"source": "anilist", "media_id": "30002"}, "status": 0}],
        "book": [
            {"item": {"source": "openlibrary", "media_id": "OL1M"}, "status": 0, "score": 9},
            {"item": {"source": "openlibrary", "media_id": "OL2M"}, "status": 1},  # not in catalog
            {"item": {"source": "hardcover", "media_id": "123"}, "status": 1},
        ],
    }
    stored = {}

    monkeypatch.setattr(yamtrack, "effective_yamtrack_config", lambda: ("http://yt", "tok"))
    monkeypatch.setattr(yamtrack, "_fetch_media", lambda client, base, kind: lists[kind])
    monkeypatch.setattr(
        yamtrack,
        "openlibrary_work_key",
        lambda media_id, client: {"OL1M": "/works/OL9W", "OL2M": "/works/OL8W"}.get(media_id),
    )
    monkeypatch.setattr(yamtrack, "book_ids_by_work", lambda keys: {"/works/OL9W": 1000000007})
    monkeypatch.setattr(
        yamtrack, "store_exclusion_list", lambda name, entries: stored.update(entries)
    )
    monkeypatch.setattr(yamtrack, "get_status", lambda name: _status(len(stored)))

    status = yamtrack.refresh_yamtrack()

    assert stored == {
        ("mal_anime", 5): (False, 80),
        ("anilist", 30002): (True, None),
        ("anilist", 1000000007): (True, 90),
    }
    assert status.skipped == 2  # the book not in the catalog, and Hardcover


def test_an_instance_without_books_still_syncs(monkeypatch):
    def fetch(client, base, kind):
        if kind == "book":
            request = httpx.Request("GET", "http://yt/api/v1/media/book")
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )
        return [{"item": {"source": "mal", "media_id": "5"}, "status": 3}]

    stored = {}
    monkeypatch.setattr(yamtrack, "effective_yamtrack_config", lambda: ("http://yt", "tok"))
    monkeypatch.setattr(yamtrack, "_fetch_media", fetch)
    monkeypatch.setattr(yamtrack, "book_ids_by_work", lambda keys: {})
    monkeypatch.setattr(
        yamtrack, "store_exclusion_list", lambda name, entries: stored.update(entries)
    )
    monkeypatch.setattr(yamtrack, "get_status", lambda name: _status(len(stored)))

    yamtrack.refresh_yamtrack()
    assert ("mal_anime", 5) in stored and ("mal_manga", 5) in stored


def _status(count: int):
    return yamtrack.ExclusionStatus(
        name="yamtrack", entry_count=count, updated_at=datetime.now(UTC)
    )
