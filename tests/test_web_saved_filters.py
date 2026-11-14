"""The web client remembers its filters, weights and switches per browser,
as the Android app does per phone. They used to reset on every reload."""

from pathlib import Path

APP_JS = (Path(__file__).resolve().parent.parent / "web/app.js").read_text()


def test_every_filter_control_is_remembered():
    saved = APP_JS.split("const SAVED_CONTROLS = [")[1].split("];")[0]
    for control in (
        "wSemantic",
        "wTaste",
        "adult",
        "country",
        "status",
        "format",
        "dubLang",
        "alExclude",
        "ytExclude",
        "keepPlanned",
        "crossMedia",
    ):
        assert f'"{control}"' in saved, control


def test_restored_before_the_first_query():
    restore = APP_JS.index("\nrestoreFilters();")
    assert restore < APP_JS.index("\nbindEvents();")
    assert restore < APP_JS.index("\nloadAccountSettings();")


def test_chips_and_late_dropdowns_are_kept():
    assert "saveFilters();" in APP_JS.split("function renderGenreChips()")[1][:200]
    assert "saveFilters();" in APP_JS.split("function renderTagChips()")[1][:200]
    assert "select.dataset.want" in APP_JS.split("async function loadDubLanguages")[1][:2000]
