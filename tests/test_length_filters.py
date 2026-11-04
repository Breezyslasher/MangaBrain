"""Length floors, mirroring the caps.

Only max_episodes and max_chapters existed, which was an omission rather
than a decision - "something long to sink into" is as ordinary a request as
"something short", and with only a cap there was no way to express it.

Unknown lengths pass both bounds. That is the same rule the caps already
applied and the same rule the project applies everywhere else: missing data
is not evidence against a title. The dub filter is positive-only for this
reason, and english_va_count = 0 is never served as sub-only. The asymmetric
alternative - excluding NULL from a floor while a cap keeps it - would hide
every still-releasing series from "at least 50 episodes" while leaving them
in "at most 26", which is incoherent rather than merely strict.
"""

from api.filters import build_filters


def joined(sql) -> str:
    return " ".join(sql) if isinstance(sql, list) else str(sql)


def test_a_floor_is_applied_for_anime():
    sql, params = build_filters(min_episodes=50)
    assert "m.episodes >= %(f_min_episodes)s" in joined(sql)
    assert params["f_min_episodes"] == 50


def test_a_floor_is_applied_for_print_media():
    sql, params = build_filters(min_chapters=100)
    assert "m.chapters >= %(f_min_chapters)s" in joined(sql)
    assert params["f_min_chapters"] == 100


def test_unknown_lengths_pass_the_floor():
    """A still-releasing series has no count; that is not a short series."""
    assert "m.episodes IS NULL OR m.episodes >= " in joined(build_filters(min_episodes=50)[0])
    assert "m.chapters IS NULL OR m.chapters >= " in joined(build_filters(min_chapters=50)[0])


def test_unknown_lengths_still_pass_the_cap():
    """The behaviour the floor is mirroring, pinned so they stay symmetric."""
    assert "m.episodes IS NULL OR m.episodes <= " in joined(build_filters(max_episodes=26)[0])
    assert "m.chapters IS NULL OR m.chapters <= " in joined(build_filters(max_chapters=26)[0])


def test_a_floor_and_a_cap_combine_into_a_range():
    sql, params = build_filters(min_episodes=12, max_episodes=26)
    text = joined(sql)
    assert "m.episodes >= %(f_min_episodes)s" in text
    assert "m.episodes <= %(f_max_episodes)s" in text
    assert params["f_min_episodes"] == 12
    assert params["f_max_episodes"] == 26


def test_no_length_clause_without_a_bound():
    text = joined(build_filters()[0])
    assert "f_min_episodes" not in text
    assert "f_max_episodes" not in text
    assert "f_min_chapters" not in text
    assert "f_max_chapters" not in text


def test_a_floor_of_one_is_honoured_not_treated_as_absent():
    """0 and None differ; a floor of 1 is a real request."""
    sql, params = build_filters(min_episodes=1)
    assert "f_min_episodes" in joined(sql)
    assert params["f_min_episodes"] == 1


def test_every_endpoint_that_takes_a_cap_takes_a_floor():
    """A filter reachable on /recommend but not /random is a trap."""
    import inspect

    from api.routers import foryou, random_pick, recommend

    for module in (recommend, random_pick, foryou):
        source = inspect.getsource(module)
        assert source.count("max_episodes") == source.count("min_episodes"), module.__name__
        assert source.count("max_chapters") == source.count("min_chapters"), module.__name__


def test_the_ui_offers_both_bounds():
    from pathlib import Path

    web = Path(__file__).parent.parent / "web"
    html = (web / "index.html").read_text()
    app = (web / "app.js").read_text()
    assert 'id="minLen"' in html
    assert 'id="maxLen"' in html
    assert "params.min_episodes" in app
    assert "params.min_chapters" in app
    # And the value is remembered alongside the others.
    assert '"minLen"' in app
