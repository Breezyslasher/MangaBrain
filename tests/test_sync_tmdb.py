"""Films and TV from TMDB: what is kept, how it maps, and what is never read."""

import inspect

from pipeline import sync_tmdb

LONG = "A long enough overview. " * 12


def movie(**over):
    data = {
        "id": 550,
        "title": "Fight Club",
        "original_title": "Fight Club",
        "overview": LONG,
        "genres": [{"name": "Drama"}],
        "keywords": {"keywords": [{"name": "dual identity"}, {"name": "insomnia"}]},
        "release_date": "1999-10-15",
        "status": "Released",
        "origin_country": ["US"],
        "poster_path": "/p.jpg",
        "popularity": 99.0,
        "vote_average": 8.4,
        "vote_count": 30000,
    }
    data.update(over)
    return data


def test_a_film_maps_onto_the_media_row():
    row = sync_tmdb.row_for("movie", movie())
    assert row["source_id"] == "movie/550"
    assert (row["media_type"], row["medium"], row["format"]) == ("MOVIE", "movie", "MOVIE")
    assert row["genres"] == ["Drama"]
    assert [t["name"] for t in row["tags"]] == ["dual identity", "insomnia"]
    assert row["start_year"] == 1999 and row["status"] == "FINISHED" and row["country"] == "US"
    assert row["cover_medium"].endswith("/w342/p.jpg")


def test_short_overviews_adult_and_japanese_animation_are_kept_out():
    assert sync_tmdb.row_for("movie", movie(overview="Too short.")) is None
    assert sync_tmdb.row_for("movie", movie(adult=True)) is None
    anime = movie(genres=[{"name": "Animation"}], origin_country=["JP"])
    assert sync_tmdb.row_for("movie", anime) is None
    western = movie(genres=[{"name": "Animation"}], origin_country=["US"])
    assert sync_tmdb.row_for("movie", western) is not None


def test_a_short_overview_counts_with_five_keywords():
    # Iron Man's TMDB overview is about 130 characters.
    five = {"keywords": [{"name": f"k{i}"} for i in range(5)]}
    short = "S" * 130
    assert sync_tmdb.row_for("movie", movie(overview=short, keywords=five)) is not None
    four = {"keywords": five["keywords"][:4]}
    assert sync_tmdb.row_for("movie", movie(overview=short, keywords=four)) is None
    assert sync_tmdb.row_for("movie", movie(overview="S" * 79, keywords=five)) is None
    # TV's keywords reply is shaped {"results": [...]}.
    tv = movie(overview=short, keywords={"results": five["keywords"]}, name="Show")
    assert sync_tmdb.row_for("tv", tv) is not None


def test_no_popularity_vote_or_rating_is_read_or_stored():
    row = sync_tmdb.row_for("movie", movie())
    assert not {"popularity", "vote_average", "vote_count", "average_score"} & set(row)
    code = inspect.getsource(sync_tmdb.row_for)
    for field in ("popularity", "vote_average", "vote_count"):
        assert field not in code, field
    assert "popularity" not in sync_tmdb.UPDATE_SQL and "average_score" not in sync_tmdb.INSERT_SQL


class FakeConn:
    def __init__(self, existing: set[str]):
        self.existing = existing
        self.calls: list[str] = []

    def execute(self, sql, params=None):
        conn = self

        class Result:
            rowcount = 0

        result = Result()
        if sql.lstrip().startswith("UPDATE"):
            result.rowcount = int(params["source_id"] in conn.existing)
            conn.calls.append("update")
        elif sql.lstrip().startswith("INSERT"):
            conn.calls.append("insert")
        elif sql.startswith("DELETE"):
            result.rowcount = int(params[1] in conn.existing)
            conn.calls.append("delete")
        return result


def test_store_updates_first_and_inserts_only_when_new():
    row = sync_tmdb.row_for("movie", movie())
    conn = FakeConn({"movie/550"})
    assert sync_tmdb.store(conn, "movie", 550, row) == "kept"
    assert conn.calls == ["update"], "no id is drawn for a row that already exists"
    conn = FakeConn(set())
    sync_tmdb.store(conn, "movie", 550, row)
    assert conn.calls == ["update", "insert"]


def test_a_title_that_stops_qualifying_is_removed():
    conn = FakeConn({"movie/550"})
    assert sync_tmdb.store(conn, "movie", 550, None) == "removed"
    assert sync_tmdb.store(FakeConn(set()), "movie", 550, None) == "dropped"


