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
