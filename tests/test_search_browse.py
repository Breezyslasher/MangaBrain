"""Search accepts the filter panel, and works with no query at all.

/search was a seed picker: it took a query, a medium and the adult toggle
and nothing else. The filter panel reached /recommend, /random and /foryou
but not here, so with a catalog of 22,667 fully filtered rows there was no
way to ask "obscure 2015-2020 ONA horror with an English dub" - you had to
name a title first and ask what resembled it.

build_filters has said "for recommend, random, and search" in its docstring
since it was written. Search simply never called it.

Making `q` optional is what turns the same endpoint into browse: filters
with no query list the catalog. Ordering then has to come from somewhere,
and it deliberately is not popularity - the project bars popularity from
ranking, and a browse list sorted by member count would put it back in the
least visible place available.
"""

import inspect

from api.routers import search as search_module


def test_the_query_is_optional():
    """Which is what makes browse possible at all."""
    sig = inspect.signature(search_module.search)
    default = sig.parameters["q"].default
    assert getattr(default, "default", default) is None


def test_search_takes_the_same_filters_as_the_recommender():
    sig = inspect.signature(search_module.search)
    for name in (
        "format",
        "year_min",
        "year_max",
        "min_score",
        "country",
        "status",
        "genre_in",
        "genre_ex",
        "tag_in",
        "tag_ex",
        "max_popularity",
        "max_episodes",
        "max_chapters",
        "min_episodes",
        "min_chapters",
        "dubbed",
        "dub_lang",
    ):
        assert name in sig.parameters, name


def test_search_supports_paging():
    sig = inspect.signature(search_module.search)
    assert "offset" in sig.parameters


def test_search_delegates_to_the_shared_filter_builder():
    """Rather than growing a second, drifting copy of the same clauses."""
    source = inspect.getsource(search_module.search)
    assert "build_filters(" in source


def test_a_browse_listing_is_not_ordered_by_popularity():
    """The one ordering this project must not use."""
    assert "popularity" not in search_module.BROWSE_ORDER.lower()
    assert "favourite" not in search_module.BROWSE_ORDER.lower()
    assert "members" not in search_module.BROWSE_ORDER.lower()


def test_a_browse_listing_is_deterministic():
    """Paging over an unstable order repeats and skips rows."""
    assert "m.id" in search_module.BROWSE_ORDER
    assert "start_year" in search_module.BROWSE_ORDER


def test_the_rank_expression_is_only_used_with_a_query():
    """word_similarity against an absent query ranks everything equally."""
    source = inspect.getsource(search_module.search)
    assert 'rank_sql=RANK_SQL if q else "0"' in source
    assert 'order_sql="match_rank DESC, m.id" if q else BROWSE_ORDER' in source


def test_the_query_parameter_is_only_bound_when_present():
    """psycopg rejects a parameter the statement does not reference."""
    source = inspect.getsource(search_module.search)
    assert 'if q:\n        params["q"] = q' in source


def test_the_adult_default_still_excludes_adult_titles():
    """It moved from an inline clause into build_filters; same behaviour."""
    from api.filters import build_filters

    sql, _ = build_filters(adult=False)
    assert "m.is_adult = FALSE" in sql
    sql, _ = build_filters(adult=True)
    assert "is_adult" not in sql


def test_the_ui_has_a_button_that_applies_the_filters():
    from pathlib import Path

    web = Path(__file__).parent.parent / "web"
    html = (web / "index.html").read_text()
    app = (web / "app.js").read_text()
    assert 'id="browseBtn"' in html
    # Typing stays a plain title lookup; the button opts into the filters.
    assert 'runSearch($("search").value, true)' in app
    assert "withFilters" in app
    assert "Object.assign(params, filterParams())" in app
