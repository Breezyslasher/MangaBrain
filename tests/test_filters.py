from api.filters import build_filters


def test_adult_excluded_by_default_at_sql_level():
    sql, params = build_filters()
    assert "m.is_adult = FALSE" in sql
    assert params == {}


def test_adult_toggle_removes_clause():
    sql, _ = build_filters(adult=True)
    assert "is_adult" not in sql


def test_all_filters_produce_bound_params():
    sql, params = build_filters(
        adult=False,
        formats=["tv", "MOVIE"],
        year_min=1990,
        year_max=2020,
        min_score=70,
        countries=["jp"],
        statuses=["finished"],
        mal_user="  SomeUser ",
    )
    for clause in (
        "m.is_adult = FALSE",
        "m.format = ANY",
        "m.start_year >=",
        "m.start_year <=",
        "m.average_score >=",
        "m.country_of_origin = ANY",
        "m.status = ANY",
        "NOT EXISTS",
    ):
        assert clause in sql
    assert params["f_formats"] == ["TV", "MOVIE"]
    assert params["f_countries"] == ["JP"]
    assert params["f_statuses"] == ["FINISHED"]
    assert params["f_mal_user"] == "someuser"
    assert "%(f_year_min)s" in sql and params["f_year_min"] == 1990
    assert "mal_list_entries" in sql


def test_genre_and_tag_filters():
    sql, params = build_filters(
        genres_include=["Romance", "Drama"],
        genres_exclude=["Ecchi"],
        tags_include=["Time Travel"],
        tags_exclude=["Harem"],
    )
    assert "m.genres @> %(f_genres_inc)s::text[]" in sql
    assert "NOT (m.genres && %(f_genres_exc)s::text[])" in sql
    assert params["f_genres_inc"] == ["Romance", "Drama"]
    assert params["f_genres_exc"] == ["Ecchi"]
    assert params["f_tags_inc"] == ["Time Travel"]
    assert params["f_tags_exc"] == ["Harem"]


def test_max_popularity_is_a_filter_and_treats_unknown_as_obscure():
    sql, params = build_filters(max_popularity=10000)
    assert "(m.popularity IS NULL OR m.popularity <= %(f_max_pop)s)" in sql
    assert params["f_max_pop"] == 10000


def test_mal_exclusion_pairs_list_type_with_media_type():
    # MAL anime and manga ids are separate id spaces: anime #1 on the user's
    # list must not exclude manga #1. The clause has to pair list_type with
    # the row's media_type.
    sql, _ = build_filters(mal_user="someuser")
    assert "l.list_type = lower(m.media_type)" in sql
