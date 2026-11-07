from api.routers.search import MAX_QUERY_WORDS, word_filter


def test_multi_word_query_requires_all_words_any_order():
    sql, params = word_filter("titan attack")
    assert sql.count("ILIKE") == 2
    assert " AND " in sql
    assert params == {"word_0": "%titan%", "word_1": "%attack%"}


def test_empty_query_matches_everything():
    assert word_filter("   ") == ("TRUE", {})


def test_word_count_is_capped():
    sql, params = word_filter(" ".join(str(i) for i in range(20)))
    assert len(params) == MAX_QUERY_WORDS


def test_punctuation_is_dropped_from_query_words():
    # A phone keyboard types a curly apostrophe; titles mostly hold a
    # straight one. Both are dropped, on both sides.
    sql, params = word_filter("A Dog’s Purpose")
    assert params == {"word_0": "%A%", "word_1": "%Dogs%", "word_2": "%Purpose%"}
    assert "h.hay" in sql
    assert word_filter("spider-man")[1] == {"word_0": "%spiderman%"}


def test_a_word_of_only_punctuation_is_ignored():
    assert word_filter("- ’ x")[1] == {"word_0": "%x%"}
    assert word_filter(" - ") == ("TRUE", {})


def test_the_title_side_drops_the_same_characters():
    from api.routers.search import IGNORED_PUNCTUATION, NORMALISED_SQL

    assert "translate(" in NORMALISED_SQL
    assert IGNORED_PUNCTUATION.replace("'", "''") + " '" in NORMALISED_SQL, "spaces too"
    for ch in "'’‘-":
        assert ch in IGNORED_PUNCTUATION
