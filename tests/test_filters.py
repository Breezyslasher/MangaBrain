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


def test_anilist_exclusion_matches_media_id_directly():
    sql, params = build_filters(anilist_user="  SomeUser ")
    assert "anilist_list_entries" in sql
    assert "al.media_id = m.id" in sql
    assert params["f_anilist_user"] == "someuser"


def test_length_filters_include_unknown_lengths():
    sql, params = build_filters(max_episodes=26, max_chapters=100)
    assert "(m.episodes IS NULL OR m.episodes <= %(f_max_episodes)s)" in sql
    assert "(m.chapters IS NULL OR m.chapters <= %(f_max_chapters)s)" in sql
    assert params["f_max_episodes"] == 26
    assert params["f_max_chapters"] == 100


def test_generic_exclusion_list_pairs_mal_kinds_with_media_type():
    sql, params = build_filters(exclude_lists=["  MyList ", "kitsu"])
    assert "custom_exclusion_entries" in sql
    assert "ce.list_name = ANY(%(f_excl_lists)s)" in sql
    assert "ce.kind = 'anilist' AND ce.ext_id = m.id" in sql
    assert "ce.kind = 'mal_anime' AND m.media_type = 'ANIME'" in sql
    assert "ce.kind = 'mal_manga' AND m.media_type = 'MANGA'" in sql
    assert params["f_excl_lists"] == ["mylist", "kitsu"]


def test_exclusion_lists_with_only_blank_names_add_no_clause():
    sql, params = build_filters(exclude_lists=["  ", ""])
    assert "custom_exclusion_entries" not in sql
    assert params == {}


def test_keep_planned_adds_status_guards_to_every_list_source():
    sql, _ = build_filters(
        mal_user="u", anilist_user="u", exclude_lists=["kitsu"], keep_planned=True
    )
    # MAL has two status vocabularies because the source changed: the
    # official API sends "plan_to_watch"/"plan_to_read", and Jikan - which
    # this read until its user endpoints were discontinued - sent numeric
    # codes where 6 was plan to watch/read. A list cached under either
    # shape must honour the toggle, so both are guarded; AniList uses the
    # MediaListStatus PLANNING value; generic lists carry a planned flag.
    assert "l.status NOT IN ('6', 'plan_to_watch', 'plan_to_read')" in sql
    assert "al.status <> 'PLANNING'" in sql
    assert "NOT ce.planned" in sql


def test_planned_entries_excluded_by_default():
    sql, _ = build_filters(mal_user="u", anilist_user="u", exclude_lists=["kitsu"])
    assert "l.status" not in sql
    assert "al.status" not in sql
    assert "ce.planned" not in sql


def test_mal_exclusion_pairs_list_type_with_media_type():
    # MAL anime and manga ids are separate id spaces: anime #1 on the user's
    # list must not exclude manga #1. The clause has to pair list_type with
    # the row's media_type.
    sql, _ = build_filters(mal_user="someuser")
    assert "l.list_type = lower(m.media_type)" in sql


def test_an_unknown_year_passes_both_bounds():
    """The same rule the length bounds follow: missing data is not
    evidence against a title.

    It mattered little while AniList supplied a year for almost
    everything. An Open Library work carries no reliable date -
    publication belongs to its editions - so about four books in five
    have none, and a bare >= excluded every one of them from any
    year-filtered search without saying so.
    """
    from api.filters import build_filters

    def joined(sql):
        return " ".join(sql) if isinstance(sql, list) else str(sql)

    assert "m.start_year IS NULL OR m.start_year >= " in joined(build_filters(year_min=1990)[0])
    assert "m.start_year IS NULL OR m.start_year <= " in joined(build_filters(year_max=2000)[0])


def test_the_year_bounds_match_the_length_bounds():
    """Two filters on the same principle must not disagree about it."""
    from api.filters import build_filters

    def joined(sql):
        return " ".join(sql) if isinstance(sql, list) else str(sql)

    year = joined(build_filters(year_min=1990)[0])
    episodes = joined(build_filters(min_episodes=12)[0])
    assert ("IS NULL OR" in year) == ("IS NULL OR" in episodes)


def test_an_unscored_title_passes_the_score_floor():
    """The third filter to need this rule, and the one that would have
    emptied the Books tab outright.

    An Open Library work has no rating this project will store, so every
    book has a NULL average_score. A bare >= is NULL for them, so moving
    the score slider at all returned nothing, with no explanation.
    """
    from api.filters import build_filters

    def joined(sql):
        return " ".join(sql) if isinstance(sql, list) else str(sql)

    assert "m.average_score IS NULL OR m.average_score >= " in joined(
        build_filters(min_score=70)[0]
    )


def test_every_numeric_bound_treats_missing_data_the_same_way():
    """Lengths, years and scores are one principle, not three
    decisions. Each was fixed separately, after a medium arrived that
    exposed it - so the rule is pinned across all of them rather than
    per filter."""
    from api.filters import build_filters

    def joined(sql):
        return " ".join(sql) if isinstance(sql, list) else str(sql)

    for kwargs, column in (
        ({"min_episodes": 12}, "m.episodes"),
        ({"max_episodes": 26}, "m.episodes"),
        ({"min_chapters": 10}, "m.chapters"),
        ({"year_min": 1990}, "m.start_year"),
        ({"year_max": 2020}, "m.start_year"),
        ({"min_score": 70}, "m.average_score"),
    ):
        sql = joined(build_filters(**kwargs)[0])
        assert f"{column} IS NULL OR" in sql, kwargs
