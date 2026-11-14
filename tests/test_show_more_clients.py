"""Both clients page past the first 60 results with Show more. Search and
browse ask the server for the next page by offset; recommendations ask for a
longer list (the ranking is deterministic); For you, which draws its seeds at
random, fetches once and reveals the rest."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "web/app.js").read_text()
DISCOVER = (
    ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverFragment.kt"
).read_text()


def test_web_search_pages_by_offset():
    body = APP_JS.split("async function loadSearchPage(")[1].split("\n}\n")[0]
    assert "offset: String(offset)" in body
    assert "seq !== state.searchSeq" in body, "a late page is dropped"
    assert "data.results.length < Number(params.limit)" in body, "no button after the last page"


def test_web_clears_pages_with_the_results():
    clear = APP_JS.split("function clearResults()")[1].split("\n}\n")[0]
    assert "state.searchSeq += 1" in clear


def test_android_search_pages_by_offset():
    assert '"offset" to offset.toString()' in DISCOVER
    assert "ConcatAdapter(header, grid, more)" in DISCOVER


def test_android_recommendations_ask_for_a_longer_list():
    body = DISCOVER.split("private fun offerMoreRecommendations(")[1].split("\n    }\n")[0]
    assert "minOf(limit + PAGE, MAX_RECOMMENDATIONS)" in body
    assert "data.results.drop(grid.itemCount)" in body


def test_android_for_you_reveals_one_fetch():
    for_you = DISCOVER.split("private fun loadForYou()")[1].split("\n    }\n")[0]
    assert "renderRevealed {" in for_you
    assert "fetch(MAX_RECOMMENDATIONS)" in DISCOVER
