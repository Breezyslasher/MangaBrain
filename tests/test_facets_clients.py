"""The format, origin and status pickers list what each medium's titles
carry, from /facets. They were AniList's lists typed into both clients, so
the Films tab offered Japan, Korea, China and Taiwan and nothing else."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "web/app.js").read_text()
HTML = (ROOT / "web/index.html").read_text()
ANDROID = ROOT / "android/app/src/main/java/com/mangabrain/app"
FILTERS = (ANDROID / "ui/filters/FiltersFragment.kt").read_text()
STATE = (ANDROID / "data/FilterState.kt").read_text()


def test_no_country_or_status_list_is_typed_in():
    assert 'value="JP"' not in HTML
    assert 'value="RELEASING"' not in HTML
    assert '"JP", "KR"' not in FILTERS
    assert "formatsForMedium" not in STATE + FILTERS


def test_both_clients_ask_for_the_medium_facets():
    assert 'api("/facets", { medium, adult })' in APP_JS
    assert "api.facets(medium, FilterState.adult)" in FILTERS


def test_statuses_are_worded_for_the_medium():
    for words in ('RELEASING: "Airing"', 'FINISHED: "Ended"', 'FINISHED: "Released"'):
        assert words in APP_JS
    assert '"tv" -> listOf("Ended", "Airing"' in STATE


def test_a_saved_choice_survives_the_lists_loading():
    assert "select.dataset.want" in APP_JS
    assert "if (facets != null) {" in FILTERS
