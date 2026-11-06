"""TV from TVmaze: what is kept, how it maps, and what is never read."""

import inspect

from pipeline import sync_tvmaze

SUMMARY = "<p>A long enough <b>summary</b> &amp; more. " + "Plot goes on. " * 15 + "</p>"


def show(**over):
    data = {
        "id": 82,
        "name": "Game of Thrones",
        "type": "Scripted",
        "language": "English",
        "genres": ["Drama", "Adventure", "Fantasy"],
        "status": "Ended",
        "premiered": "2011-04-17",
        "network": {"country": {"code": "US"}},
        "image": {"medium": "m.jpg", "original": "o.jpg"},
        "summary": SUMMARY,
        "rating": {"average": 8.9},
        "weight": 99,
    }
    data.update(over)
    return data


def test_a_show_maps_onto_the_media_row():
    row = sync_tvmaze.row_for(show())
    assert row["source_id"] == "82" and row["source"] == "tvmaze"
    assert row["format"] == "SCRIPTED" and row["status"] == "FINISHED"
    assert row["genres"] == ["Drama", "Adventure", "Fantasy"]
    assert row["start_year"] == 2011 and row["country"] == "US"
    assert "<" not in row["description"] and "&amp;" not in row["description"]
    assert row["cover_large"] == "o.jpg"


def test_web_channel_country_counts_when_there_is_no_network():
    row = sync_tvmaze.row_for(show(network=None, webChannel={"country": {"code": "GB"}}))
    assert row["country"] == "GB"


def test_short_summaries_and_japanese_animation_are_kept_out():
    assert sync_tvmaze.row_for(show(summary="<p>Short.</p>")) is None
    anime = show(type="Animation", language="Japanese", network={"country": {"code": "JP"}})
    assert sync_tvmaze.row_for(anime) is None
    western = show(type="Animation", network={"country": {"code": "US"}})
    assert sync_tvmaze.row_for(western) is not None


def test_rating_and_weight_are_never_read():
    row = sync_tvmaze.row_for(show())
    assert not {"rating", "weight", "average_score", "popularity"} & set(row)
    code = inspect.getsource(sync_tvmaze.row_for)
    assert '"rating"' not in code and '"weight"' not in code


def test_a_short_index_never_removes_shows():
    """An outage that ends the index early must not empty the TV tab."""
    source = inspect.getsource(sync_tvmaze.sync)
    assert 'if tally["read"] >= MIN_INDEX:' in source
    assert sync_tvmaze.MIN_INDEX >= 10_000


def test_tv_is_an_installable_tvmaze_dataset_credited_to_tvmaze():
    from api.datasets import BY_KEY, dataset_for_medium
    from pipeline.dataset_install import INSTALLERS

    assert dataset_for_medium("tv").key == "tvmaze"
    assert "TVmaze" in BY_KEY["tvmaze"].description and "CC BY-SA" in BY_KEY["tvmaze"].description
    assert "tvmaze" in INSTALLERS


def test_the_nightly_pass_reads_tvmaze():
    from pathlib import Path

    nightly = (Path(__file__).resolve().parent.parent / "pipeline/nightly.py").read_text()
    assert "tvmaze_nightly()" in nightly
