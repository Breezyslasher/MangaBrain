from api.routers.search import MAX_QUERY_WORDS, word_filter


def test_multi_word_query_requires_all_words_any_order():
    sql, params = word_filter("titan attack")
    assert sql.count(" LIKE ") == 2
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
    assert params == {"word_0": "%a%", "word_1": "%dogs%", "word_2": "%purpose%"}
    assert "mb_title_search(" in sql
    assert word_filter("spider-man")[1] == {"word_0": "%spiderman%"}


def test_a_word_of_only_punctuation_is_ignored():
    assert word_filter("- ’ x")[1] == {"word_0": "%x%"}
    assert word_filter(" - ") == ("TRUE", {})


def test_the_title_side_drops_the_same_characters():
    """db/schema.sql's mb_title_search must drop exactly what the query
    side drops, plus spaces, or a word would never match its title."""
    from pathlib import Path

    from api.routers.search import IGNORED_PUNCTUATION

    schema = (Path(__file__).resolve().parent.parent / "db/schema.sql").read_text()
    body = schema[schema.index("FUNCTION mb_title_search(") :]
    assert "'" + (IGNORED_PUNCTUATION + " ").replace("'", "''") + "', ''" in body
    assert "lower(translate(" in body
    for ch in "'’‘-":
        assert ch in IGNORED_PUNCTUATION


def test_the_query_matches_the_indexed_expression():
    """The WHERE clause must be written exactly as the index is, or the
    planner cannot use it and every search scans the catalogue again."""
    from api.db import LARGE_INDEXES
    from api.routers.search import TITLE_SEARCH_SQL

    index = dict(LARGE_INDEXES)["idx_media_title_search"]
    assert TITLE_SEARCH_SQL.replace("m.", "") in index
