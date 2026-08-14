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
