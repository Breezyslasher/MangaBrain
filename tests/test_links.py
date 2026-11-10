"""Each title links to its page on the site its data came from. Every
result used to link to anilist.co with the catalogue id, which for films,
shows and books is MangaBrain's own and opened an unrelated AniList entry."""

from pathlib import Path

from api.links import links_for

ROOT = Path(__file__).resolve().parent.parent


def urls(row):
    return [(link.label, link.url) for link in links_for(row)]


def test_anilist_rows_link_to_anilist_and_mal():
    row = {"source": "anilist", "source_id": "16498", "medium": "anime", "id_mal": 16498}
    assert urls(row) == [
        ("AniList", "https://anilist.co/anime/16498"),
        ("MyAnimeList", "https://myanimelist.net/anime/16498"),
    ]
    manhwa = {"source": "anilist", "source_id": "105398", "medium": "manhwa", "id_mal": None}
    assert urls(manhwa) == [("AniList", "https://anilist.co/manga/105398")]


def test_films_shows_and_books_link_to_their_own_source():
    assert urls({"id": 900001, "source": "tmdb", "source_id": "movie/1726"}) == [
        ("TMDB", "https://www.themoviedb.org/movie/1726")
    ]
    assert urls({"id": 900002, "source": "tmdb", "source_id": "tv/1399"}) == [
        ("TMDB", "https://www.themoviedb.org/tv/1399")
    ]
    assert urls({"id": 900003, "source": "tvmaze", "source_id": "82"}) == [
        ("TVmaze", "https://www.tvmaze.com/shows/82")
    ]
    assert urls({"id": 900004, "source": "openlibrary", "source_id": "/works/OL45804W"}) == [
        ("Open Library", "https://openlibrary.org/works/OL45804W")
    ]


def test_no_link_is_built_from_the_catalogue_id_of_another_source():
    for row in (
        {"id": 900001, "source": "tmdb", "source_id": "movie/1726"},
        {"id": 900003, "source": "tvmaze", "source_id": "82"},
        {"id": 900004, "source": "openlibrary", "source_id": "/works/OL1W"},
    ):
        assert all("anilist" not in url for _, url in urls(row))


def test_the_web_client_uses_the_server_links():
    app = (ROOT / "web/app.js").read_text()
    assert "anilist.co" not in app
    assert "myanimelist.net" not in app
    assert "seed.links" in app and "media.links" in app


def test_media_cols_carry_the_source():
    from api.db import MEDIA_COLS

    assert "m.source, m.source_id" in MEDIA_COLS
