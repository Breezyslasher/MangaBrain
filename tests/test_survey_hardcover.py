"""The Hardcover survey matches by ISBN and reads no popularity."""

import inspect

from pipeline import survey_hardcover as survey


class FakeOpenLibrary:
    def __init__(self, editions: dict[str, list[dict]]):
        self.editions = editions

    def request(self, method, url, **_):
        key = url.removeprefix(survey.OPENLIBRARY).removesuffix("/editions.json")
        return {"entries": self.editions.get(key, [])}


class FakeHardcover:
    def __init__(self, books: dict[str, dict]):
        self.books = books
        self.calls = 0

    def request(self, method, url, json_body=None, **_):
        self.calls += 1
        isbns = json_body["variables"]["isbns"]
        return {
            "data": {
                "editions": [
                    {"isbn_13": i, "isbn_10": None, "book": self.books[i]}
                    for i in isbns
                    if i in self.books
                ]
            }
        }


def test_matching_and_counts():
    long_text = "x" * 300
    group = [
        {"key": "/works/OL1W", "description": 250, "tags": 2},
        {"key": "/works/OL2W", "description": 220, "tags": 1},
        {"key": "/works/OL3W", "description": 210, "tags": 4},  # no ISBN
    ]
    openlibrary = FakeOpenLibrary(
        {
            "/works/OL1W": [{"isbn_13": ["978-0-00-000001-1"]}],
            "/works/OL2W": [{"isbn_10": ["0000000002"]}],
        }
    )
    hardcover = FakeHardcover(
        {
            "9780000000011": {
                "description": long_text,
                "cached_tags": {
                    "Genre": [{"tag": "Fantasy"}],
                    "Mood": [{"tag": "dark"}],
                    "Tag": [{"tag": "magic"}, {"tag": "dragons"}],
                },
            },
        }
    )
    out = survey.measure(group, openlibrary, hardcover, 200)
    assert out["with_isbn"] == 2
    assert out["matched"] == 1
    assert out["hc_long_description"] == 1
    assert out["hc_longer_description"] == 1
    assert out["hc_tags_median"] == 4
    assert out["hc_with_mood_pct"] == 100.0


def test_cached_tags_as_a_json_string():
    assert survey.tag_counts('{"Genre": [{"tag": "Horror"}]}')["Genre"] == 1
    assert survey.tag_counts(None) == {c: 0 for c in survey.TAG_CATEGORIES}


def test_no_popularity_is_requested():
    """Hardcover carries ratings and reader counts; the survey must not
    ask for them, so nothing popularity-shaped can creep in from here."""
    query = survey.EDITIONS_QUERY
    for field in ("rating", "users_count", "users_read_count", "ratings_count"):
        assert field not in query, field


def test_the_dump_is_streamed_not_cached():
    assert "cache=None" in inspect.getsource(survey.sample_works)
