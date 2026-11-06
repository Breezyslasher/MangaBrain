"""Hardcover's library feeds the "hardcover" exclusion list.

Hardcover names books by ISBN; the catalogue holds Open Library works.
These pin the ISBN-to-work resolution, the status and rating mapping,
and that a resolved book is stored under the kind matching media.id.
"""

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from api import openlibrary_match
from api.routers import hardcover

OL = "https://openlibrary.org"


class FakeOpenLibrary:
    def __init__(self, pages: dict[str, dict]):
        self.pages = pages

    def request(self, method, url, **_):
        import httpx

        if url not in self.pages:
            request = httpx.Request(method, url)
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )
        return self.pages[url]

    def close(self):
        pass


def test_an_isbn_resolves_to_its_work():
    client = FakeOpenLibrary({f"{OL}/isbn/9780000000011.json": {"works": [{"key": "/works/OL9W"}]}})
    assert openlibrary_match.isbn_work_key("978-0-00-000001-1", client) == "/works/OL9W"
    assert openlibrary_match.isbn_work_key("not an isbn", client) is None
    assert openlibrary_match.isbn_work_key("9780000000099", client) is None  # unknown


def test_the_chosen_edition_is_tried_first():
    entry = {
        "edition": {"isbn_13": "9781111111111", "isbn_10": None},
        "book": {"editions": [{"isbn_13": "9782222222222"}, {"isbn_13": "9781111111111"}]},
    }
    assert hardcover.isbns_of(entry) == ["9781111111111", "9782222222222"]


def test_a_later_isbn_is_tried_when_the_first_is_unknown():
    client = FakeOpenLibrary({f"{OL}/isbn/9782222222222.json": {"works": [{"key": "/works/OL5W"}]}})
    entry = {
        "edition": {"isbn_13": "9781111111111"},
        "book": {"editions": [{"isbn_13": "9782222222222"}]},
    }
    assert hardcover.work_of(entry, client) == "/works/OL5W"


def test_ratings_scale_to_100_and_unrated_is_none():
    assert hardcover._score(4.5) == 90
    assert hardcover._score("3.0") == 60
    assert hardcover._score(None) is None
    assert hardcover._score(0) is None


def test_graphql_errors_are_reported_not_swallowed():
    with pytest.raises(HTTPException):
        hardcover._user_books({"errors": [{"message": "field not found"}]})


def test_me_is_read_as_a_list_of_one():
    assert hardcover._user_books({"data": {"me": [{"user_books": [{"status_id": 3}]}]}}) == [
        {"status_id": 3}
    ]


def test_no_popularity_is_requested():
    """The reader's own rating is read (it weights For you); the book's
    rating, reader and rating counts are popularity, and are not."""
    query = hardcover.LIBRARY_QUERY
    for field in ("users_count", "users_read_count", "ratings_count", "cached_tags"):
        assert field not in query, field
    book = query[query.index("book {") :]
    assert "rating" not in book


def test_refresh_stores_books_by_media_id(monkeypatch):
    library = [
        {"status_id": 3, "rating": 4.5, "edition": {"isbn_13": "A"}},
        {"status_id": 1, "rating": None, "edition": {"isbn_13": "B"}},
        {"status_id": 3, "edition": {"isbn_13": "C"}},  # work not in the catalogue
        {"status_id": 3, "edition": None, "book": {"editions": []}},  # no ISBN
    ]
    stored = {}
    monkeypatch.setattr(hardcover, "effective_hardcover_token", lambda: "tok")
    monkeypatch.setattr(hardcover, "fetch_library", lambda client: library)
    monkeypatch.setattr(
        hardcover,
        "isbn_work_key",
        lambda isbn, client: {"A": "/works/OL1W", "B": "/works/OL2W", "C": "/works/OL3W"}.get(isbn),
    )
    monkeypatch.setattr(
        hardcover,
        "book_ids_by_work",
        lambda keys: {"/works/OL1W": 1000000001, "/works/OL2W": 1000000002},
    )
    monkeypatch.setattr(hardcover, "store_exclusion_list", lambda name, e: stored.update(e))
    monkeypatch.setattr(
        hardcover,
        "get_status",
        lambda name: hardcover.ExclusionStatus(
            name=name, entry_count=len(stored), updated_at=datetime.now(UTC)
        ),
    )
    status = hardcover.refresh_hardcover()
    assert stored == {
        ("anilist", 1000000001): (False, 90),
        ("anilist", 1000000002): (True, None),
    }
    assert status.skipped == 2


def test_refresh_without_a_token_says_so(monkeypatch):
    monkeypatch.setattr(hardcover, "effective_hardcover_token", lambda: "")
    with pytest.raises(HTTPException) as exc:
        hardcover.refresh_hardcover()
    assert exc.value.status_code == 422


def test_catalogue_lookup_reads_dict_rows(monkeypatch):
    """The API pool returns dict rows; unpacking one as a tuple yields its
    keys, which is how the first Yamtrack books version matched nothing."""

    class Conn:
        def execute(self, sql, params):
            class Result:
                def fetchall(self):
                    return [{"source_id": "/works/OL1W", "id": 1000000001}]

            return Result()

    class Pool:
        @contextmanager
        def connection(self):
            yield Conn()

    monkeypatch.setattr(openlibrary_match, "get_pool", lambda: Pool())
    assert openlibrary_match.book_ids_by_work(["/works/OL1W"]) == {"/works/OL1W": 1000000001}


def test_the_token_is_write_only():
    from api.models import AppSettingsOut

    assert "hardcover_token_set" in AppSettingsOut.model_fields
    assert "hardcover_token" not in AppSettingsOut.model_fields


def test_hardcover_needs_the_access_token():
    from api.main import API_PREFIXES

    assert "/hardcover" in API_PREFIXES