def test_the_full_pass_runs_once_then_leaves_it_to_the_changes_feed():
    source = inspect.getsource(sync_tmdb.full)
    assert 'if state.get("complete"):' in source and "continue" in source
    assert '"after": chunk[-1]' in source, "resumable mid-pass"


def test_rows_are_refreshed_inside_the_six_month_cache_limit():
    assert sync_tmdb.REFRESH_DAYS < 182


def test_a_v4_token_goes_in_a_header_and_a_v3_key_in_the_query():
    v4 = sync_tmdb.TmdbClient("aaa.bbb.ccc")
    v3 = sync_tmdb.TmdbClient("0123456789abcdef0123456789abcdef")
    try:
        assert v4.http.headers["Authorization"] == "Bearer aaa.bbb.ccc" and not v4.params
        assert v3.params == {"api_key": "0123456789abcdef0123456789abcdef"}
    finally:
        v4.close()
        v3.close()


def test_films_are_an_installable_dataset_never_a_release():
    from api.datasets import BY_KEY
    from pipeline.dataset_install import INSTALLERS

    dataset = BY_KEY["tmdb"]
    assert dataset.media == ("movie",) and dataset.source == "tmdb"
    # TMDB's terms, paragraph 3, give this notice word for word.
    assert (
        "uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB"
        in dataset.description
    )
    assert "tmdb" in INSTALLERS
    install = inspect.getsource(INSTALLERS["tmdb"])
    assert "find_release_manifest" not in install and "sync_tmdb.full" in install


def test_the_tmdb_key_is_write_only():
    from api.models import AppSettingsOut

    assert "tmdb_api_key_set" in AppSettingsOut.model_fields
    assert "tmdb_api_key" not in AppSettingsOut.model_fields


def show(**over):
    data = {
        "id": 1399,
        "name": "Show",
        "original_name": "Show",
        "overview": LONG,
        "genres": [{"name": "Drama"}],
        "keywords": {"results": [{"name": "dragon"}]},
        "external_ids": {"imdb_id": "tt0944947", "tvdb_id": 121361},
        "first_air_date": "2011-04-17",
        "status": "Returning Series",
        "type": "Miniseries",
        "number_of_episodes": 8,
        "origin_country": ["GB"],
    }
    data.update(over)
    return data


def test_a_show_maps_with_its_type_episodes_and_external_ids():
    row = sync_tmdb.row_for("tv", show())
    assert (row["media_type"], row["medium"], row["format"]) == ("TV", "tv", "MINISERIES")
    assert row["episodes"] == 8 and row["status"] == "RELEASING"
    assert row["tags"][0]["name"] == "dragon"
    assert row["external_ids"] == {"imdb": "tt0944947", "tvdb": "121361"}


class DedupeConn(FakeConn):
    """FakeConn whose TVmaze lookup answers a fixed yes or no."""

    def __init__(self, existing, tvmaze_has_it):
        super().__init__(existing)
        self.tvmaze_has_it = tvmaze_has_it

    def execute(self, sql, params=None):
        if "v.source = 'tvmaze'" in sql:
            answer = (1,) if self.tvmaze_has_it else None
            self.calls.append("lookup")

            class Result:
                def fetchone(self):
                    return answer

            return Result()
        return super().execute(sql, params)


def test_a_show_tvmaze_already_has_is_not_kept_from_tmdb():
    row = sync_tmdb.row_for("tv", show())
    conn = DedupeConn({"tv/1399"}, tvmaze_has_it=True)
    assert sync_tmdb.store(conn, "tv", 1399, row) == "duplicate"
    assert conn.calls == ["lookup", "delete"], "an existing TMDB copy is removed"
    conn = DedupeConn(set(), tvmaze_has_it=False)
    assert sync_tmdb.store(conn, "tv", 1399, row) == "kept"


def test_films_are_never_checked_against_tvmaze():
    conn = DedupeConn(set(), tvmaze_has_it=True)
    assert sync_tmdb.store(conn, "movie", 550, sync_tmdb.row_for("movie", movie())) == "kept"
    assert "lookup" not in conn.calls


def test_a_tvmaze_read_removes_tmdb_shows_it_now_covers():
    from pipeline import sync_tvmaze

    assert "t.source = 'tmdb' AND t.medium = 'tv'" in sync_tvmaze.DEDUPE_SQL
    assert "dedupe(conn)" in inspect.getsource(sync_tvmaze.sync)
