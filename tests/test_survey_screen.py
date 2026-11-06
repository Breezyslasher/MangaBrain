"""The screen survey reads content, never popularity."""

import inspect

from pipeline import survey_screen as survey


class FakeTmdb:
    def __init__(self, data):
        self.data = data

    def request(self, method, url, params=None, **_):
        return self.data


def test_a_tmdb_title_is_measured_by_overview_and_keywords():
    data = {
        "overview": "x" * 250,
        "genres": [{"name": "Animation"}, {"name": "Drama"}],
        "keywords": {"keywords": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
        "origin_country": ["JP"],
        "popularity": 999.0,
        "vote_count": 5000,
    }
    row = survey.tmdb_row(FakeTmdb(data), {}, "movie", 1)
    assert row == {"synopsis": 250, "genres": 2, "keywords": 3, "anime": True}


def test_tv_keywords_come_back_under_results():
    data = {"overview": "", "genres": [], "keywords": {"results": [{"name": "a"}]}}
    assert survey.tmdb_row(FakeTmdb(data), {}, "tv_series", 1)["keywords"] == 1


def test_summary_counts_usable_and_drops_anime_from_the_estimate():
    rows = [
        {"synopsis": 300, "genres": 2, "keywords": 5, "anime": False},
        {"synopsis": 300, "genres": 2, "keywords": 1, "anime": True},
        {"synopsis": 50, "genres": 1, "keywords": 0, "anime": False},
        {"synopsis": 0, "genres": 0, "keywords": 0, "anime": False},
    ]
    out = survey.summarise(rows, 1000, 200)
    assert out["usable_pct"] == 50.0
    assert out["japanese_animation_pct"] == 25.0
    assert out["usable_estimate"] == 250  # one usable non-anime title in four


def test_no_popularity_field_is_read():
    source = inspect.getsource(survey.tmdb_row) + inspect.getsource(survey.survey_tvmaze)
    for field in ("popularity", "vote_count", "vote_average", "rating", "weight"):
        assert f'"{field}"' not in source, field
