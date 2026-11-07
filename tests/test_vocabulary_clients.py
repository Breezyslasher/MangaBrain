"""Both clients ask for the genres and tags of the medium in view, and ask
again when it changes. They used to show AniList's genres on every tab and
suggest tags from the whole catalogue."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "web/app.js").read_text()
ANDROID = ROOT / "android/app/src/main/java/com/mangabrain/app"
FILTERS = (ANDROID / "ui/filters/FiltersFragment.kt").read_text()
API = (ANDROID / "data/api/MangaBrainApi.kt").read_text()


def test_web_fetches_both_lists_for_the_medium():
    assert 'api("/genres", { medium })' in APP_JS
    assert 'api("/tags", { medium })' in APP_JS
    set_medium = APP_JS.split("function setMedium(")[1].split("\n}")[0]
    assert "loadVocabularies()" in set_medium
    assert "for (const genre of GENRES)" not in APP_JS


def test_web_drops_a_genre_the_new_medium_lacks():
    body = APP_JS.split("async function loadVocabularies()")[1].split("\n}")[0]
    assert "delete state.genreState[name]" in body
    assert "state.medium !== medium" in body


def test_android_fetches_both_lists_for_the_medium():
    assert '@GET("genres")' in API and '@Query("medium") medium: String' in API
    assert "api.genres(medium)" in FILTERS
    assert "api.tags(medium)" in FILTERS
    assert "vocabularyMedium != medium" in FILTERS
    assert "for (genre in FilterState.GENRES)" not in FILTERS


def test_android_drops_a_genre_the_new_medium_lacks():
    assert "FilterState.genreState.keys.retainAll(list.toSet())" in FILTERS
    assert "if (medium != FilterState.medium) return@launch" in FILTERS
